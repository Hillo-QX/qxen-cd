#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T054 Phase B 修复轮 — 训练数据重采样：
  Dispatcher 决策：训练分布向评估集倾斜（KEEP 主导 62%）。
  目标：KEEP=126 (60%)，其余 6 类各 14 (40% 均分) → 210 条。

  数据源：全部从 HQ1700 未训练池抽取（排除 mixed used_fps 与 eval_extended 指纹），
          KEEP 126 条 + 其余 6 类各 14 条。
          不使用 phaseB/train.jsonl（内含 mixed 已训练样本，会重复训练）。

  防泄漏：指纹 = 完整 prompt；dup==0 且 leak==0 才成功。
"""
from __future__ import annotations

import json
import os
import random
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHASEB_TRAIN = os.path.join(PROJECT_ROOT, "data", "phaseB", "train.jsonl")
MIXED = os.path.join(PROJECT_ROOT, "data", "mixed_train", "train.jsonl")
EVAL_EXT = os.path.join(PROJECT_ROOT, "data", "phaseB", "eval_extended.jsonl")
HQ = os.path.join(PROJECT_ROOT, "data", "hq1700", "train.json")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "phaseB", "train_resampled")

SEED = 42
TARGET_TOTAL = 210
KEEP_TARGET = 126          # 60%
OTHER_LABELS = ["COMPRESS", "DROP", "PIN", "REFRESH", "RETRIEVE", "VERBATIM"]
OTHER_PER = (TARGET_TOTAL - KEEP_TARGET) // len(OTHER_LABELS)   # 14
LABELS = sorted(["KEEP"] + OTHER_LABELS)


def fingerprint(r: dict) -> str:
    return r.get("prompt", "")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = random.Random(SEED)

    phaseb = [json.loads(l) for l in open(PHASEB_TRAIN, encoding="utf-8") if l.strip()]
    mixed = [json.loads(l) for l in open(MIXED, encoding="utf-8") if l.strip()]
    eval_ext = [json.loads(l) for l in open(EVAL_EXT, encoding="utf-8") if l.strip()]
    hq = json.load(open(HQ, encoding="utf-8"))

    used_fps = {fingerprint(r) for r in mixed}          # T052 已用
    eval_fps = {fingerprint(r) for r in eval_ext}       # 评估集指纹
    all_fps = used_fps | eval_fps                       # 禁止混入

    # HQ1700 未训练池，按标签分组
    by_label: dict[str, list[dict]] = {}
    for r in hq:
        if fingerprint(r) in all_fps:
            continue
        by_label.setdefault(r["completion"], []).append(r)

    chosen: list[dict] = []
    # 1) KEEP：抽 KEEP_TARGET 条
    keep_pool = list(by_label.get("KEEP", []))
    rng.shuffle(keep_pool)
    chosen.extend(keep_pool[:KEEP_TARGET])
    # 2) 其余 6 类各 OTHER_PER 条
    for lbl in OTHER_LABELS:
        pool = list(by_label.get(lbl, []))
        rng.shuffle(pool)
        chosen.extend(pool[:OTHER_PER])

    # 去重 / 泄漏检查
    fps = [fingerprint(r) for r in chosen]
    dup = len(fps) - len(set(fps))
    leak = len(set(fps) & all_fps)

    dist = Counter(r["completion"] for r in chosen)
    print(f"训练集总计: {len(chosen)} (目标 {TARGET_TOTAL})")
    print(f"标签分布: {dict(dist)}")
    print(f"KEEP 占比: {dist.get('KEEP',0)/len(chosen)*100:.1f}%")
    print(f"训练内重复: {dup} | 与 mixed/eval 泄漏: {leak}")

    if dup != 0 or leak != 0:
        print("重采样失败：存在重复或泄漏")
        return 1
    if len(chosen) != TARGET_TOTAL:
        print(f"重采样失败：总数 {len(chosen)} != {TARGET_TOTAL}")
        return 1
    if dist.get("KEEP", 0) != KEEP_TARGET:
        print(f"重采样失败：KEEP {dist.get('KEEP')} != {KEEP_TARGET}")
        return 1

    with open(os.path.join(OUT_DIR, "train.jsonl"), "w", encoding="utf-8") as f:
        for r in chosen:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    manifest = {
        "task_id": "T054-fix",
        "seed": SEED,
        "total": len(chosen),
        "keep_target": KEEP_TARGET,
        "other_per_label": OTHER_PER,
        "distribution": dict(dist),
        "dup_in_train": dup,
        "leak_with_mixed_or_eval": leak,
        "source": "hq1700 未训练池(排除 mixed+eval)",
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("manifest 写入:", os.path.join(OUT_DIR, "manifest.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
