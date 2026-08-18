#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline MCP discovery and deterministic tool tests for global QXEN-CD."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from qxen_cd_mcp import split_longtext_chunks  # noqa: E402

SERVER = ROOT / "scripts" / "qxen_cd_mcp.py"
PYTHON = ROOT / "venv" / "bin" / "python"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)


async def call(name: str, args: dict | None = None):
    params = StdioServerParameters(command=str(PYTHON), args=[str(SERVER)])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool(name, args or {})
            text = "".join(item.text for item in result.content if hasattr(item, "text"))
            return json.loads(text)


async def main() -> int:
    text = "x" * 24754
    chunks = split_longtext_chunks(text, 6000)
    assert len(chunks) == 5
    assert max(map(len, chunks)) <= 6000
    assert "".join(chunks) == text

    params = StdioServerParameters(command=str(PYTHON), args=[str(SERVER)])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            expected = {"qxen_cd_health", "qxen_cd_bootstrap", "qxen_cd_capabilities", "qxen_cd_route", "qxen_cd_detection_tasks", "qxen_cd_detection_plan", "qxen_cd_longtext_distill", "qxen_cd_source_slice", "qxen_cd_compact", "qxen_cd_audit_register", "qxen_cd_audit_usage", "qxen_cd_audit_capsule_use", "qxen_cd_audit_summary", "qxen_cd_audit_local_qwen", "qxen_cd_audit_distill", "qxen_cd_audit_failure_extract", "qxen_cd_audit_cluster", "qxen_cd_audit_classify"}
            assert expected <= names, names
            assert "qxen_cd_process" not in names
            assert "qxen_cd_ingest" not in names
            health = await session.call_tool("qxen_cd_health", {})
            h = json.loads("".join(item.text for item in health.content if hasattr(item, "text")))
            assert h["server"] == "qxen-cd"
            plan = await session.call_tool("qxen_cd_detection_plan", {
                "task_ids": ["detect_data_quality", "detect_runtime_health"],
                "target": "sample.csv", "read_only": True,
            })
            p = json.loads("".join(item.text for item in plan.content if hasattr(item, "text")))
            assert p["status"] == "OK" and p["read_only"] is True
            rejected = await session.call_tool("qxen_cd_detection_plan", {
                "task_ids": ["not_a_detection_task"], "target": "sample.csv", "read_only": True,
            })
            rej = json.loads("".join(item.text for item in rejected.content if hasattr(item, "text")))
            assert rej["status"] == "FALLBACK"
            compact_result = await session.call_tool("qxen_cd_compact", {
                "records": [{"guard_status": "FALLBACK", "fallback_reason": "parse_error",
                             "source": "doc/a", "raw_model_output": "broken"}],
                "task_id": "MCP-SMOKE",
            })
            c = json.loads("".join(item.text for item in compact_result.content if hasattr(item, "text")))
            assert c["status"] == "OK" and c["requires_gpt_review"] is True
    print("QXEN-CD MCP smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
