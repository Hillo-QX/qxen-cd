#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""working loop 状态持久化脚本 —— 让 codex working loop 可恢复、可周期触发。

职责（确定性数字提取，shell/python 本地完成，不调 qwen、不调远程）：
    1. 自动发现当前活跃训练 adapter（从 mlx_lm 进程命令行解析）或显式指定
    2. 读 configs/{adapter}.yaml 取 target_iters + gate 阈值
    3. 读 logs/r3/{adapter}_train.log 取最新 iter / train / val / 峰值内存
    4. 检查训练进程存活
    5. 判定 phase（training / done / failed / idle）
    6. 写 调度状态/working_loop_state.json（供 codex automation / watch 恢复上下文）
    7. stdout 输出单行 JSON 摘要（≤ 少量字段，供 shell 直接消费）

用法：
    scripts/update_loop_state.py                 # 自动发现活跃 adapter
    scripts/update_loop_state.py --adapter r3a_hard_pure_v1
    scripts/update_loop_state.py --adapter r3a_v2 --print-state   # 打印 state 全文

phase 判定：
    training : 进程存活 且 iter < iters（正常推进中）
    done     : 进程不在 且 iter >= iters 且日志含 Saved final weights（待 Gate）
    failed   : 进程不在 且 (iter < iters 或 无 Saved final weights)
    idle     : 无训练进程 且 无日志（还没启动）
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "调度状态" / "working_loop_state.json"

# adapter -> config 映射（与 scripts/r3_split_train.py 保持一致）
ADAPTERS = {
    "r3a": "configs/r3a.yaml",
    "r3a_v2": "configs/r3a_v2.yaml",
    "r3a_hard_v1": "configs/r3a_hard_v1.yaml",
    "r3a_hard_v1_seq256": "configs/r3a_hard_v1_seq256.yaml",
    "r3a_hard_pure_v1": "configs/r3a_hard_pure_v1.yaml",
    "r3a_structured_v1": "configs/r3a_structured_v1.yaml",
    "r3a_structured_v2": "configs/r3a_structured_v2.yaml",
    "r3a_structured_v3": "configs/r3a_structured_v3.yaml",
    "r3b": "configs/r3b.yaml",
    "r3c": "configs/r3c.yaml",
}

