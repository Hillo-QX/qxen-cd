#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T049 — 平衡 ctxA 训练数据：将 DROP 类占比降至 ≤25%（欠采样），保持其他 6 类全部保留。

输入: outputs/context_decision_training/train.jsonl（108 条, {prompt,completion} chat 格式）
输出: data/distill_ctxA/train_balanced.jsonl（{prompt,completion}, DROP ≤25%）
      data/distill_ctxA/train_balanced_manifest.json（分布报告）

设计要点：
  - 只欠采样 DROP，其他标签全部保留（保持相对比例，任务要求）；
  - seed=42 固定，保证可复现；
  - 不修改任何原始文件（train.jsonl 保留不动）。
"""
from __future__ import annotations

import json
import os
import random

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(PROJECT_ROOT, "outputs", "context_decision_training", "train.jsonl")
OUT = os.path.join(PROJECT_ROOT, "data", "distill_ctxA", "train_balanced.jsonl")
MANIFEST = os.path.join(PROJECT_ROOT, "data", "distill_ctxA", "train_balanced_manifest.json")

MAX_DROP_RATIO = 0.25
SEED = 42


def main() -> int:
    with open(SRC, encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["completion"], []).append(r)

    total = len(rows)
    dist = {k: len(v) for k, v in sorted(groups.items())}
    print(f"原始样本: {total}")
    for k, n in dist.items():
        print(f"  {k}: {n} ({100*n/total:.1f}%)")

    # 目标：DROP 占比 ≤25%，其余标签全部保留。
    # DROP_max = floor(0.25 * (非DROP数 + DROP_max)) => DROP_max <= 非DROP数/3
    non_drop = sum(n for k, n in dist.items() if k != "DROP")
    drop_max = int(non_drop / (1.0 / MAX_DROP_RATIO - 1.0))  # floor
    print(f"非 DROP 样本: {non_drop} -> DROP 上限: {drop_max} (占比 {drop_max/(non_drop+drop_max):.1%})")

    rng = random.Random(SEED)
    drop_rows = groups.get("DROP", [])
    if len(drop_rows) > drop_max:
        rng.shuffle(drop_rows)
        groups["DROP"] = drop_rows[:drop_max]

    balanced: list[dict] = []
    for k in sorted(groups):
        balanced.extend(groups[k])
    rng.shuffle(balanced)  # 混合顺序，避免同类连续

    new_total = len(balanced)
    new_dist = {k: sum(1 for r in balanced if r["completion"] == k) for k in sorted(groups)}
    drop_ratio = new_dist.get("DROP", 0) / new_total

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        for r in balanced:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    manifest = {
        "task_id": "T049",
        "purpose": "修正 DROP 过度占比（45.4% -> <=25%），欠采样 DROP，其余标签全保留",
        "src": SRC,
        "out": OUT,
        "seed": SEED,
        "original": dist,
        "balanced": new_dist,
        "total_original": total,
        "total_balanced": new_total,
        "drop_ratio_balanced": round(drop_ratio, 4),
        "drop_ratio_target": MAX_DROP_RATIO,
    }
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(f"\n平衡后: {new_total} 条")
    for k, n in sorted(new_dist.items()):
        print(f"  {k}: {n} ({100*n/new_total:.1f}%)")
    print(f"DROP 占比: {drop_ratio:.1%} (目标 ≤{MAX_DROP_RATIO:.0%})")
    print(f"写出: {OUT}")
    print(f"manifest: {MANIFEST}")
    ok = drop_ratio <= MAX_DROP_RATIO + 1e-9
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
