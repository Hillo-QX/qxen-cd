#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QXEN-CD capsule schema 一致性校验（Kimi-Expert ACTION 补强 2）。

数据生成后固定 verifier 步骤：对 capsule jsonl（data100 / data1000 / 后续档位）
逐行校验：
  1. JSON 可解析
  2. 与 evidence_capsule_v1_schema.json 全量 jsonschema 校验（Draft7，additionalProperties:false 严格）
  3. evidence_links 语义约束（若存在）：
     - evidence_refs 必须引用 key_evidence[].source 值（Kimi 边界规则）
     - 不强制要求与 conflicts 去重（conflicts 为自由字符串无法程序化比对，由生成器纪律约束）
  4. 必填字段齐全（由 schema required 保证）

运行:
  ./venv/bin/python scripts/verify_capsule_schema.py [--dirs data/r3/ec_v1/data1000 ...]

默认扫描 data/r3/ec_v1/data100 与 data1000 下 *_capsule.jsonl（不含 state_patch 契约）。
退出码: 0=PASS, 1=FAIL。
输出: reports/r3/verify_capsule_schema_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from jsonschema import Draft7Validator
except ImportError:
    print("FATAL: 需要 jsonschema（venv 内有）：./venv/bin/python scripts/verify_capsule_schema.py")
    sys.exit(1)

SCHEMA_PATH = PROJECT_ROOT / "configs/evidence_capsule_v1_schema.json"
DEFAULT_DIRS = ["data/r3/ec_v1/data100", "data/r3/ec_v1/data1000"]
REPORT_PATH = PROJECT_ROOT / "reports/r3/verify_capsule_schema_report.json"
GLOB_PATTERNS = ["*capsule.jsonl"]
# state_patch_validation.jsonl 是 state_patch 契约（sample_id/old_state/new_event/patch/expected_ops），
# 不属于 capsule 契约，不在此校验；若需校验 state_patch 单独扩展（skill §4.3 首轮未训 state_patch）。


def collect_files(dirs: list[str]) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        base = PROJECT_ROOT / d
        if not base.is_dir():
            print(f"WARN: 目录不存在，跳过: {d}")
            continue
        for pat in GLOB_PATTERNS:
            files.extend(sorted(base.glob(pat)))
    return files


def check_evidence_refs(rec: dict) -> list[str]:
    """evidence_refs 必须引用 key_evidence[].source。返回违规列表。"""
    issues: list[str] = []
    links = rec.get("evidence_links") or []
    sources = {item.get("source") for item in (rec.get("key_evidence") or [])}
    for i, link in enumerate(links):
        refs = link.get("evidence_refs") or []
        for ref in refs:
            if ref not in sources:
                issues.append(
                    f"evidence_links[{i}].evidence_refs 引用 {ref!r} 不在 key_evidence[].source 集合中"
                )
    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dirs", nargs="*", default=DEFAULT_DIRS)
    ap.add_argument("--report", default=str(REPORT_PATH))
    args = ap.parse_args()

    if not SCHEMA_PATH.exists():
        print(f"FATAL: schema 不存在: {SCHEMA_PATH}")
        return 1

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)

    files = collect_files(args.dirs)
    if not files:
        print("FATAL: 未找到任何校验目标文件")
        return 1

    total_errors: list[dict] = []
    total_lines = 0
    per_file = []
    for f in files:
        file_errors: list[dict] = []
        n_lines = 0
        for ln, line in enumerate(f.open(encoding="utf-8"), 1):
            n_lines += 1
            total_lines += 1
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                file_errors.append({"line": ln, "type": "json", "detail": str(e)})
                continue
            for verr in validator.iter_errors(rec):
                file_errors.append({
                    "line": ln,
                    "type": "schema",
                    "path": "." + ".".join(str(p) for p in verr.path),
                    "detail": verr.message,
                })
            for issue in check_evidence_refs(rec):
                file_errors.append({"line": ln, "type": "evidence_refs", "path": "", "detail": issue})
        total_errors.extend(file_errors)
        per_file.append({"file": str(f.relative_to(PROJECT_ROOT)), "lines": n_lines, "errors": len(file_errors)})
        print(f"  {f.relative_to(PROJECT_ROOT)}: {n_lines} 行, 错误 {len(file_errors)}")

    report = {
        "script": "scripts/verify_capsule_schema.py",
        "schema": str(SCHEMA_PATH.relative_to(PROJECT_ROOT)),
        "files": per_file,
        "total_lines": total_lines,
        "total_errors": len(total_errors),
        "errors": total_errors[:50],
        "status": "PASS" if not total_errors else "FAIL",
        "as_of": None,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n总行数: {total_lines}, 总错误: {len(total_errors)} -> {report['status']}")
    print(f"报告: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0 if not total_errors else 1


if __name__ == "__main__":
    sys.exit(main())
