"""Dependency-free command line interface for the deterministic core."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compact import compact
from .guard import guard_v1


def main() -> int:
    parser = argparse.ArgumentParser(prog="qxen-cd")
    sub = parser.add_subparsers(dest="command", required=True)
    guard = sub.add_parser("guard", help="validate one model JSON response")
    guard.add_argument("--prompt", required=True)
    guard.add_argument("--raw", required=True, help="raw model output or @path")
    compact_parser = sub.add_parser("compact", help="merge guarded records")
    compact_parser.add_argument("--records", required=True, help="JSON/JSONL path")
    compact_parser.add_argument("--state")
    compact_parser.add_argument("--output", required=True)
    compact_parser.add_argument("--max-items", type=int, default=64)
    compact_parser.add_argument("--max-chars", type=int, default=24000)
    args = parser.parse_args()

    if args.command == "guard":
        raw = Path(args.raw[1:]).read_text(encoding="utf-8") if args.raw.startswith("@") else args.raw
        print(json.dumps(guard_v1(raw, args.prompt), ensure_ascii=False, indent=2))
        return 0

    path = Path(args.records)
    text = path.read_text(encoding="utf-8").strip()
    records = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
    state = json.loads(Path(args.state).read_text(encoding="utf-8")) if args.state else None
    result = compact(records, state, args.max_items, args.max_chars)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "OK", "accepted": len(result["accepted_capsules"]),
                      "pending_gpt_review": len(result["pending_gpt_review"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
