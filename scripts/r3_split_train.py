#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T355 — R3A/R3B/R3C 拆分训练脚本（pipeline 构建，本脚本可运行训练）。

用户决策(2026-08-13):
  - R3A status / R3B authority / R3C conflict 三个独立 adapter
  - 从干净 Base 重训（resume_adapter_file=null）
  - valid checkpoint 选择（不直接冻结最终权重）
  - 每个 adapter 单独过 Gate 后再组合

数据: data/r3/staging/{r3a,r3b,r3c}/{train,valid}.jsonl (T355 已生成, 平衡)
配置: configs/r3{a,b,c}.yaml
输出: models/r3a/, models/r3b/, models/r3c/ (含 per-iter checkpoints + best)

用法:
  ./venv/bin/python scripts/r3_split_train.py --adapter r3a --check-config   # 静态校验
  ./venv/bin/python scripts/r3_split_train.py --adapter r3a                  # 训练
"""
from __future__ import annotations
import argparse, glob, json, os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

ADAPTERS = {"r3a": "configs/r3a.yaml", "r3a_v2": "configs/r3a_v2.yaml",
            "r3a_hard_v1": "configs/r3a_hard_v1.yaml",
            "r3a_hard_v1_seq256": "configs/r3a_hard_v1_seq256.yaml",
            "r3a_hard_pure_v1": "configs/r3a_hard_pure_v1.yaml",
            "r3a_structured_v2": "configs/r3a_structured_v2.yaml",
            "r3a_structured_v3": "configs/r3a_structured_v3.yaml",
            "r3a_structured_v3_resume": "configs/r3a_structured_v3_resume.yaml",
            "r3a_cot_v4": "configs/r3a_cot_v4.yaml",
            "r3a_cot_v5": "configs/r3a_cot_v5.yaml",
            "r3b": "configs/r3b.yaml", "r3c": "configs/r3c.yaml"}

def load_cfg(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def check_config(name):
    cfg = load_cfg(ADAPTERS[name])
    t, d, a, m = cfg["training"], cfg["data"], cfg["adapter"], cfg["model"]
    ok = True
    train_path = f"{d['dir']}/train.jsonl"
    train_rows = sum(1 for line in open(train_path, encoding="utf-8") if line.strip())
    print(f"[{name}-check] 配置: {ADAPTERS[name]}")
    # 数据存在 + 条数
    for split, expect in (("train", d["train_rows"]), ("valid", d["valid_rows"])):
        p = f"{d['dir']}/{split}.jsonl"
        n = sum(1 for l in open(p, encoding="utf-8") if l.strip()) if os.path.exists(p) else -1
        good = n == expect
        ok &= good
        print(f"  [{'OK' if good else 'FAIL'}] {p}: {n} rows (期望 {expect})")
    # 超参
    checks = [
        ("rank==8", a["rank"] == 8),
        ("num_layers in [2,4]", a["num_layers"] in (2, 4)),
        ("lr==4e-6", t["learning_rate"] == 4e-6),
        ("epochs==1", t["epochs"] == 1),
        (f"iters=={train_rows}(冷启) 或 iters<={train_rows}(resume剩余)", t["iters"] == train_rows or (t.get("resume_adapter_file") is not None and t["iters"] <= train_rows)),
        (f"max_seq_length in [256,512,768]", t["max_seq_length"] in (256, 384, 448, 512, 768)),
        ("val_batches>0", t["val_batches"] > 0),
        ("save_every 100-200", 100 <= t["save_every"] <= 200),
        ("resume==null 或 resume 文件存在", t["resume_adapter_file"] is None or os.path.exists(t["resume_adapter_file"])),
        ("output 匹配 name 或指向既有 adapter 目录", a["output"] == f"models/{name}" or os.path.isdir(a["output"])),
    ]
    for label, good in checks:
        ok &= good
        print(f"  [{'OK' if good else 'FAIL'}] {label}")
    print(f"[{name}-check] {'ALL PASS' if ok else 'FAIL'}")
    return 0 if ok else 1

def train(name, daemon=False):
    cfg = load_cfg(ADAPTERS[name])
    t, d, a = cfg["training"], cfg["data"], cfg["adapter"]
    outdir = a["output"]
    cmd = [
        sys.executable, "-m", "mlx_lm", "lora",
        "--model", cfg["model"], "--train",
        "--data", d["dir"],
        "--fine-tune-type", a["fine_tune_type"],
        "--num-layers", str(a["num_layers"]),
        "--batch-size", str(t["batch_size"]),
        "--iters", str(t["iters"]),
        "--learning-rate", f"{t['learning_rate']:.1e}",
        "--adapter-path", outdir,
        "--save-every", str(t["save_every"]),
        "--max-seq-length", str(t["max_seq_length"]),
        "--grad-checkpoint",
        "--grad-accumulation-steps", str(t["grad_accumulation_steps"]),
        "--seed", str(t["seed"]),
        "--steps-per-report", str(t["steps_per_report"]),
        "--val-batches", str(t["val_batches"]),
        "--steps-per-eval", str(t["steps_per_eval"]),
    ]
    if t.get("resume_adapter_file"):
        cmd += ["--resume-adapter-file", t["resume_adapter_file"]]
    print(f"[{name}-train] 命令:\n  {' '.join(cmd)}")
    log = f"logs/r3/{name}_train.log"
    os.makedirs(os.path.dirname(log), exist_ok=True)
    if daemon:
        pidfile = f"logs/r3/{name}_train.pid"
        with open(log, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(
                cmd, stdout=f, stderr=subprocess.STDOUT,
                start_new_session=True,  # 等价 setsid：脱离 codex 进程树，codex 退出不带走训练
            )
        with open(pidfile, "w", encoding="utf-8") as pf:
            pf.write(str(proc.pid))
        print(f"[{name}-train] daemon 启动 pid={proc.pid} | log={log} | pidfile={pidfile}")
        print(f"[{name}-train] 训练已脱离本进程树；用 scripts/update_loop_state.py 轮询进度")
        return 0
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    print(f"[{name}-train] 退出码 {proc.returncode} | 耗时 {time.time()-t0:.0f}s | log={log}")
    return proc.returncode

def main():
    ap = argparse.ArgumentParser(description="R3A/R3B/R3C 拆分训练")
    ap.add_argument("--adapter", required=True, choices=list(ADAPTERS))
    ap.add_argument("--check-config", action="store_true", help="仅静态校验")
    ap.add_argument("--daemon", action="store_true", help="nohup 后台启动，脱离 codex 进程树")
    args = ap.parse_args()
    if args.check_config:
        return check_config(args.adapter)
    # 训练前先校验
    if check_config(args.adapter) != 0:
        print(f"[{args.adapter}] FAIL: 配置校验未过, 不启动训练")
        return 1
    return train(args.adapter, daemon=args.daemon)

if __name__ == "__main__":
    sys.exit(main())