PIPELINE_STAGE_ADAPTERS = {
    "r3a": "r3a_structured_v1",
    "r3b": "r3b",
    "r3c": "r3c",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def discover_adapter() -> str | None:
    """从 mlx_lm 训练进程命令行解析 --adapter-path 的 basename。"""
    try:
        out = subprocess.run(
            ["pgrep", "-fl", "mlx_lm lora"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    for line in out.splitlines():
        # 正则抓 --adapter-path 后的值
        m = re.search(r"--adapter-path\s+(\S+)", line)
        if m:
            return Path(m.group(1)).name
    return None


def discover_iters() -> int | None:
    """从 mlx_lm 训练进程命令行解析 --iters（resume 训练的目标 iter 与 config 不同）。"""
    try:
        out = subprocess.run(
            ["pgrep", "-fl", "mlx_lm lora"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    for line in out.splitlines():
        m = re.search(r"--iters\s+(\d+)", line)
        if m:
            return int(m.group(1))
    return None


def load_cfg(adapter: str) -> dict | None:
    """读 config；对 resume adapter（名带 _resume\d+ 后缀）fallback 到 base config。"""
    import yaml
    # 1) 精确匹配
    cfg_path = ROOT / ADAPTERS.get(adapter, "")
    if not cfg_path.is_file():
        # 2) resume fallback：r3a_hard_pure_v1_resume1000 -> r3a_hard_pure_v1
        base = re.sub(r"_resume\d+$", "", adapter)
        cfg_path = ROOT / ADAPTERS.get(base, "")
    if not cfg_path.is_file():
        return None
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        cfg.setdefault("_base_adapter", adapter)
        return cfg
    except Exception:
        return None


def read_progress(adapter: str) -> dict:
    log = ROOT / "logs" / "r3" / f"{adapter}_train.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""

    def last(pattern: str) -> str:
        import re
        found = re.findall(pattern, text)
        return found[-1] if found else ""

    last_iter = last(r"Iter (\d+): Train loss")
    train_loss = last(r"Iter \d+: Train loss ([\d.]+)")
    val_loss = last(r"Iter \d+: Val loss ([\d.]+)")
    peak_mem = last(r"Iter \d+: Train loss .*Peak mem ([\d.]+) GB")

    # 进程存活判断
    try:
        proc_alive = subprocess.run(
            ["pgrep", "-f", f"mlx_lm lora.*{adapter}"],
            capture_output=True, timeout=5,
        ).returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        proc_alive = False

    return {
        "last_iter": int(last_iter) if last_iter else 0,
        "train_loss": train_loss or None,
        "val_loss": val_loss or None,
        "peak_mem_gb": peak_mem or None,
        "proc_alive": proc_alive,
        "saved_final": "Saved final weights" in text,
        "log_exists": log.is_file(),
    }


def compute_phase(p: dict, target_iters: int) -> str:
    if not p["log_exists"] and not p["proc_alive"]:
        return "idle"
    if p["proc_alive"]:
        return "training"
    if p["last_iter"] >= target_iters and p["saved_final"]:
        return "done"
    return "failed"


def build_state(adapter: str, cfg: dict, p: dict, target_override: int | None = None) -> dict:
    target = target_override or int(cfg["training"]["iters"])
    phase = compute_phase(p, target)
    prior = {}
    if STATE_PATH.is_file():
        try:
            loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if loaded.get("adapter") == adapter:
                prior = loaded
        except (OSError, json.JSONDecodeError):
            prior = {}
    state = {
        "loop": "qxen-working-loop",
        "adapter": adapter,
        "phase": phase,
        "target_iters": target,
        "last_iter": p["last_iter"],
        "train_loss": p["train_loss"],
        "val_loss": p["val_loss"],
        "peak_mem_gb": p["peak_mem_gb"],
        "proc_alive": p["proc_alive"],
        "saved_final": p["saved_final"],
        "gate_thresholds": cfg.get("gate", {}),
        # 决策路由字段：由主 Agent / 专家咨询流程消费，默认不挂起 loop。
        "expert_pending": bool(prior.get("expert_pending", False)),
        "decision_route": prior.get("decision_route", "auto_continue"),
        "repair_attempt": int(prior.get("repair_attempt", 0) or 0),
        "expert_consult": prior.get("expert_consult"),
        "updated_at": now_iso(),
    }
    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=None, help="显式指定 adapter（默认自动发现）")
    ap.add_argument("--print-state", action="store_true", help="打印完整 state JSON 后退出")
    args = ap.parse_args()

    adapter = args.adapter or discover_adapter()
    if not adapter:
        # R3 pipeline 状态优先于旧 working state，避免完成后回落废弃 adapter。
        pipeline_path = ROOT / "调度状态" / "r3_pipeline_state.json"
        if pipeline_path.is_file():
            try:
                ps = json.loads(pipeline_path.read_text(encoding="utf-8"))
                stage = ps.get("current_stage")
                if ps.get("loop") == "qxen-r3-pipeline" and stage in PIPELINE_STAGE_ADAPTERS:
                    adapter = PIPELINE_STAGE_ADAPTERS[stage]
            except (OSError, json.JSONDecodeError):
                pass
    if not adapter:
        # 无活跃训练，回落到最近一次 state 记录的 adapter，否则 idle
        if STATE_PATH.is_file():
            try:
                adapter = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("adapter")
            except Exception:
                adapter = None
    if not adapter:
        state = {
            "loop": "qxen-working-loop", "adapter": None, "phase": "idle",
            "updated_at": now_iso(),
        }
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False))
        return 0

    cfg = load_cfg(adapter)
    if cfg is None:
        print(json.dumps({"status": "ERROR", "reason": f"no config for {adapter}"}, ensure_ascii=False))
        return 1

    p = read_progress(adapter)
    state = build_state(adapter, cfg, p, target_override=discover_iters())

    if args.print_state:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # stdout 摘要（精简，供 shell 消费）
    summary = {
        "status": "OK",
        "adapter": adapter,
        "phase": state["phase"],
        "iter": f"{p['last_iter']}/{state['target_iters']}",
        "train_loss": p["train_loss"],
        "val_loss": p["val_loss"],
        "peak_mem_gb": p["peak_mem_gb"],
        "proc_alive": p["proc_alive"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
