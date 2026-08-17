#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T341/T342 — 数据校验脚本（T342 决策 A 修订版）。

读取 adapters/r3/schema.json，对 data/r3/ 下所有子目录（train/valid/fresh）递归扫描 *.jsonl
逐行校验：
  1. JSON 可解析
  2. 必填字段齐全（candidate_id, text, label, authority_type, operativeness, source, task_group, split）
  3. 枚举值域合法（label/authority_type/operativeness/task_group/split）
  4. label == operativeness 交叉一致性（若均存在）
  5. 行级泄漏检查（DECISION 2026-08-13 A）：同一 (query_id, candidate_id) 不得出现在多个
     split 中；允许同一 task_group 跨 train/valid/fresh（族内 72/10/18 切分合法）
  6. 输出各 split 行数与占比（用于核对 72/10/18）

空数据目录 → PASS（无数据时通过）。

输出: reports/r3/r3_validate_report.json + 控制台 PASS/FAIL。

运行:
  python3 scripts/r3_validate.py
"""
from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "adapters", "r3", "schema.json")
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "r3")
REPORT_DIR = os.path.join(PROJECT_ROOT, "reports", "r3")

REQUIRED_FIELDS = ["candidate_id", "text", "label", "authority_type", "operativeness", "source", "task_group", "split"]


def load_schema(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def enum_ok(value, schema_prop):
    return value in schema_prop.get("enum", [])


def iter_data_files(data_dir: str):
    """递归扫描 data_dir 下所有 *.jsonl 文件（含子目录），返回排序后的 (相对路径, 绝对路径) 列表。"""
    files = []
    for root, _dirs, fnames in os.walk(data_dir):
        for fn in sorted(fnames):
            if fn.endswith(".jsonl"):
                abs_path = os.path.join(root, fn)
                rel_path = os.path.relpath(abs_path, data_dir)
                files.append((rel_path, abs_path))
    return sorted(files)


def validate_file(path: str, schema: dict) -> dict:
    """逐行校验单个数据文件，返回 {ok, errors, rows}。"""
    errors = []
    rows = 0
    props = schema["properties"]
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            rows += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append({"file": os.path.basename(path), "line": lineno, "reason": f"JSON 解析失败: {e}"})
                continue
            if not isinstance(rec, dict):
                errors.append({"file": os.path.basename(path), "line": lineno, "reason": "记录非 JSON 对象"})
                continue
            missing = [k for k in REQUIRED_FIELDS if k not in rec]
            if missing:
                errors.append({"file": os.path.basename(path), "line": lineno, "reason": f"缺少必填字段: {missing}"})
                continue
            for key in ["label", "authority_type", "operativeness", "task_group", "split"]:
                if key in rec and not enum_ok(rec[key], props[key]):
                    errors.append({"file": os.path.basename(path), "line": lineno,
                                   "reason": f"字段 {key} 值 '{rec[key]}' 不在枚举 {props[key].get('enum')} 内"})
            if "text" in rec and (not isinstance(rec["text"], str) or len(rec["text"]) == 0):
                errors.append({"file": os.path.basename(path), "line": lineno, "reason": "字段 text 必须为非空字符串"})
            if "operativeness" in rec and "label" in rec and rec["operativeness"] != rec["label"]:
                errors.append({"file": os.path.basename(path), "line": lineno,
                               "reason": f"operativeness={rec['operativeness']} 与 label={rec['label']} 不一致"})
    return {"ok": len(errors) == 0, "errors": errors, "rows": rows}


def check_row_leakage(data_dir: str) -> list:
    """行级泄漏检查（DECISION A）：同一 (query_id, candidate_id) 跨多个 split 才判泄漏。

    允许同一 task_group 跨 train/valid/fresh。query_id 缺失时退化为 candidate_id。
    """
    seen = {}  # key -> split
    leaks = []
    for rel, path in iter_data_files(data_dir):
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                sp = rec.get("split")
                if "query_id" in rec and "candidate_id" in rec:
                    key = (rec["query_id"], rec["candidate_id"])
                elif "candidate_id" in rec:
                    key = rec["candidate_id"]
                else:
                    continue
                if key in seen and seen[key] != sp:
                    leaks.append({
                        "key": f"{key[0]}-{key[1]}" if isinstance(key, tuple) else str(key),
                        "splits": [seen[key], sp],
                        "detail": f"同一候选行出现在 {seen[key]} 与 {sp} 两个 split（行级泄漏）",
                    })
                seen[key] = sp
    return leaks


def split_distribution(data_dir: str) -> dict:
    dist = {}
    for _rel, path in iter_data_files(data_dir):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and "split" in rec:
                    dist[rec["split"]] = dist.get(rec["split"], 0) + 1
    return dist


def main() -> int:
    schema = load_schema(SCHEMA_PATH)
    files = iter_data_files(DATA_DIR)
    results = {}
    total_rows = 0
    all_errors = []
    for rel, path in files:
        r = validate_file(path, schema)
        results[rel] = {"rows": r["rows"], "ok": r["ok"]}
        total_rows += r["rows"]
        all_errors.extend(r["errors"])

    all_errors.extend(check_row_leakage(DATA_DIR))
    dist = split_distribution(DATA_DIR)

    passed = (len(all_errors) == 0)
    report = {
        "stage": "R3",
        "tool": "scripts/r3_validate.py",
        "decision_ref": "DECISION 2026-08-13 选项A（递归扫描 + 行级查重）",
        "schema": SCHEMA_PATH,
        "data_dir": DATA_DIR,
        "result": "PASS" if passed else "FAIL",
        "total_rows": total_rows,
        "split_distribution": dist,
        "per_file": results,
        "errors": all_errors[:50],
        "error_count": len(all_errors),
        "note": "空数据目录视为 PASS（无数据时通过）；行级泄漏=同一 query_id+candidate_id 跨 split",
    }
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "r3_validate_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if passed:
        print(f"[r3_validate] PASS — schema 校验通过, 共 {total_rows} 行, 0 错误")
        print(f"[r3_validate] split 分布: {dist}")
    else:
        print(f"[r3_validate] FAIL — {len(all_errors)} 个错误:")
        for e in all_errors:
            print("  -", e)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
