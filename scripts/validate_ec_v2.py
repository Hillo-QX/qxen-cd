#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate EC v2 structure and report semantic-label coverage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("schema_version", "task_type", "relevance", "key_evidence", "sufficiency",
            "uncertainty", "next_step", "assessed_fields", "profiles")
PROFILE = ("timeline", "relations", "conflicts", "operative_status", "authority", "provenance")
ARRAY_FIELDS = {"key_evidence", "assessed_fields", "timeline", "relations", "conflicts"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="JSONL files to validate")
    args = ap.parse_args()
    overall = {"rows": 0, "schema_valid": 0, "type_valid": 0, "core_coverage": {k: 0 for k in REQUIRED}}
    errors = []
    for raw_path in args.paths:
        path = Path(raw_path)
        rows = schema_ok = type_ok = 0
        coverage = {k: 0 for k in REQUIRED}
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            rows += 1
            try:
                row = json.loads(line)
                obj = json.loads(row["completion"])
            except Exception as exc:
                errors.append({"file": str(path), "line": line_no, "error": str(exc)})
                continue
            missing = [key for key in REQUIRED if key not in obj]
            if not missing:
                schema_ok += 1
                for key in REQUIRED:
                    coverage[key] += 1
            types_ok = (
                isinstance(obj.get("key_evidence"), list)
                and isinstance(obj.get("assessed_fields"), list)
                and isinstance(obj.get("profiles"), dict)
                and all(key in obj["profiles"] for key in PROFILE)
                and all(isinstance(obj["profiles"][key], list) for key in ("timeline", "relations", "conflicts") if obj["profiles"][key] is not None)
            )
            if types_ok:
                type_ok += 1
            if missing or not types_ok:
                errors.append({"file": str(path), "line": line_no, "missing": missing, "type_valid": types_ok})
        print(json.dumps({"file": str(path), "rows": rows, "schema_valid": schema_ok,
                          "type_valid": type_ok, "coverage": coverage}, ensure_ascii=False))
        overall["rows"] += rows
        overall["schema_valid"] += schema_ok
        overall["type_valid"] += type_ok
        for key in REQUIRED:
            overall["core_coverage"][key] += coverage[key]
    report = {"overall": overall, "errors": errors[:20], "error_count": len(errors)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
