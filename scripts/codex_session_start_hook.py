#!/usr/bin/env python3
"""Adapt SessionStart bootstrap output to Codex's JSON hook protocol."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOOTSTRAP = ROOT / "session_bootstrap.py"


def main() -> int:
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--hook", "--force"],
        input=sys.stdin.read(), text=True, capture_output=True, check=False,
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": result.stdout.strip(),
        }
    }
    print(json.dumps(output, ensure_ascii=False), flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
