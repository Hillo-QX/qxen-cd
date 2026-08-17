#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the global QXEN bootstrap before a Codex CLI session.

The hook writes a small, auditable state capsule and never forwards raw
workspace material to Codex. A bootstrap failure is recorded and does not
prevent the real CLI from starting.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".codex" / "state"
STATE_PATH = STATE_DIR / "last_bootstrap.json"
sys.path.insert(0, str(ROOT))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(workspace: str) -> dict:
    try:
        from qxen_cd_mcp import qxen_cd_bootstrap
        result = asyncio.run(qxen_cd_bootstrap(workspace=workspace, max_files=8))
        return {"status": result.get("status", "ERROR"), "result": result}
    except Exception as exc:  # startup must remain non-blocking
        return {"status": "FALLBACK", "reason": f"bootstrap_error:{exc}"}


def main() -> int:
    workspace = str(Path.cwd().resolve())
    started = time.time()
    payload = {
        "schema": "codex_startup_bootstrap_v1",
        "started_at": now(),
        "workspace": workspace,
        "pid": os.getpid(),
    }
    payload.update(run(workspace))
    payload["elapsed_s"] = round(time.time() - started, 2)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except OSError as exc:
        payload["state_write_error"] = str(exc)
    # Keep stdout compact; raw summaries remain only in the auditable state file.
    print(json.dumps({"status": payload.get("status"),
                      "workspace": workspace,
                      "state": str(STATE_PATH),
                      "elapsed_s": payload["elapsed_s"]}, ensure_ascii=False),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
