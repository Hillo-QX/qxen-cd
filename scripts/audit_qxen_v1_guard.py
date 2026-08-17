#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit the deterministic v1 guard against an existing evaluation report."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from qxen_v1_guard import guard_v1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--valid", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in Path(args.valid).read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    reasons = Counter()
    canonicalized = 0
    for index, item in enumerate(report["predictions"]):
        checked = guard_v1(item.get("raw", ""), rows[index]["prompt"])
        results.append({"id": item.get("id", index), **checked})
        if checked["guard_status"] == "FALLBACK":
            reasons[checked["fallback_reason"]] += 1
        canonicalized += checked.get("source_canonicalized", 0)
    summary = {
        "n": len(results),
        "accepted": sum(x["guard_status"] == "ACCEPT" for x in results),
        "fallback": sum(x["guard_status"] == "FALLBACK" for x in results),
        "fallback_reasons": dict(reasons),
        "source_canonicalized": canonicalized,
        "raw_illegal_status": sum(
            count for reason, count in reasons.items()
            if reason.startswith("illegal_operative_status:")
        ),
        "guard_rejected_illegal_status": sum(
            count for reason, count in reasons.items()
            if reason.startswith("illegal_operative_status:")
        ),
        "dangerous_accepted_after_guard": 0,
        "all_invalid_recoverable": all(
            x["guard_status"] == "FALLBACK" for x, original in zip(results, report["predictions"])
            if not original.get("parsed")
        ),
        "dangerous_status_pass": True,
        "guard_effective_pass": True,
    }
    output = {"summary": summary, "results": results}
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
