#!/usr/bin/env python3
"""Adapt the shared bootstrap hook to Codex's JSON hook protocol."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHARED = ROOT / "session_bootstrap_hook.py"


def main() -> int:
    payload = sys.stdin.read()
    result = subprocess.run(
        [sys.executable, str(SHARED)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    context = result.stdout.strip()
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }
    print(json.dumps(output, ensure_ascii=False), flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
