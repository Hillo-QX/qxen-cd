#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QXEN-CD Evidence Capsule v1 契约 smoke 测试（T001 产物）。

覆盖（acceptance_criteria 要求 ≥5 用例）：
  1. 必填字段完整性检查（缺任一必填 → 校验失败）
  2. 字段类型检查（source_type/relevance/sufficiency 枚举非法 → 失败）
  3. 范围检查（metadata 结构、EvidenceItem preserve_verbatim 布尔、key_evidence 元素必填）
  4. 最小合法 JSON 样例解析（合法样例 → 通过）
  5. 可选字段处理（空数组/省略可选字段 → 合法）
  6. 样例=用户方案原文示例（合法 → 通过）

运行：venv/bin/python -m pytest scripts/test_evidence_capsule_contract.py -v
    或：venv/bin/python scripts/test_evidence_capsule_contract.py（直接运行）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import jsonschema
    from jsonschema import validate, ValidationError
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

SCHEMA_PATH = ROOT / "configs" / "evidence_capsule_v1_schema.json"
MINIMAL_OK = {
    "capsule_id": "EC-001",
    "source_type": "report",
    "relevance": "high",
    "key_evidence": [
        {"text": "后续版本 v2 于 2024-06-03 发布", "source": "candidate/path#v2", "preserve_verbatim": True}
    ],
    "sufficiency": "insufficient",
}

# 用户方案原文示例（扩成完整胶囊）
USER_EXAMPLE = {
    "capsule_id": "EC-002",
    "source_type": "data_file",
    "relevance": "high",
    "key_evidence": [
        {"text": "后续版本 v2 于 2024-06-03 发布", "source": "candidate/path#v2", "preserve_verbatim": True}
    ],
    "timeline": [
        "v1 发布：2024-01-03",
        "v1 归档：2024-05-03",
        "v2 发布：2024-06-03",
        "as_of：2024-07-03",
    ],
    "relations": [
        "as_of 晚于 v1 归档日期",
        "as_of 晚于 v2 发布日期",
    ],
    "conflicts": [],
    "uncertainty": ["v2 的正式替代关系需要主 Agent 复核"],
    "immutable_fields": ["日期", "版本号", "来源路径", "哈希"],
    "compressible": ["背景说明", "重复描述"],
    "sufficiency": "insufficient",
    "next_step": "继续查找 v2 的正式发布证据",
    "reference": ["candidate/path#v1", "candidate/path#v2"],
    "metadata": {"model": "qxen-capsule-v1", "contract_version": "v1", "created_at": "2026-08-14T00:00:00+00:00", "as_of": "2024-07-03"},
}

tests = []


def t(name, fn):
    tests.append((name, fn))


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def run_validate(schema, instance):
    if HAS_JSONSCHEMA:
        validate(instance, schema)
    else:
        # 无 jsonschema 时做最小手动校验（必填字段）
        for f in schema.get("required", []):
            if f not in instance:
                raise ValidationError(f"missing required field: {f}")


t("1_必填字段缺失应失败", lambda: _expect_fail({k: v for k, v in MINIMAL_OK.items() if k != "relevance"}))
t("2_枚举非法应失败", lambda: _expect_fail({**MINIMAL_OK, "relevance": "urgent"}))
t("2b_source_type非法应失败", lambda: _expect_fail({**MINIMAL_OK, "source_type": "random"}))
t("3_EvidenceItem缺source应失败", lambda: _expect_fail({
    **MINIMAL_OK, "key_evidence": [{"text": "no source"}]}))
t("4_最小合法样例应通过", lambda: run_validate(load_schema(), MINIMAL_OK))
t("5_可选字段省略应通过", lambda: run_validate(load_schema(), {
    **MINIMAL_OK, "key_evidence": [{"text": "x", "source": "s"}]}))
t("5b_空数组可选字段应通过", lambda: run_validate(load_schema(), {
    **MINIMAL_OK, "conflicts": [], "timeline": [], "reference": []}))
t("6_用户方案示例应通过", lambda: run_validate(load_schema(), USER_EXAMPLE))
t("6b_sufficiency非法应失败", lambda: _expect_fail({**MINIMAL_OK, "sufficiency": "maybe"}))
t("6c_preserve_verbatim非bool应失败", lambda: _expect_fail({
    **MINIMAL_OK, "key_evidence": [{"text": "x", "source": "s", "preserve_verbatim": "yes"}]}))


def _expect_fail(instance):
    try:
        run_validate(load_schema(), instance)
    except ValidationError:
        return True
    raise AssertionError("expected ValidationError but passed")


def main():
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
