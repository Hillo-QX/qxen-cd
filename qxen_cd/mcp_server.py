"""Optional MCP adapter for the dependency-free QXEN-CD core.

Install the optional ``mcp`` extra to expose ``health``, ``guard`` and
``compact``. Model loading is intentionally not part of this public adapter;
connect any approved provider through the guard boundary.
"""
from __future__ import annotations

import json

from .compact import compact
from .guard import guard_v1

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only without extra
    raise SystemExit("Install qxen-cd[mcp] to run the MCP adapter") from exc

mcp = FastMCP("qxen-cd")


@mcp.tool()
def health() -> dict:
    return {"status": "OK", "server": "qxen-cd", "mode": "deterministic-core"}


@mcp.tool()
def guard(raw_model_output: str, prompt: str) -> dict:
    return guard_v1(raw_model_output, prompt)


@mcp.tool()
def compact_context(records: list[dict], state: dict | None = None,
                    max_items: int = 64, max_chars: int = 24000) -> dict:
    return compact(records, state, max_items, max_chars)


if __name__ == "__main__":
    mcp.run()
