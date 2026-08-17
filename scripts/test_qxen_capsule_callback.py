#!/usr/bin/env python3
"""Verify QXEN process automatically claims and fails a capsule on fallback."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import response_capsule as rc
sys.path.insert(0, str(rc.ROOT))
import qxen_cd_mcp as qxen


async def run_case(envelope: Path) -> dict:
    return await qxen._qxen_generate(
        source="callback-test",
        evidence="callback test",
        task="unknown_callback_test",
        capsule_id=str(envelope),
        work_item_id="callback-test",
        task_id="callback-test",
        workspace=str(rc.ROOT),
    )


async def run_longtext_case(envelope: Path, source: str) -> dict:
    original = qxen._qxen_generate

    async def fake_process(**kwargs):
        return {"runtime": "QXEN-CD", "task": "qxen_longtext_distill",
                "guard_status": "ADVISORY", "requires_gpt_review": False,
                "review_policy": "conditional",
                "received_preflight": kwargs["evidence"].startswith("[DETERMINISTIC_PREFLIGHT]"),
                "gpt_context": {"capsule": {"summary": ["保真摘要"]}}}

    qxen._qxen_generate = fake_process
    try:
        return await qxen.qxen_cd_longtext_distill(
            source=source, evidence="可复用状态摘要",
            capsule_id=str(envelope), session_id="callback-longtext-session",
        )
    finally:
        qxen._qxen_generate = original


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        old_queue, old_log = rc.QUEUE, rc.P1_LOG
        old_audit_log = qxen.AUDIT_LOG
        rc.QUEUE = Path(tmp)
        rc.P1_LOG = Path(tmp) / "p1.jsonl"
        qxen.AUDIT_LOG = Path(tmp) / "qxen-audit.jsonl"
        rc.record({"assistant_response": "交接状态\n" + "x" * 5000,
                   "session_id": "callback-session", "task": "交接状态"})
        envelope = next(Path(tmp).glob("*.json"))
        asyncio.run(run_case(envelope))
        data = json.loads(envelope.read_text(encoding="utf-8"))
        assert data["status"] == rc.PENDING_STATUS
        assert data["attempts"] == 1
        asyncio.run(run_case(envelope))
        data = json.loads(envelope.read_text(encoding="utf-8"))
        assert data["status"] == "FAILED"
        assert data["attempts"] == 2

        rc.record({"assistant_response": "交接状态\n" + "y" * 5000,
                   "session_id": "callback-longtext-session", "task": "交接状态"})
        longtext_envelope = next(Path(tmp).glob("*_callback-longtext-session.json"))
        text_source = Path(tmp) / "callback-longtext.txt"
        text_source.write_text("不是 PDF", encoding="utf-8")
        result = asyncio.run(run_longtext_case(longtext_envelope, str(text_source)))
        assert result["guard_status"] == "ADVISORY"
        assert result["preflight"]["table_line_count"] == 0
        assert result["received_preflight"] is False
        data = json.loads(longtext_envelope.read_text(encoding="utf-8"))
        assert data["status"] == rc.COMPLETED_STATUS
        assert data["distilled_result"]["gpt_context"]["capsule"]["summary"] == ["保真摘要"]

        path_only = Path(tmp) / "path-only-longtext.txt"
        path_only.write_text("路径模式的可复用状态。", encoding="utf-8")
        original = qxen._qxen_generate

        async def fake_process(**kwargs):
            return {"runtime": "QXEN-CD", "task": "qxen_longtext_distill",
                    "guard_status": "ADVISORY", "requires_gpt_review": False,
                    "review_policy": "conditional",
                    "gpt_context": {"capsule": {"summary": ["路径摘要"]}}}

        qxen._qxen_generate = fake_process
        try:
            path_result = asyncio.run(qxen.qxen_cd_longtext_distill(
                source=path_only.name, source_path=str(path_only),
                session_id="path-only-session",
            ))
        finally:
            qxen._qxen_generate = original
        assert path_result["input_mode"] == "local_path"
        assert path_result["raw_pointer"] == str(path_only.resolve())
        assert path_result["source_locator"]["sha256"]
        assert "compact_state" not in path_result
        explicit_state = asyncio.run(qxen.qxen_cd_compact(
            records=[path_result], task_id="path-only-explicit-compact",
        ))
        compacted = explicit_state["accepted_capsules"][0]
        assert compacted["raw_pointer"] == str(path_only.resolve())
        assert compacted["consumption_policy"]["never_claim_full_source_replacement"] is True
        source_slice = qxen.qxen_cd_source_slice(
            str(path_only), path_result["source_locator"]["sha256"], query="可复用",
        )
        assert source_slice["status"] == "OK"
        assert "可复用状态" in source_slice["text"]
        mismatch = qxen.qxen_cd_source_slice(str(path_only), "bad-hash", start_line=1)
        assert mismatch["fallback_reason"] == "source_hash_mismatch"
        rc.QUEUE, rc.P1_LOG = old_queue, old_log
        qxen.AUDIT_LOG = old_audit_log
    print("test_qxen_capsule_callback: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
