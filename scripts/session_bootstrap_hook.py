#!/usr/bin/env python3
"""Hook adapter: normalize Codex/Kimi hook payload before bootstrap filtering."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "session_bootstrap.py"
RESPONSE_CAPSULE = ROOT / "scripts" / "response_capsule.py"
import response_capsule
from response_capsule import estimate_context_pressure


def first_value(obj, keys):
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in obj.values():
            found = first_value(value, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = first_value(value, keys)
            if found:
                return found
    return ""


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
    except (OSError, json.JSONDecodeError):
        payload = {}
    session_id = first_value(payload, ("session_id", "sessionId", "conversation_id")) or "unknown"
    explicit_pressure = next((payload[key] for key in ("context_pressure", "contextPressure", "pressure") if key in payload), None)
    if explicit_pressure is None:
        pressure = estimate_context_pressure(session_id, first_value(payload, ("transcript_path", "transcriptPath")))
    else:
        try:
            pressure = {"pressure": max(0.0, min(float(explicit_pressure), 1.0)),
                        "observed_tokens": 0, "limit_tokens": 0, "source": "hook_payload"}
        except (TypeError, ValueError):
            pressure = estimate_context_pressure(session_id, first_value(payload, ("transcript_path", "transcriptPath")))
    normalized = {
        "cwd": first_value(payload, ("cwd", "working_directory", "workspace")) or os.getcwd(),
        "session_id": session_id,
        "task": first_value(payload, ("task", "task_type", "user_prompt", "prompt", "content")),
        "task_id": first_value(payload, ("task_id", "taskId", "work_item_id", "workItemId")),
        "context_pressure": pressure["pressure"],
        "context_pressure_observed_tokens": pressure["observed_tokens"],
        "context_pressure_limit_tokens": pressure["limit_tokens"],
        "context_pressure_source": pressure["source"],
        "target_workspace": first_value(payload, ("target_workspace", "targetWorkspace")),
        "transcript_path": first_value(payload, ("transcript_path", "transcriptPath")),
    }
    transcript = response_capsule.locate_codex_transcript(session_id, normalized["transcript_path"])
    captured = response_capsule.extract_final_response(str(transcript or ""))
    capture_output = ""
    if captured:
        capture_payload = dict(normalized)
        capture_payload["assistant_response"] = captured
        capture = subprocess.run(
            [sys.executable, str(RESPONSE_CAPSULE)], input=json.dumps(capture_payload, ensure_ascii=False),
            text=True, capture_output=True, check=False,
        )
        if "status=PENDING_QXEN" in capture.stdout or "status=DEDUP" in capture.stdout:
            capture_output = capture.stdout
    proc = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--hook"],
        input=json.dumps(normalized, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    pending = subprocess.run(
        [sys.executable, str(RESPONSE_CAPSULE), "--pending"],
        input=json.dumps(normalized, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    sys.stdout.write(proc.stdout)
    sys.stdout.write(capture_output)
    sys.stdout.write(pending.stdout)
    sys.stderr.write(proc.stderr)
    sys.stderr.write(pending.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
