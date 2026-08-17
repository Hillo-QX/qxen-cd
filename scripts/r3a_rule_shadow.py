#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3A 规则层只读 shadow baseline。

从冻结 train 学习确定性多数标签映射，在 fresh 上评估；不加载模型、不修改
冻结数据、不改变 Gate 协议。用于判断状态标签是否具有可分证据。
"""
from __future__ import annotations

import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(root):
    rows = []
    for path in sorted((ROOT / root).glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def majority(rows, key):
    by = defaultdict(Counter)
    for row in rows:
        by[key(row)][row["label"]] += 1
    return {k: c.most_common(1)[0][0] for k, c in by.items()}


def evaluate(train, fresh, key):
    mapping = majority(train, key)
    pred = [mapping.get(key(row), "CURRENT") for row in fresh]
    n = len(fresh)
    acc = sum(row["label"] == p for row, p in zip(fresh, pred)) / n
    sup = [i for i, row in enumerate(fresh) if row["label"] == "SUPERSEDED"]
    sup_rej = sum(pred[i] == "SUPERSEDED" for i in sup) / len(sup)
    wrong_auth = sum(pred[i] == "CURRENT" and fresh[i]["label"] != "CURRENT" for i in range(n)) / n
    return {
        "cells": len(mapping),
        "accuracy": round(acc, 4),
        "superseded_rejection": round(sup_rej, 4),
        "wrong_authority_preference_rate": round(wrong_auth, 4),
        "coverage": round(sum(key(row) in mapping for row in fresh) / n, 4),
        "predictions": dict(Counter(pred)),
    }


def main():
    train, fresh = load("data/r3/train"), load("data/r3/fresh")
    keys = {
        "reason_code": lambda r: r["reason_code"],
        "task_reason": lambda r: (r["task_group"], r["reason_code"]),
        "task_reason_auth_conf": lambda r: (r["task_group"], r["reason_code"], r["authority_type"], r["material_conflict"]),
    }
    out = {name: evaluate(train, fresh, fn) for name, fn in keys.items()}
    report = {"source": "frozen train -> frozen fresh labels only", "n_fresh": len(fresh), "baselines": out}
    path = ROOT / "reports/r3/r3a_rule_shadow.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
