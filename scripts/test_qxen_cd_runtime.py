#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QXEN-CD runtime routing and rolling-context deterministic smoke tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from qxen_cd_runtime import TASK_INSTRUCTIONS, WORK_ROUTING, _faithful_result, build_prompt, route_backend  # noqa: E402
from qxen_cd_compact import compact  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def accepted(source: str, text: str, task: str = "key_evidence_selection") -> dict:
    capsule = {
        "relevance": "high",
        "key_evidence": [{"text": text, "source": source, "preserve_verbatim": True}],
        "timeline": ["v1 -> v2"],
        "conflicts": ["candidate-a vs candidate-b"],
        "uncertainty": ["date needs review"],
    }
    return {"guard_status": "ACCEPT", "task": task,
            "gpt_context": {"context_mode": "CAPSULE", "capsule": capsule}}


def main() -> int:
    faithful = _faithful_result(
        '{"summary":[],"omitted":[],"uncertainty":[]}',
        "2026年7月金融数据分析.pdf#chunk01",
        "faithful_chunk_distill",
        "图：M1/M2同比增速与剪刀差。\n7月新增社融14017亿元，同比多增2710亿元。M2同比由8.0%降至7.7%。",
    )
    faithful_summary = faithful["gpt_context"]["capsule"]["summary"]
    check(faithful["guard_status"] == "ADVISORY", "faithful deterministic fallback should be advisory")
    check(any("新增社融14017亿元" in x["text"] for x in faithful_summary), "faithful fallback should extract numeric fact")
    check(not any(x["text"].startswith("图：") for x in faithful_summary), "faithful fallback should filter chart captions")

    expected = {"capsule", "relevance_screening", "key_evidence_selection",
                "evidence_compression", "source_preservation", "preliminary_sufficiency",
                "timeline_extraction", "relation_extraction", "conflict_candidate_extraction",
                "rolling_context_compact"}
    check(expected <= set(TASK_INSTRUCTIONS), "task route missing")
    check("faithful_chunk_distill" in TASK_INSTRUCTIONS, "faithful distill task registered")
    check("qxen_longtext_distill" in TASK_INSTRUCTIONS, "longtext task registered")
    faithful = _faithful_result('{"summary":[{"text":"2026年7月社融变化","source":"doc.pdf#p1"}]}', "doc.pdf#p1", "faithful_chunk_distill")
    check(faithful["guard_status"] == "ADVISORY", "faithful task uses lightweight guard")
    longtext = _faithful_result('{"summary":[{"text":"2020Q4银行家问卷","source":"doc.pdf#p1"}]}', "doc.pdf#p1", "qxen_longtext_distill")
    check(longtext["guard_status"] == "ADVISORY", "longtext task uses lightweight guard")
    check(_faithful_result('{"relevance":"high"}', "doc.pdf#p1", "faithful_chunk_distill")["guard_status"] == "FALLBACK", "faithful malformed summary rejected")
    check(len(WORK_ROUTING) == 9, "work type routing incomplete")
    check(route_backend("backtest_result_organize")["backend"] == "qxen-cd", "backtest not QXEN primary")
    check(route_backend("failure_extract")["backend"] == "local-qwen", "technical task not LocalQwen primary")
    check(route_backend("timeline_extraction", evidence_chars=3200)["backend"] == "qxen-cd", "safe timeline block uses QXEN")
    check(route_backend("timeline_extraction", evidence_chars=7000)["reason"] == "deterministic_chunk_required_over_6000", "long block requires chunking")
    check(route_backend("timeline_extraction", evidence_chars=1200)["reason"] == "qxen_input_below_safe_minimum", "short block avoids QXEN")
    check(route_backend("evidence_compression", evidence_chars=1200)["backend"] != "qxen-cd", "generic compression avoids QXEN")
    prompt = build_prompt("doc/a", "原文", "timeline_extraction")
    check("QXEN-CD/timeline_extraction" in prompt, "task route not visible in prompt")
    check("提取事件、日期" in prompt, "task-specific instruction missing")
    conflict_prompt = build_prompt("doc/a", "原文", "conflict_candidate_extraction")
    check("只标记候选" in conflict_prompt, "conflict route instruction missing")

    first = accepted("doc/a", "不可改写日期 2026-08-15")
    first["raw_pointer"] = "/tmp/doc-a.txt"
    first["source_locator"] = {"path": "/tmp/doc-a.txt", "sha256": "abc", "span": "full_source"}
    first["consumption_policy"] = {
        "mode": "capsule_first_targeted_retrieval",
        "equivalence": "task_scoped_not_source_equivalent",
    }
    duplicate = json.loads(json.dumps(first, ensure_ascii=False))
    fallback = {"guard_status": "FALLBACK", "fallback_reason": "parse_error",
                "source": "doc/b", "raw_model_output": "{broken"}
    state = compact([first, duplicate, fallback], {"task_id": "T-QXEN", "as_of": "2026-08-15"}, max_chars=24000)
    check(len(state["accepted_capsules"]) == 1, "duplicate capsule was not removed")
    check(state["accepted_capsules"][0]["raw_pointer"] == "/tmp/doc-a.txt", "raw pointer lost")
    check(state["accepted_capsules"][0]["source_locator"]["sha256"] == "abc", "source locator lost")
    check(state["accepted_capsules"][0]["consumption_policy"]["mode"] == "capsule_first_targeted_retrieval", "consumption policy lost")
    check(len(state["pending_gpt_review"]) == 1, "fallback was not quarantined")
    check(state["verbatim_evidence"][0]["text"] == "不可改写日期 2026-08-15", "verbatim evidence lost")
    check(state["candidate_conflicts"] == ["candidate-a vs candidate-b"], "conflict candidate lost")
    check(state["dropped_summary"]["duplicate_capsules"] == 1, "drop accounting missing")
    print("QXEN-CD runtime smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
