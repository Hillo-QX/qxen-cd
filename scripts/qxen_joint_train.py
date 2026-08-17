#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qxen_joint_v1 首轮 capsule-only 训练启动（T001 训练前置检查 PASS 后）。

依据 skill §5.1/§5.2 + configs/qxen_joint_v1_train.yaml。
使用 mlx_lm.lora CLI（与 r3_split_train.py 同模式）。

用法：
  ./venv/bin/python scripts/qxen_joint_train.py --check-config   # 静态校验
  ./venv/bin/python scripts/qxen_joint_train.py                  # 前台训练
  ./venv/bin/python scripts/qxen_joint_train.py --daemon         # 后台训练(脱离进程树)

守护：
  - memory_monitor.sh <pid> 监控 wired ≤ 18GB，越线 SIGTERM
  - 日志 logs/qxen_joint_v1_train.log
  - pidfile logs/qxen_joint_v1_train.pid
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "configs/qxen_joint_v1_train.yaml"
LOG_PATH = ROOT / "logs/qxen_joint_v1_train.log"
PIDFILE = ROOT / "logs/qxen_joint_v1_train.pid"
MONITOR = ROOT / "scripts/memory_monitor.sh"


def load_cfg() -> dict:
    import yaml
    return yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))


def check_config() -> int:
    cfg = load_cfg()
    t, d, a = cfg["training"], cfg["data"], cfg["adapter"]
    data_dir = ROOT / d["dir"]
    train_f = data_dir / "train.jsonl"
    valid_f = data_dir / "valid.jsonl"
    checks = [
        ("model 存在", (ROOT / cfg["model"]).is_dir()),
        ("data 目录存在", data_dir.is_dir()),
        ("train.jsonl 存在", train_f.is_file()),
        ("valid.jsonl 存在", valid_f.is_file()),
        ("train 行数=800", sum(1 for _ in open(train_f)) == d["train_rows"]),
        ("valid 行数=200", sum(1 for _ in open(valid_f)) == d["valid_rows"]),
        ("max_seq_length 安全范围", 256 <= t["max_seq_length"] <= 512),
        ("LoRA rank/layers 安全范围", a["rank"] in (4, 8) and 1 <= a["num_layers"] <= 4),
        ("learning_rate=4e-6", t["learning_rate"] == 4.0e-06),
        ("adapter 输出未存在", not (ROOT / a["output"]).exists() or (ROOT / a["output"]).is_dir()),
        ("memory_monitor 存在", MONITOR.is_file()),
    ]
    ok = True
    for label, good in checks:
        ok &= good
        print(f"  [{'OK' if good else 'FAIL'}] {label}")
    print(f"[qxen_joint_v1-check] {'ALL PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def build_cmd(cfg: dict) -> list[str]:
    t, d, a = cfg["training"], cfg["data"], cfg["adapter"]
    cmd = [
        sys.executable, "-m", "mlx_lm", "lora",
        "--model", cfg["model"], "--train",
        "--data", str(ROOT / d["dir"]),
        "--fine-tune-type", a["fine_tune_type"],
        "--num-layers", str(a["num_layers"]),
        "--batch-size", str(t["batch_size"]),
        "--iters", str(t["iters"]),
        "--learning-rate", f"{t['learning_rate']:.1e}",
        "--adapter-path", str(ROOT / a["output"]),
        "--save-every", str(t["save_every"]),
        "--max-seq-length", str(t["max_seq_length"]),
        "--grad-checkpoint",
        "--grad-accumulation-steps", str(t["grad_accumulation_steps"]),
        "--seed", str(t["seed"]),
        "--steps-per-report", str(t["steps_per_report"]),
        "--val-batches", str(t["val_batches"]),
        "--steps-per-eval", str(t["steps_per_eval"]),
    ]
    if t.get("clear_cache_threshold"):
        cmd += ["--clear-cache-threshold", str(t["clear_cache_threshold"])]
    return cmd


def train(daemon: bool = False) -> int:
    cfg = load_cfg()
    cmd = build_cmd(cfg)
    print(f"[qxen_joint_v1-train] 命令:\n  {' '.join(cmd)}")
    os.makedirs(LOG_PATH.parent, exist_ok=True)
    if daemon:
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        with open(PIDFILE, "w", encoding="utf-8") as pf:
            pf.write(str(proc.pid))
        # 启动 memory_monitor 守护训练进程
        mon_proc = subprocess.Popen(
            ["bash", str(MONITOR), str(proc.pid), str(LOG_PATH)],
            start_new_session=True,
        )
        print(f"[qxen_joint_v1-train] daemon pid={proc.pid} | mon pid={mon_proc.pid}")
        print(f"  log={LOG_PATH} | pidfile={PIDFILE}")
        print("  用 CheckBackgroundJob 或 tail 日志看进度；每次 checkpoint 只汇报增量")
        return 0
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    print(f"[qxen_joint_v1-train] 退出码 {proc.returncode} | log={LOG_PATH}")
    return proc.returncode


def main() -> int:
    global CFG_PATH, LOG_PATH, PIDFILE
    ap = argparse.ArgumentParser(description="qxen_joint_v1 capsule-only 训练")
    ap.add_argument("--check-config", action="store_true", help="仅静态校验")
    ap.add_argument("--daemon", action="store_true", help="后台训练")
    ap.add_argument("--config", default=str(CFG_PATH), help="训练配置路径")
    ap.add_argument("--log", default=str(LOG_PATH), help="训练日志路径")
    ap.add_argument("--pidfile", default=str(PIDFILE), help="训练 PID 文件路径")
    args = ap.parse_args()
    CFG_PATH = Path(args.config).resolve()
    LOG_PATH = Path(args.log).resolve()
    PIDFILE = Path(args.pidfile).resolve()
    if args.check_config:
        return check_config()
    if check_config() != 0:
        print("FAIL: 配置校验未过，不启动训练")
        return 1
    return train(daemon=args.daemon)


if __name__ == "__main__":
    sys.exit(main())
