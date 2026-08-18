#!/usr/bin/env python3
"""Verify longtext uses final GPT context burden, not full MCP envelope size."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

    compacted = asyncio.run(qxen.qxen_cd_compact(records=[bypassed], task_id="bypass-test"))
    assert compacted["accepted_capsules"] == []
    assert compacted["dropped_summary"].get("context_burden_bypass") == 1
    print("test_qxen_context_burden_gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
