#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T052 — 混合训练集构建：T049 balanced 78 条 + HQ1700 子集 400 条。

Dispatcher 决策（T051 FAIL 后）：选项 C 混合训练。
  - HQ1700 子集: 从 1700 条中抽 400 条，7 标签均衡（每类约 57 条）；
  - T049 balanced: data/distill_ctxA/train_balanced.jsonl（78 条，DROP 24.4%）；
  - 合并为 data/mixed_train/train.jsonl。

保留 T049 balanced 作为回退基线（不覆盖）。
"""
from __future__ import annotations

import json
import os
import random
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HQ = os.path.join(PROJECT_ROOT, "data", "hq1700", "train.json")
BALANCED = os.path.join(PROJECT_ROOT, "data", "distill_ctxA", "train_balanced.jsonl")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "mixed_train")

HQ_SAMPLE = 400
SEED = 42
VALID = {"PIN", "KEEP", "VERBATIM", "COMPRESS", "DROP", "REFRESH", "RETRIEVE"}


def main() -> int:
    # HQ1700 抽样子集（7 标签均衡）
    hq = json.load(open(HQ, encoding="utf-8"))
    rng = random.Random(SEED)
    by_label: dict[str, list[dict]] = {}
    for r in hq:
        by_label.setdefault(r["completion"], []).append(r)
    per_label = HQ_SAMPLE // 7
    hq_sample = []
    for lbl in sorted(by_label):
        pool = by_label[lbl]
        rng.shuffle(pool)
        take = min(per_label, len(pool))
        hq_sample.extend(pool[:take])
        print(f"HQ1700 {lbl}: 取 {take}/{len(pool)}")

    # T049 balanced 78 条
    balanced = []
    with open(BALANCED, encoding="utf-8") as f:
        balanced = [json.loads(l) for l in f if l.strip()]
    print(f"T049 balanced: {len(balanced)} 条")

    # 合并（HQ1700 样本加前缀 id 避免冲突）
    mixed = []
    for i, r in enumerate(hq_sample):
        mixed.append({"prompt": r["prompt"], "completion": r["completion"]})
    for i, r in enumerate(balanced):
        mixed.append({"prompt": r["prompt"], "completion": r["completion"]})
    rng.shuffle(mixed)

    # 统计
    total = len(mixed)
    by_dec = Counter(r["completion"] for r in mixed)
    print(f"\n混合集总数: {total}")
    for lbl in sorted(VALID):
        print(f"  {lbl}: {by_dec.get(lbl, 0)} ({100*by_dec.get(lbl,0)/total:.1f}%)")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "train.jsonl"), "w", encoding="utf-8") as f:
        for r in mixed:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    manifest = {
        "task_id": "T052",
        "purpose": "混合训练: HQ1700 子集(400, 7类均衡) + T049 balanced(78)",
        "hq_sample": len(hq_sample),
        "balanced": len(balanced),
        "total": total,
        "by_decision": dict(sorted(by_dec.items())),
        "seed": SEED,
        "out": os.path.join(OUT_DIR, "train.jsonl"),
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n写出: {os.path.join(OUT_DIR, 'train.jsonl')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
