#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1 数据集静态校验（T003 验收）。

校验：样本数、字段完整性、span 逐字匹配（正例）、反例比例≥15%、
hard+fresh_like≥30%、任务族/难度覆盖、三集合两两无重叠（内容哈希）。

用法：./venv/bin/python scripts/validate_v1_1_data.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ_FIELDS = ["id", "task_family", "difficulty", "conflict_type",
              "is_counterexample", "source_doc_id", "source_text",
              "span", "prompt", "completion"]
TASK_FAMILIES = {"evidence_compression", "timeline", "conflict_candidate", "action_suggestion"}
DIFFICULTIES = {"easy", "hard", "fresh_like"}


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in open(p, encoding="utf-8")]


def h(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def check_span_verbatim(row: dict) -> bool:
    """正例：gold.key_evidence 必须逐字存在于 source_text；反例不校验（本就故意错误）。"""
    if row["is_counterexample"]:
        return True
    try:
        gold = json.loads(row["completion"])
    except Exception:
        return False
    ke = gold.get("key_evidence", "")
    return ke in row["source_text"] or ke == ""


def main() -> int:
    ok = True
    files = {
        "train": ROOT / "data/v1.1/train/train.jsonl",
        "valid": ROOT / "data/v1.1/val/valid.jsonl",
        "fresh": ROOT / "data/v1.1/fresh/fresh.jsonl",
    }
    data = {}
    for name, p in files.items():
        if not p.is_file():
            print(f"FAIL {name}: {p} 不存在")
            return 1
        rows = load(p)
        data[name] = rows
        n = len(rows)
        # 样本数范围
        lo, hi = {"train": (1500, 2000), "valid": (150, 250), "fresh": (50, 100)}[name]
        good_n = lo <= n <= hi
        ok &= good_n
        print(f"[{'OK' if good_n else 'FAIL'}] {name} 样本数 {n} (期望 {lo}-{hi})")
        # 字段完整性
        missing = set()
        for r in rows:
            for f in REQ_FIELDS:
                if f not in r:
                    missing.add(f)
        good_f = not missing
        ok &= good_f
        print(f"[{'OK' if good_f else 'FAIL'}] {name} 字段完整 {'' if good_f else missing}")
        # 任务族/难度枚举
        tf = {r["task_family"] for r in rows}
        df = {r["difficulty"] for r in rows}
        good_tf = tf.issubset(TASK_FAMILIES) and len(tf) == 4
        good_df = df.issubset(DIFFICULTIES) and len(df) == 3
        ok &= good_tf and good_df
        print(f"[{'OK' if good_tf else 'FAIL'}] {name} 任务族覆盖 {len(tf)}/4")
        print(f"[{'OK' if good_df else 'FAIL'}] {name} 难度覆盖 {len(df)}/3")
        # 比例
        ce = sum(1 for r in rows if r["is_counterexample"])
        hf = sum(1 for r in rows if r["difficulty"] in ("hard", "fresh_like"))
        ce_frac, hf_frac = ce / n, hf / n
        good_ce = ce_frac >= 0.15
        good_hf = hf_frac >= 0.30
        ok &= good_ce and good_hf
        print(f"[{'OK' if good_ce else 'FAIL'}] {name} 反例 {ce}/{n}={ce_frac:.3f} (≥0.15)")
        print(f"[{'OK' if good_hf else 'FAIL'}] {name} hard+fresh_like {hf}/{n}={hf_frac:.3f} (≥0.30)")
        # span 逐字（正例）
        bad_span = [r["id"] for r in rows if not check_span_verbatim(r)]
        good_span = not bad_span
        ok &= good_span
        print(f"[{'OK' if good_span else 'FAIL'}] {name} span 逐字校验 {'PASS' if good_span else bad_span[:5]}")
        # 来源文档隔离（集内 source_doc_id 可重复，但保证来源属于本集）
        srcs = {r["source_doc_id"] for r in rows}
        print(f"[info] {name} 来源文档数 {len(srcs)}")

    # 三集合两两无重叠（按 source_doc_id + 内容哈希双证明）
    for a, b in [("train", "valid"), ("train", "fresh"), ("valid", "fresh")]:
        ha = {h(json.dumps(r, ensure_ascii=False, sort_keys=True)) for r in data[a]}
        hb = {h(json.dumps(r, ensure_ascii=False, sort_keys=True)) for r in data[b]}
        overlap = ha & hb
        sa = {r["source_doc_id"] for r in data[a]}
        sb = {r["source_doc_id"] for r in data[b]}
        src_overlap = sa & sb
        good = not overlap and not src_overlap
        ok &= good
        print(f"[{'OK' if good else 'FAIL'}] {a}×{b} 无重叠 样本哈希重叠={len(overlap)} 来源重叠={len(src_overlap)}")

    print("=" * 50)
    print(f"[validate_v1_1_data] {'ALL PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
