#!/usr/bin/env python3
"""Summarize deterministic P1 trigger telemetry for an observation window."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "日志" / "p1_trigger_events.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))
    events = []
    if LOG.is_file():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                when = datetime.fromisoformat(item["time"])
                if when >= since:
                    events.append(item)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    triggered = [e for e in events if e.get("triggered")]
    print(json.dumps({
        "window_days": args.days,
        "log": str(LOG),
        "events": len(events),
        "triggered": len(triggered),
        "trigger_rate": round(len(triggered) / len(events), 4) if events else None,
        "by_reason": dict(Counter(e.get("reason", "unknown") for e in events)),
        "by_trigger": dict(Counter(e.get("reason", "unknown") for e in triggered)),
        "sessions": len({e.get("session_id") for e in events if e.get("session_id")}),
        "max_pressure": max((float(e.get("pressure", 0)) for e in events), default=0.0),
        "max_observed_tokens": max((int(e.get("observed_tokens", 0)) for e in events), default=0),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
