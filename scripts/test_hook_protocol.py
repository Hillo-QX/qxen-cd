#!/usr/bin/env python3
"""Smoke-test that every globally registered Codex hook returns JSON."""
from __future__ import annotations

import json
import subprocess
import tempfile
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
    with tempfile.TemporaryDirectory() as tmp:
        attachment = Path(tmp) / ".codex" / "attachments" / "pasted-text.txt"
        attachment.parent.mkdir(parents=True)
        attachment.write_text("x" * 2001, encoding="utf-8")
        structured = dict(base, user_prompt=f"请分析 {attachment}")
        context = __import__("session_bootstrap_hook").attachment_distill_context(structured)
        assert "必须先走 QXEN" in context
        targeted = dict(base, user_prompt=f"只读取 {attachment} 第 10 行")
        targeted_context = __import__("session_bootstrap_hook").attachment_distill_context(targeted)
        assert "允许确定性局部回源" in targeted_context
    structured = dict(
        base,
        user_prompt="核对 DOCX 文档描述是否与代码一致",
        tool_name="Bash",
        tool_input={"command": "sed -n '1,240p' /tmp/report.md"},
    )
    structured_output = run("force_distill.py", structured)
    assert "permissionDecision" not in structured_output["hookSpecificOutput"]
    assert "结构化文档核对路径" in structured_output["hookSpecificOutput"]["additionalContext"]
    assert run("session_end_hook.py", base)["hookSpecificOutput"]["hookEventName"] == "SessionEnd"
    print("test_hook_protocol: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
