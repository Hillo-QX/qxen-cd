#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate the controlled R3A Gate test dataset without model inference."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/r3/r3a_gate_test"
REQUIRED = {
    "anchor_id", "candidate_path", "observed_at", "published_at", "effective_from",
    "archived_at", "superseded_at", "superseded_by", "as_of", "operative_status",
    "events", "source_refs", "date_provenance", "gate_mode", "completion",
}


def d(value: str) -> date:
    return date.fromisoformat(value)


def load(name: str) -> list[dict]:
    return [json.loads(x) for x in (DATA / name).read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    errors: list[str] = []
    timeline = load("timeline.jsonl")
    if len(timeline) != 72:
        errors.append(f"timeline rows={len(timeline)}, expected 72")
    for row in timeline:
        missing = REQUIRED - row.keys()
        if missing:
            errors.append(f"{row.get('record_id')}: missing {sorted(missing)}")
            continue
        published, effective = d(row["published_at"]), d(row["effective_from"])
        archived, superseded, as_of = d(row["archived_at"]), d(row["superseded_at"]), d(row["as_of"])
        if not published <= effective <= archived <= superseded:
            errors.append(f"{row['record_id']}: invalid event order")
        expected = "CURRENT" if as_of < archived else "STALE" if as_of < superseded else "SUPERSEDED"
        if row["operative_status"] != expected:
            errors.append(f"{row['record_id']}: status={row['operative_status']} expected={expected}")
        if row["gate_mode"] != "controlled_synthetic" or row["date_provenance"] != "controlled_synthetic":
            errors.append(f"{row['record_id']}: provenance marker missing")

    split_sets = {}
    for name in ("train", "valid", "fresh"):
        rows = load(f"{name}.jsonl")
        for row in rows:
            if not row.get("prompt"):
                errors.append(f"{name}/{row.get('record_id')}: missing prompt")
        split_sets[name] = {x["anchor_id"] for x in rows}
        if not rows:
            errors.append(f"{name}: empty")
    if split_sets["train"] & split_sets["valid"] or split_sets["train"] & split_sets["fresh"] or split_sets["valid"] & split_sets["fresh"]:
        errors.append("anchor leakage across splits")
    if errors:
        print(json.dumps({"status": "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "status": "PASS",
        "timeline_rows": len(timeline),
        "splits": {name: len(load(f"{name}.jsonl")) for name in ("train", "valid", "fresh")},
        "anchor_isolation": True,
        "date_order": True,
        "forward_label_calculation": True,
        "gate_mode": "controlled_synthetic",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
