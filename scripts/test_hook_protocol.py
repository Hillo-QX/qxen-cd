#!/usr/bin/env python3
"""Smoke-test that every globally registered Codex hook returns JSON."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(script: str, payload: dict, allowed_returncodes=(0,)) -> dict:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / script)],
                          input=json.dumps(payload), text=True, capture_output=True, check=False)
    assert proc.returncode in allowed_returncodes, (script, proc.returncode, proc.stderr)
    return json.loads(proc.stdout)


def main() -> int:
    base = {"cwd": str(ROOT), "session_id": "hook-protocol-test", "transcript_path": "/missing"}
    assert run("codex_session_start_hook.py", base)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    prompt = dict(base, user_prompt="短测试")
    assert run("codex_session_bootstrap_hook.py", prompt)["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    pre = dict(base, tool_name="Read", tool_input={"path": "AGENTS.md"})
    assert run("force_distill.py", pre)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    long_read = dict(
        base,
        tool_name="Bash",
        tool_input={"command": "sed -n '1,240p' /tmp/SKILL.md"},
    )
    denied = run("force_distill.py", long_read, allowed_returncodes=(2,))
    denied_output = denied["hookSpecificOutput"]
    assert denied_output["permissionDecision"] == "deny"
    assert "qxen_cd_longtext_distill" in denied_output["permissionDecisionReason"]
    assert run("session_end_hook.py", base)["hookSpecificOutput"]["hookEventName"] == "SessionEnd"
    print("test_hook_protocol: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
