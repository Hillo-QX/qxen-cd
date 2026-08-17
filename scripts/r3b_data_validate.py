#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R3B 能力覆盖样本校验器 — 骨架(SKELETON ONLY)

依据: docs/r3b_context_capability_data_design.md (DRAFT, 2026-08-14)
状态: 骨架代码, 不落地数据。待数据生成器实现后配套使用。

用法(当前仅占位):
    ./venv/bin/python scripts/r3b_data_validate.py --dry-run
"""
from __future__ import annotations
import argparse
import json
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# 每类样本的必填字段 / 类型约束 (对齐 design §3)
# ---------------------------------------------------------------------------

SCHEMA_RULES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "evidence_selection": {
        "type": {"required": True, "type": str},
        "prompt": {"required": True, "type": str, "nonempty": True},
        "completion": {"required": True, "type": str, "nonempty": True},
        "provenance": {"required": True, "type": dict},
    },
    "conflict_explanation": {
        "type": {"required": True, "type": str},
        "prompt": {"required": True, "type": str, "nonempty": True},
        "completion": {"required": True, "type": str, "nonempty": True},
        "provenance": {"required": True, "type": dict},
    },
    "uncertainty_retrieve": {
        "type": {"required": True, "type": str},
        "prompt": {"required": True, "type": str, "nonempty": True},
        "completion": {"required": True, "type": str, "nonempty": True},
        "provenance": {"required": True, "type": dict},
    },
    "state_update": {
        "type": {"required": True, "type": str},
        "prompt": {"required": True, "type": str, "nonempty": True},
        "completion": {"required": True, "type": str, "nonempty": True},
        "provenance": {"required": True, "type": dict},
    },
    "fidelity_compress": {
        "type": {"required": True, "type": str},
        "prompt": {"required": True, "type": str, "nonempty": True},
        "completion": {"required": True, "type": str, "nonempty": True},
        "provenance": {"required": True, "type": dict},
    },
}


def validate_sample(sample: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    校验单条样本: 必填字段、类型、非空约束。
    返回 (is_valid, errors)。
    待实现: 按 sample['type'] 取 SCHEMA_RULES 对应规则逐字段检查;
            额外校验 completion 是否为合法 JSON 片段(按类型策略)。
    """
    errors: List[str] = []
    sample_type = sample.get("type")
    if sample_type not in SCHEMA_RULES:
        errors.append(f"unknown type: {sample_type!r}")
        return False, errors
    rules = SCHEMA_RULES[sample_type]
    for field_name, rule in rules.items():
        if rule.get("required") and field_name not in sample:
            errors.append(f"missing required field: {field_name}")
            continue
        val = sample.get(field_name)
        if rule.get("nonempty") and not val:
            errors.append(f"empty field: {field_name}")
        if val is not None and not isinstance(val, rule.get("type")):
            errors.append(f"field {field_name}: expected {rule['type'].__name__}, got {type(val).__name__}")
    return len(errors) == 0, errors


def validate_file(path: str) -> Tuple[int, List[str]]:
    """
    校验整个 JSONL 文件: 逐行 json.loads + validate_sample。
    返回 (valid_count, errors)。
    待实现: 附加分布统计(每类型条数/比例)、provenance 来源追踪、train/valid 组隔离检查。
    """
    raise NotImplementedError("SKELETON: 待数据生成后实现")


def main() -> int:
    parser = argparse.ArgumentParser(description="R3B 能力样本校验器 (SKELETON)")
    parser.add_argument("--dry-run", action="store_true", help="仅打印校验规则, 不读文件")
    args = parser.parse_args()
    if args.dry_run:
        print("SKELETON: 校验规则覆盖", len(SCHEMA_RULES), "类样本:")
        for t, rules in SCHEMA_RULES.items():
            print(f"  {t}: {len(rules)} 字段规则")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
