#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析 C(CoT) 诊断结果：对 invalid + conflict 错例做四分类。

分类维度：
  - 截断：raw 末尾无 "效力状态" 行（JSON/五行未闭合）
  - 枚举外标签：reason_code 字段值不在 19 类 gold 枚举内
  - 格式污染：字段值正确但带推理尾巴（如 "false（v2取代...）"）
  - 语义错：字段值本身错误（非污染、非枚举外）
"""
from __future__ import annotations
import json, sys, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 19 类 gold 枚举
REASONS = {
    "ACTIVE_CONFIG", "ACTIVE_SCHEMA", "AGENT_REPORT", "AGENT_SUMMARY",
    "ARCHIVED_BACKUP", "CONFLICT_T0_T1", "CURRENT_SOURCE",
    "DEPRECATED_SCHEMA", "EXECUTED_CODE", "EXECUTED_SCHEMA",
    "HISTORICAL_LOG", "LOW_AUTHORITY_NOTE", "NOT_APPLICABLE_TO_TASK",
    "ONLY_SURVIVING_RECORD", "PROJECT_SPEC", "README_STATEMENT",
    "RUNTIME_TRUTH", "SUPERSEDED_SIMILAR", "VERIFIER_TRUTH",
}


def classify(rec: dict) -> str:
    raw = rec["raw"]
    p = rec["parsed"]
    # 截断：缺效力状态行
    if p["status"] is None:
        return "截断(缺效力状态行)"
    if p["authority"] is None or p["conflict_raw"] is None or p["reason_raw"] is None:
        return "截断/字段缺失"
    # reason_code 枚举外
    r = p["reason_raw"].strip()
    if r not in REASONS:
        return f"枚举外reason({r[:40]})"
    # 格式污染：conflict 字段带尾巴（含括号说明）
    if p["conflict_raw"] not in ("true", "false"):
        return f"conflict污染({p['conflict_raw'][:40]})"
    # 语义错
    return "语义错"


def main():
    diag_path = sys.argv[1] if len(sys.argv) > 1 else "reports/r3/r3a_cot_diag.jsonl"
    recs = [json.loads(l) for l in open(diag_path, encoding="utf-8")]

    invalid = [r for r in recs if not r["valid"]]
    conf_bad = [r for r in recs if r["valid"] and not r["conflict_correct"]]
    reason_bad = [r for r in recs if r["valid"] and not r["reason_correct"]]

    print(f"总样本 {len(recs)} | invalid {len(invalid)} | conflict错 {len(conf_bad)} | reason错 {len(reason_bad)}\n")

    print("=== invalid 样本分类 ===")
    from collections import Counter
    c = Counter(classify(r) for r in invalid)
    for k, v in c.most_common():
        print(f"  {v:3}  {k}")
    print("\n=== invalid 样本 raw 详情（前4）===")
    for r in invalid[:4]:
        print(f"  [{r['query_id']}] gold={r['gold_label']} len={len(r['raw'])}")
        print(f"    raw尾: {repr(r['raw'][-120:])}")
        print()

    print("=== conflict 错例分类 ===")
    c2 = Counter(classify(r) for r in conf_bad)
    for k, v in c2.most_common():
        print(f"  {v:3}  {k}")
    print("\n=== conflict 错例 raw 详情（前5）===")
    for r in conf_bad[:5]:
        print(f"  [{r['query_id']}] gold_conflict={r['gold_conflict']} parsed={r['parsed']['conflict_raw']!r}")
        print(f"    raw: {repr(r['raw'][:200])}")
        print()


if __name__ == "__main__":
    main()
