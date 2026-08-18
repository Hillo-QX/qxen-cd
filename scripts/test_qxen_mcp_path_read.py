#!/usr/bin/env python3
"""Black-box proof that QXEN MCP reads source_path outside the caller context."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "venv" / "bin" / "python"
SERVER = ROOT / "qxen_cd_mcp.py"
SOURCE = ROOT / "configs" / "qxen_cd_runtime_contract.md"


async def main() -> int:
    request = {
        "source": SOURCE.name,
        "source_path": str(SOURCE),
        "max_tokens": 500,
        "task_id": "MCP-PATH-READ-PROBE",
        "workspace": str(ROOT),
    }
    params = StdioServerParameters(command=str(PYTHON), args=[str(SERVER)])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            schema = tools["qxen_cd_longtext_distill"].inputSchema
            result = await session.call_tool("qxen_cd_longtext_distill", request)
            payload = json.loads("".join(
                item.text for item in result.content if hasattr(item, "text")
            ))
            compact_result = await session.call_tool("qxen_cd_compact", {
                "records": [payload],
                "task_id": "MCP-PATH-READ-PROBE-COMPACT",
                "workspace": str(ROOT),
            })
            compact_payload = json.loads("".join(
                item.text for item in compact_result.content if hasattr(item, "text")
            ))
            slice_result = await session.call_tool("qxen_cd_source_slice", {
                "raw_pointer": str(SOURCE),
                "expected_sha256": payload.get("source_locator", {}).get("sha256", ""),
                "query": "QXEN-CD",
                "context_lines": 1,
                "max_chars": 500,
                "work_item_id": "MCP-PATH-READ-PROBE",
                "workspace": str(ROOT),
            })
            slice_payload = json.loads("".join(
                item.text for item in slice_result.content if hasattr(item, "text")
            ))
            audit_result = await session.call_tool("qxen_cd_audit_summary", {
                "workspace": str(ROOT),
            })
            audit_payload = json.loads("".join(
                item.text for item in audit_result.content if hasattr(item, "text")
            ))

    expected_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    locator = payload.get("source_locator", {})
    proof = {
        "request_keys": sorted(request),
        "evidence_chars_sent": len(str(request.get("evidence", ""))),
        "schema_has_source_path": "source_path" in schema.get("properties", {}),
        "old_public_tools_present": sorted(
            {"qxen_cd_process", "qxen_cd_ingest"} & set(tools)
        ),
        "guard_status": payload.get("guard_status"),
        "raw_pointer": payload.get("raw_pointer"),
        "source_locator_path": locator.get("path"),
        "source_hash_matches": locator.get("sha256") == expected_hash,
        "server_reported_bytes": locator.get("bytes"),
        "server_reported_content_chars": locator.get("content_chars"),
        "chunking": payload.get("chunking"),
        "embedded_compact_state": "compact_state" in payload,
        "has_gpt_context_payload": bool(payload.get("gpt_context_payload")),
        "context_burden": payload.get("context_burden", {}),
        "accepted_capsule_count": payload.get("accepted_capsule_count"),
        "explicit_compact_status": compact_payload.get("status"),
        "source_slice_status": slice_payload.get("status"),
        "source_slice_chars": len(slice_payload.get("text", "")),
        "observable_path_accounting": audit_payload.get("observable_path_accounting", {}),
    }
    print(json.dumps(proof, ensure_ascii=False, indent=2))
    assert proof["evidence_chars_sent"] == 0
    assert proof["schema_has_source_path"] is True
    assert proof["old_public_tools_present"] == []
    assert proof["raw_pointer"] == str(SOURCE.resolve())
    assert proof["source_locator_path"] == str(SOURCE.resolve())
    assert proof["source_hash_matches"] is True
    assert int(proof["server_reported_content_chars"] or 0) > 0
    assert proof["embedded_compact_state"] is False
    assert proof["context_burden"]["decision"] in {"INJECT_QXEN", "BYPASS_QXEN"}
    if proof["context_burden"]["decision"] == "INJECT_QXEN":
        assert proof["has_gpt_context_payload"] is True
        assert proof["context_burden"]["ratio"] < 1
        assert int(proof["accepted_capsule_count"] or 0) > 0
    else:
        assert proof["has_gpt_context_payload"] is False
        assert proof["context_burden"]["ratio"] == 1.0
        assert int(proof["accepted_capsule_count"] or 0) == 0
    assert proof["explicit_compact_status"] == "OK"
    assert proof["source_slice_status"] == "OK"
    assert proof["observable_path_accounting"]["reread_events"] >= 1
    assert proof["observable_path_accounting"]["net_avoided_chars"] >= 0
    print("test_qxen_mcp_path_read: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
