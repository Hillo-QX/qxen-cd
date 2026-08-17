#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T054 Phase B 修复轮 v2 — 训练数据重采样（Dispatcher 决策 A）：
  上一轮 KEEP=60% 导致 PIN/VERBATIM 被吞并（精确召回 0%）。
  新分布：KEEP=45%，PIN=15%，VERBATIM=15%，其余 4 类均分 25%。
  210 条整数分配：KEEP=95(45.2%)、PIN=31(14.8%)、VERBATIM=32(15.2%)、
                  COMPRESS/DROP/REFRESH/RETRIEVE 各 13(6.2%)。

  数据源：全部从 HQ1700 未训练池抽取（排除 mixed used_fps 与 eval_extended 指纹）。
  防泄漏：指纹 = 完整 prompt；dup==0 且 leak==0 才成功。
"""
from __future__ import annotations

import json
import os
import random
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIXED = os.path.join(PROJECT_ROOT, "data", "mixed_train", "train.jsonl")
EVAL_EXT = os.path.join(PROJECT_ROOT, "data", "phaseB", "eval_extended.jsonl")
HQ = os.path.join(PROJECT_ROOT, "data", "hq1700", "train.json")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "phaseB", "train_resampled_v2")

SEED = 42
TARGET_PER_LABEL = {
    "KEEP": 95,       # 45.2%
    "PIN": 31,        # 14.8%
    "VERBATIM": 32,   # 15.2%
    "COMPRESS": 13,   # 6.2%
    "DROP": 13,       # 6.2%
    "REFRESH": 13,    # 6.2%
    "RETRIEVE": 13,   # 6.2%
}
TARGET_TOTAL = sum(TARGET_PER_LABEL.values())   # 210


def fingerprint(r: dict) -> str:
    return r.get("prompt", "")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = random.Random(SEED)

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
    for lbl, target in sorted(TARGET_PER_LABEL.items()):
        pool = list(by_label.get(lbl, []))
        rng.shuffle(pool)
        if len(pool) < target:
            print(f"重采样失败：{lbl} 可用 {len(pool)} < 目标 {target}")
            return 1
        chosen.extend(pool[:target])

    # 去重 / 泄漏检查
    fps = [fingerprint(r) for r in chosen]
    dup = len(fps) - len(set(fps))
    leak = len(set(fps) & all_fps)

    dist = Counter(r["completion"] for r in chosen)
    print(f"训练集总计: {len(chosen)} (目标 {TARGET_TOTAL})")
    print(f"标签分布: {dict(dist)}")
    print(f"KEEP 占比: {dist.get('KEEP',0)/len(chosen)*100:.1f}% | PIN+VERBATIM 占比: {(dist.get('PIN',0)+dist.get('VERBATIM',0))/len(chosen)*100:.1f}%")
    print(f"训练内重复: {dup} | 与 mixed/eval 泄漏: {leak}")

    if dup != 0 or leak != 0:
        print("重采样失败：存在重复或泄漏")
        return 1
    if len(chosen) != TARGET_TOTAL:
        print(f"重采样失败：总数 {len(chosen)} != {TARGET_TOTAL}")
        return 1
    if dict(dist) != TARGET_PER_LABEL:
        print(f"重采样失败：分布 {dict(dist)} != 目标 {TARGET_PER_LABEL}")
        return 1

    with open(os.path.join(OUT_DIR, "train.jsonl"), "w", encoding="utf-8") as f:
        for r in chosen:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    manifest = {
        "task_id": "T054-fix-v2",
        "seed": SEED,
        "total": len(chosen),
        "per_label": TARGET_PER_LABEL,
        "distribution": dict(dist),
        "dup_in_train": dup,
        "leak_with_mixed_or_eval": leak,
        "source": "hq1700 未训练池(排除 mixed+eval)",
        "decision": "A: KEEP=45% PIN/VERBATIM 各15% 其余4类均分25%",
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("manifest 写入:", os.path.join(OUT_DIR, "manifest.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
