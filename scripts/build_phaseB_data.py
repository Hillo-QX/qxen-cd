#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T054 — Phase B 数据准备：
  (1) 训练增量集：从 HQ1700 抽每类 30 条（210 条，非全量均衡，避开 T051 重演），
      与 T052 mixed 477 条合并 → data/phaseB/train.jsonl；
  (2) 评估扩充集：held-out 193 + HQ1700 抽 COMPRESS/REFRESH/RETRIEVE 各 15 条
      → data/phaseB/eval_extended.jsonl，覆盖盲区。

防泄漏：
  - 训练增量样本与 T052 已抽 HQ1700 子集(seed=42, 399条) 互斥；
  - 训练增量样本与评估扩充样本互斥；
  - 以完整 prompt 为指纹去重。

依据 T053 分析：HQ1700 增量纳入而非全量均衡；KEEP 为 held-out 主导(62%)，
训练集 KEEP 占比偏低，故增量抽取按 7 类均衡但总量受控(210/1700=12.4%)，
不改变 KEEP 主导的评估分布。
"""
from __future__ import annotations

import json
import os
import random

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HQ = os.path.join(PROJECT_ROOT, "data", "hq1700", "train.json")
MIXED = os.path.join(PROJECT_ROOT, "data", "mixed_train", "train.jsonl")
HELDOUT = os.path.join(PROJECT_ROOT, "data", "ctxA", "heldout_ctxA.jsonl")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "phaseB")

TRAIN_PER_LABEL = 30     # 训练增量：每类 30 条 → 210 条
EVAL_PER_LABEL = 15      # 评估扩充：COMPRESS/REFRESH/RETRIEVE 各 15 条
SEED = 7
BLIND_LABELS = {"COMPRESS", "REFRESH", "RETRIEVE"}


def fingerprint(r: dict) -> str:
    # 前 80 字符是公共指令模板（决策标签+格式），碰撞严重；用完整 prompt 保证唯一
    return r.get("prompt", "")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = random.Random(SEED)

    hq = json.load(open(HQ, encoding="utf-8"))
    mixed = [json.loads(l) for l in open(MIXED, encoding="utf-8") if l.strip()]
    held = [json.loads(l) for l in open(HELDOUT, encoding="utf-8") if l.strip()]

    # 已用于 T052 训练的 HQ1700 指纹（防同源样本重复训练）
    used_fps = {fingerprint(r) for r in mixed}

    # HQ1700 按标签分组，剔除 T052 已用样本
    by_label: dict[str, list[dict]] = {}
    for r in hq:
        if fingerprint(r) in used_fps:
            continue
        by_label.setdefault(r["completion"], []).append(r)

    # (2) 评估扩充：盲区三类各抽 EVAL_PER_LABEL 条
    eval_blind = []
    for lbl in sorted(BLIND_LABELS):
        pool = by_label.get(lbl, [])
        rng.shuffle(pool)
        eval_blind.extend(pool[:EVAL_PER_LABEL])
        by_label[lbl] = pool[EVAL_PER_LABEL:]

    # (1) 训练增量：7 类各抽 TRAIN_PER_LABEL 条
    train_inc = []
    for lbl in sorted(by_label):
        pool = by_label.get(lbl, [])
        rng.shuffle(pool)
        train_inc.extend(pool[:TRAIN_PER_LABEL])

    # 合并训练集
    train = mixed + train_inc
    # 评估集 = held-out + 盲区扩充
    eval_ext = held + eval_blind

    # 去重检查
    fps = [fingerprint(r) for r in train]
    dup = len(fps) - len(set(fps))
    # 训练/评估交叉泄漏检查
    train_fps = set(fps)
    leak = sum(1 for r in eval_ext if fingerprint(r) in train_fps)

    with open(os.path.join(OUT_DIR, "train.jsonl"), "w", encoding="utf-8") as f:
        for r in train:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT_DIR, "eval_extended.jsonl"), "w", encoding="utf-8") as f:
        for r in eval_ext:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    from collections import Counter
    print(f"训练增量: {len(train_inc)} 条 (每类 {TRAIN_PER_LABEL})")
    print(f"评估扩充: {len(eval_blind)} 条 (盲区三类)")
    print(f"训练集总计: {len(train)} (mixed {len(mixed)} + 增量 {len(train_inc)})")
    print(f"评估集总计: {len(eval_ext)} (heldout {len(held)} + 盲区 {len(eval_blind)})")
    print(f"训练集标签分布: {dict(Counter(r['completion'] for r in train))}")
    print(f"评估集标签分布: {dict(Counter(r['completion'] for r in eval_ext))}")
    print(f"训练内重复: {dup} | 训练/评估泄漏: {leak}")
    manifest = {
        "task_id": "T054",
        "train_inc_per_label": TRAIN_PER_LABEL,
        "eval_per_label": EVAL_PER_LABEL,
        "seed": SEED,
        "train_total": len(train),
        "eval_total": len(eval_ext),
        "dup_in_train": dup,
        "leak_train_eval": leak,
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("manifest 写入:", os.path.join(OUT_DIR, "manifest.json"))
    return 0 if (dup == 0 and leak == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
