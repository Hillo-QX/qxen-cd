#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T357 — R3A checkpoint 选择脚本。

从 mlx_lm lora 训练日志解析每 100 iter 的 valid loss，
在已保存的 checkpoint 中选出 valid loss 最低的适配器权重，
输出其迭代号与路径，供 gate 评估使用。

用法:
  ./venv/bin/python scripts/select_best_checkpoint.py \
      --log logs/r3/r3a_train.log \
      --adapter-dir models/r3a \
      [--save-every 100] \
      [--min-iter 100] \
      [--json]

输出:
  --json: {"best_iter": N, "best_val_loss": X, "checkpoint_path": "models/r3a/0000N00_adapters.safetensors", "candidates": [...]}
  默认:  一行文本 best_iter 与 checkpoint 路径
"""
from __future__ import annotations
import argparse, glob, json, os, re, sys

VAL_LOSS_RE = re.compile(r"Iter (\d+): Val loss ([0-9.]+)")


def parse_log(log_path: str) -> list[tuple[int, float]]:
    """从日志解析 (iter, val_loss) 列表，按 iter 升序。"""
    if not os.path.isfile(log_path):
        raise FileNotFoundError(f"日志不存在: {log_path}")
    entries = []
    for line in open(log_path, "r", encoding="utf-8", errors="replace"):
        m = VAL_LOSS_RE.search(line)
        if m:
            entries.append((int(m.group(1)), float(m.group(2))))
    entries.sort()
    return entries


def list_checkpoints(adapter_dir: str, save_every: int) -> set[int]:
    """列出 adapter 目录中已保存的 checkpoint 迭代号（0000N00_adapters.safetensors）。

    注意: 文件名是 7 位补零的迭代号（如 0001200_adapters.safetensors），
    不能按 f"*{save_every}_adapters.safetensors" 匹配（*100_ 会漏掉
    iter 200/300/.../1200/1300 等所有不以 100 结尾的 checkpoint）。
    改为匹配全部 *_adapters.safetensors 后解析前导数字。
    """
    pat = os.path.join(adapter_dir, "*_adapters.safetensors")
    iters = set()
    for p in glob.glob(pat):
        base = os.path.basename(p)
        try:
            iters.add(int(base.split("_")[0]))
        except ValueError:
            continue
    return iters


def main() -> int:
    ap = argparse.ArgumentParser(description="从训练日志选择 valid loss 最低的 checkpoint")
    ap.add_argument("--log", required=True, help="mlx_lm lora 训练日志路径")
    ap.add_argument("--adapter-dir", required=True, help="adapter 输出目录（含 checkpoint）")
    ap.add_argument("--save-every", type=int, default=100, help="checkpoint 保存间隔")
    ap.add_argument("--min-iter", type=int, default=100,
                    help="跳过训练早期 checkpoint（默认 100 起）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    entries = parse_log(args.log)
    if not entries:
        print(f"[select_best] FAIL: 日志中未找到 Val loss 行: {args.log}", file=sys.stderr)
        return 1
    checkpoints = list_checkpoints(args.adapter_dir, args.save_every)

    candidates = [e for e in entries if e[0] >= args.min_iter and e[0] in checkpoints]
    if not candidates:
        print(f"[select_best] FAIL: 无可选 checkpoint（iter>={args.min_iter} 且已保存）。"
              f"已保存: {sorted(checkpoints)}", file=sys.stderr)
        return 1

    best_iter, best_val = min(candidates, key=lambda e: e[1])
    ckpt_path = os.path.join(args.adapter_dir, f"{best_iter:07d}_adapters.safetensors")

    if args.json:
        print(json.dumps({
            "best_iter": best_iter,
            "best_val_loss": best_val,
            "checkpoint_path": ckpt_path,
            "log": args.log,
            "adapter_dir": args.adapter_dir,
            "candidates": [{"iter": i, "val_loss": v} for i, v in candidates],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"best_iter={best_iter} best_val_loss={best_val} checkpoint={ckpt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
