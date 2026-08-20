#!/usr/bin/env python3
"""Verify longtext uses final GPT context burden, not full MCP envelope size."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qxen_cd_mcp as qxen  # noqa: E402


async def call_with_summary(summary: str, source_text: str) -> dict:
    original = qxen._qxen_generate

    async def fake_generate(**kwargs):
        return {
            "runtime": "QXEN-CD",
            "task": "qxen_longtext_distill",
            "guard_status": "ADVISORY",
            "requires_gpt_review": False,
            "review_policy": "conditional",
            "gpt_context": {
                "context_mode": "ADVISORY_ONLY",
                "capsule": {"summary": [summary], "source": kwargs.get("source")},
            },
        }

    qxen._qxen_generate = fake_generate
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            handle.write(source_text)
            path = Path(handle.name)
        try:
            return await qxen.qxen_cd_longtext_distill(source=path.name, source_path=str(path))
        finally:
            path.unlink(missing_ok=True)
    finally:
        qxen._qxen_generate = original


async def call_partial_chunks() -> dict:
    original = qxen._qxen_generate
    calls = {"n": 0}

    async def fake_generate(**kwargs):
        calls["n"] += 1
        summary = "第一块有效摘要" if calls["n"] == 1 else ("第二块膨胀摘要" * 1200)
        return {
            "runtime": "QXEN-CD",
            "task": "qxen_longtext_distill",
            "guard_status": "ADVISORY",
            "requires_gpt_review": False,
            "review_policy": "conditional",
            "gpt_context": {
                "context_mode": "ADVISORY_ONLY",
                "capsule": {"summary": [summary], "source": kwargs.get("source")},
            },
        }

    qxen._qxen_generate = fake_generate
    try:
        source = "第一块原文。" * 1000 + "第二块原文。" * 1000
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            handle.write(source)
            path = Path(handle.name)
        try:
            return await qxen.qxen_cd_longtext_distill(source=path.name, source_path=str(path))
        finally:
            path.unlink(missing_ok=True)
    finally:
        qxen._qxen_generate = original


async def call_with_tail_chunk(tail_chars: int) -> tuple[dict, int]:
    original = qxen._qxen_generate
    calls = {"n": 0}

    async def fake_generate(**kwargs):
        calls["n"] += 1
        return {
            "runtime": "QXEN-CD",
            "task": "qxen_longtext_distill",
            "guard_status": "ADVISORY",
            "requires_gpt_review": False,
            "review_policy": "conditional",
            "gpt_context": {
                "context_mode": "ADVISORY_ONLY",
                "capsule": {
                    "summary": ["长块摘要"],
                    "source": kwargs.get("source"),
                },
            },
        }

    qxen._qxen_generate = fake_generate
    try:
        source = ("甲" * 6000) + ("乙" * tail_chars)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            handle.write(source)
            path = Path(handle.name)
        try:
            return await qxen.qxen_cd_longtext_distill(source=path.name, source_path=str(path)), calls["n"]
        finally:
            path.unlink(missing_ok=True)
    finally:
        qxen._qxen_generate = original


async def call_skill_bypass() -> tuple[dict, int]:
    original = qxen._qxen_generate
    calls = {"n": 0}

    async def fake_generate(**kwargs):
        calls["n"] += 1
        return {"guard_status": "ADVISORY", "gpt_context": {"capsule": {"summary": ["should not run"]}}}

    qxen._qxen_generate = fake_generate
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text("# Demo skill\n" + ("x" * 3000), encoding="utf-8")
            return await qxen.qxen_cd_longtext_distill(source=path.name, source_path=str(path)), calls["n"]
    finally:
        qxen._qxen_generate = original


def main() -> int:
    long_source = "甲公司收入同比增长，现金流改善，订单恢复。" * 350
    injected = asyncio.run(call_with_summary("收入增长、现金流改善、订单恢复。", long_source))
    assert injected["status"] == "INJECT_QXEN"
    assert injected["guard_status"] == "ADVISORY"
    assert injected["context_burden"]["decision"] == "INJECT_QXEN"
    assert injected["context_burden"]["ratio"] < 1
    assert injected["gpt_context_payload"]["capsules"]
    assert "compact_state" not in injected
    assert "debug_only" not in injected

    short_source = "短文本。"
    bypassed = asyncio.run(call_with_summary("短文本。", short_source))
    assert bypassed["status"] == "BYPASS_QXEN"
    assert bypassed["guard_status"] == "BYPASS"
    assert bypassed["context_burden"]["decision"] == "BYPASS_QXEN"
    assert bypassed["context_burden"]["ratio"] == 1.0
    assert bypassed["gpt_context_payload"] is None
    assert bypassed["bypass_reason"] in {"no_accepted_capsule", "context_burden_not_reduced"}

    partial = asyncio.run(call_partial_chunks())
    assert partial["status"] == "INJECT_QXEN"
    assert partial["partial"] is True
    assert partial["accepted_capsule_count"] == 1
    assert partial["dropped_chunks"][0]["chunk"] == 2
    assert partial["gpt_context_payload"]["capsules"][0]["summary"] == ["第一块有效摘要"]

    for tail_chars in (98, 220):
        passthrough, calls = asyncio.run(call_with_tail_chunk(tail_chars))
        assert passthrough["status"] == "INJECT_QXEN"
        assert calls == 1
        assert passthrough["partial"] is False
        assert passthrough["passthrough_chars"] == tail_chars
        assert passthrough["passthrough_chunks"][0]["decision"] == "RAW_PASSTHROUGH"
        assert passthrough["passthrough_chunks"][0]["reason"] == "raw_chunk_below_break_even_threshold"
        assert passthrough["raw_passthrough_max_chars"] == 220
        assert passthrough["source_coverage_ratio"] == 1.0
        assert passthrough["context_burden"]["source_coverage_ratio"] == 1.0
        assert passthrough["context_burden"]["passthrough_chars"] == tail_chars
        assert passthrough["context_burden"]["final_gpt_chars"] == (
            passthrough["context_burden"]["distilled_chars"]
            + passthrough["context_burden"]["passthrough_chars"]
            + passthrough["context_burden"]["payload_overhead_chars"]
        )
        assert passthrough["gpt_context_payload"]["raw_passthrough_chunks"][0]["raw_chars"] == tail_chars

    qxen_flow, calls = asyncio.run(call_with_tail_chunk(221))
    assert qxen_flow["status"] == "INJECT_QXEN"
    assert calls == 2
    assert qxen_flow["passthrough_chars"] == 0
    assert qxen_flow["passthrough_chunks"] == []

    skill_bypass, calls = asyncio.run(call_skill_bypass())
    assert calls == 0
    assert skill_bypass["status"] == "BYPASS_QXEN"
    assert skill_bypass["guard_status"] == "BYPASS"
    assert skill_bypass["bypass_reason"] == "skill_instruction_requires_verbatim_read"
    assert skill_bypass["context_burden"]["ratio"] == 1.0
    assert skill_bypass["gpt_context_payload"] is None

    compacted = asyncio.run(qxen.qxen_cd_compact(records=[bypassed], task_id="bypass-test"))
    assert compacted["accepted_capsules"] == []
    assert compacted["dropped_summary"].get("context_burden_bypass") == 1
    print("test_qxen_context_burden_gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
