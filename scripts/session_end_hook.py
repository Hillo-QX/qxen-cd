#!/usr/bin/env python3
"""SessionEnd adapter: queue an explicit Codex response, then audit."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
RESPONSE = ROOT / "scripts" / "response_capsule.py"
BOOTSTRAP = ROOT / "scripts" / "session_bootstrap.py"


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
    except (OSError, json.JSONDecodeError):
        raw, payload = "{}", {}
    if not _has_response(payload):
        sys.path.insert(0, str(ROOT / "scripts"))
        import response_capsule

        extracted = response_capsule.extract_final_response(str(payload.get("transcript_path", "")))
        if extracted:
            payload["assistant_response"] = extracted
            raw = json.dumps(payload, ensure_ascii=False)
    route = subprocess.run([PYTHON, str(RESPONSE)], input=json.dumps(payload, ensure_ascii=False), text=True, capture_output=True, check=False)
    audit = subprocess.run([PYTHON, str(BOOTSTRAP), "--audit-check"], input=raw, text=True, capture_output=True, check=False)
    context = (route.stdout + audit.stdout).strip()
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionEnd",
        "additionalContext": context,
    }}, ensure_ascii=False), flush=True)
    sys.stderr.write(route.stderr + audit.stderr)
    return audit.returncode or route.returncode


def _has_response(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(isinstance(payload.get(key), str) and payload.get(key).strip() for key in (
        "assistant_response", "last_assistant_response", "response_text", "codex_response", "assistant_output",
    ))


if __name__ == "__main__":
    raise SystemExit(main())
