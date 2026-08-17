#!/usr/bin/env python3
"""Regression tests for the model-free QXEN-CD Guard path."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from qxen_v1_guard import guard_text  # noqa: E402


def capsule(status: str) -> str:
    return json.dumps({
        "key_evidence": [{"text": "verbatim evidence", "source": "doc.md"}],
        "operative_status": status,
        "sufficiency": "sufficient",
        "next_step": "review",
    }, ensure_ascii=False)


def main() -> int:
    cases = {
        "provisional": "illegal_operative_status:PROVISIONAL",
        "succeeded": "illegal_operative_status:SUCCEEDED",
    }
    for status, expected in cases.items():
        result = guard_text(capsule(status), "来源: doc.md\nverbatim evidence")
        actual = result.get("fallback_reason")
        if result.get("guard_status") != "FALLBACK" or actual != expected:
            raise SystemExit(f"FAIL status={status}: {result}")
        print(f"PASS {status}: {actual}")

    accepted = guard_text(capsule("CURRENT"), "来源: doc.md\nverbatim evidence")
    if accepted.get("guard_status") not in ("ACCEPT", "GPT_REVIEW"):
        raise SystemExit(f"FAIL CURRENT: {accepted}")
    print(f"PASS CURRENT: {accepted.get('guard_status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
