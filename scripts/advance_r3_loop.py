#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3 阶段编排器：done -> Gate -> 下一阶段训练。

每次调用只执行一个有界转换，状态原子落盘，重复 done 事件幂等 no-op。
不会跳过 Gate，也不会在训练进程存在时加载模型评估。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "调度状态" / "r3_pipeline_state.json"
LOCK_PATH = ROOT / "调度状态" / "r3_pipeline.lock"
PYTHON = ROOT / "venv" / "bin" / "python"

STAGES = ("r3a", "r3b", "r3c")
STAGE_INFO = {
    "r3a": {
        "adapter": "r3a_structured_v2",
        "adapter_dir": "models/r3a_structured_v2",
        "train_adapter": "r3a_structured_v2",
        "structured": True,
        "train_log": "logs/r3/r3a_structured_v2_train.log",
    },
    "r3b": {
        "adapter": "r3b",
        "adapter_dir": "models/r3b",
        "train_adapter": "r3b",
        "structured": False,
        "train_log": "logs/r3/r3b_train.log",
    },
    "r3c": {
        "adapter": "r3c",
        "adapter_dir": "models/r3c",
        "train_adapter": "r3c",
        "structured": False,
        "train_log": "logs/r3/r3c_train.log",
    },
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    if STATE_PATH.is_file():
        try:
            s = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if s.get("current_stage") in STAGES:
                return s
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "loop": "qxen-r3-pipeline",
        "current_stage": "r3a",
        "next_action": "gate",
        "stage_status": "done",
        "last_gate": None,
        "gate_report": None,
        "expert_pending": False,
        "decision_route": "auto_continue",
        "updated_at": now(),
    }


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now()
    fd, tmp = tempfile.mkstemp(prefix="r3_pipeline_", suffix=".json", dir=STATE_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def mlx_training_alive() -> bool:
    p = subprocess.run(["pgrep", "-f", "mlx_lm lora"], capture_output=True, text=True)
    return p.returncode == 0


def stage_done(stage: str) -> bool:
    info = STAGE_INFO[stage]
    if stage == "r3a":
        log = ROOT / info["train_log"]
        return log.is_file() and "Saved final weights" in log.read_text(encoding="utf-8", errors="replace")
    log = ROOT / info["train_log"]
    return log.is_file() and "Saved final weights" in log.read_text(encoding="utf-8", errors="replace")


def report_verdict(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return (d.get("gate") or {}).get("verdict")
    except (OSError, json.JSONDecodeError):
        return None


def gate_report_path(stage: str) -> Path:
    info = STAGE_INFO[stage]
    suffix = "_structured_v1" if stage == "r3a" else ""
    return ROOT / "reports" / "r3" / f"{stage}{suffix}_gate_eval.json"


def run_gate(stage: str, report: Path) -> int:
    info = STAGE_INFO[stage]
    if mlx_training_alive():
        print(json.dumps({"status": "WAIT", "reason": "training_alive", "stage": stage}, ensure_ascii=False))
        return 2
    cmd = [str(PYTHON), "scripts/r3_gate_eval.py", "--stage", stage,
           "--runs", stage, "--adapter-dir", info["adapter_dir"], "--out", str(report)]
    if info["structured"]:
        cmd.append("--structured")
    print(f"[r3-pipeline] Gate: {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


def launch_next(stage: str) -> int:
    adapter = STAGE_INFO[stage]["train_adapter"]
    if not adapter:
        return 1
    if mlx_training_alive():
        return 2
    cmd = [str(PYTHON), "scripts/r3_split_train.py", "--adapter", adapter, "--daemon"]
    print(f"[r3-pipeline] 启动 {stage}: {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


def step(state: dict, dry_run: bool) -> dict:
    stage = state["current_stage"]
    info = STAGE_INFO[stage]
    action = state.get("next_action", "gate")
    report = gate_report_path(stage)
    existing = report_verdict(report)

    if action == "gate":
        if existing in {"PASS", "FAIL"}:
            verdict = existing
        elif not stage_done(stage):
            return {"status": "WAIT", "stage": stage, "next_action": "train", "reason": "training_not_done"}
        elif dry_run:
            return {"status": "DRY_RUN", "stage": stage, "next_action": "gate", "would": "run_gate"}
        else:
            rc = run_gate(stage, report)
            if rc == 2:
                return {"status": "WAIT", "stage": stage, "next_action": "gate", "reason": "training_alive"}
            verdict = report_verdict(report) or ("FAIL" if rc else "PASS")
        state["last_gate"] = verdict
        state["gate_report"] = str(report.relative_to(ROOT))
        if verdict != "PASS":
            state.update({"stage_status": "gate_fail", "next_action": "diagnose",
                          "expert_pending": False, "decision_route": "gpt_auto_continue"})
            return {"status": "GATE_FAIL", "stage": stage, "report": str(report),
                    "next_action": "diagnose", "expert_optional": True}
        if stage == "r3c":
            state.update({"stage_status": "done", "next_action": "global_done", "expert_pending": False})
            return {"status": "DONE", "stage": stage, "report": str(report)}
        state.update({"stage_status": "gate_pass", "next_action": "launch_next", "expert_pending": False})
        return {"status": "GATE_PASS", "stage": stage, "next_action": "launch_next", "report": str(report)}

    if action == "launch_next":
        next_stage = STAGES[STAGES.index(stage) + 1]
        if dry_run:
            return {"status": "DRY_RUN", "stage": stage, "next_stage": next_stage, "would": "launch_train"}
        rc = launch_next(next_stage)
        if rc == 2:
            return {"status": "WAIT", "stage": stage, "reason": "training_alive"}
        if rc != 0:
            state.update({"stage_status": "launch_failed", "next_action": "diagnose",
                          "expert_pending": False, "decision_route": "gpt_auto_continue"})
            return {"status": "LAUNCH_FAIL", "stage": next_stage,
                    "next_action": "diagnose", "expert_optional": True}
        state.update({"current_stage": next_stage, "stage_status": "training",
                      "next_action": "gate", "last_gate": None, "gate_report": None})
        return {"status": "TRAINING_STARTED", "stage": next_stage, "next_action": "gate"}

    if action == "global_done":
        return {"status": "DONE", "stage": stage, "next_action": "global_done"}
    return {"status": "WAIT", "stage": stage, "next_action": action,
            "reason": "gpt_diagnosis_pending", "expert_optional": True}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--auto", action="store_true", help="执行一个有界推进动作")
    args = ap.parse_args()
    with LOCK_PATH.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "WAIT", "reason": "pipeline_locked"}, ensure_ascii=False))
            return 2
        state = load_state()
        result = step(state, dry_run=args.dry_run or not args.auto)
        if args.auto and result["status"] not in {"WAIT", "DRY_RUN"}:
            save_state(state)
        elif args.auto and result["status"] == "GATE_FAIL":
            save_state(state)
        print(json.dumps({**result, "state": state}, ensure_ascii=False))
        return 0 if result["status"] in {"WAIT", "DRY_RUN", "GATE_PASS", "TRAINING_STARTED", "DONE"} else 1


if __name__ == "__main__":
    sys.exit(main())
