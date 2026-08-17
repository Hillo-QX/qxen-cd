#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adapt force_distill.py (Codex/Continue hook protocol) to Kimi Code hooks.

Kimi hook protocol differences (docs: customization/hooks):
  - stdin payload uses snake_case fields (tool_name / tool_input / session_id /
    cwd) — force_distill.py already reads these, so the payload is passed
    through unchanged.
  - Context injection: plain stdout text is appended to context; the Codex-style
    {"hookSpecificOutput": {"additionalContext": ...}} JSON wrapper is NOT
    unpacked by Kimi, so this adapter extracts the capsule and prints it bare.
  - Blocking: exit code 2 with the reason on stderr (or a hookSpecificOutput
    JSON with permissionDecision="deny"). This adapter translates a deny into
    exit 2 + stderr.
  - Kimi has no updatedInput rewrite; when force_distill returns a rewritten
    safe_run command (STRICT_MODE only), the adapter denies instead, telling the
    agent to run the rewritten command itself.

Default fail-open: any internal error exits 0 with no output.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
FORCE_DISTILL = ROOT / "scripts" / "force_distill.py"


def main() -> int:
    payload = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        result = subprocess.run(
            [sys.executable, str(FORCE_DISTILL)],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0  # fail-open

    raw = result.stdout.strip()
    if not raw:
        return 0  # 守卫未触发，保持静默
    try:
        hook = json.loads(raw.splitlines()[-1]).get("hookSpecificOutput", {})
    except json.JSONDecodeError:
        return 0  # 输出异常，fail-open

    capsule = hook.get("additionalContext") or ""
    decision = hook.get("permissionDecision")
    reason = hook.get("permissionDecisionReason") or ""

    if hook.get("updatedInput"):
        # Kimi 不支持改写 tool_input；转为阻断并提示改写后的命令
        rewritten = hook["updatedInput"].get("command") or ""
        if capsule:
            print(capsule, file=sys.stderr)
        print(f"{reason or '命令须先经 safe_run 落盘截断'}。"
              f"请改用手动执行: {rewritten}", file=sys.stderr)
        return 2

    if decision == "deny":
        if capsule:
            print(capsule, file=sys.stderr)
        if reason:
            print(reason, file=sys.stderr)
        return 2

    if capsule:
        print(capsule, flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
