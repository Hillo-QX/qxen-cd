#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert the clean EC v1 data into an explicit, non-fabricating EC v2 schema.

This is a deterministic data transform. It never invents semantic labels:
missing fields become null/[] and are not added to assessed_fields.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/r3/ec_v1/data1000/clean_train_format"

CORE = ("relevance", "key_evidence", "sufficiency", "uncertainty", "next_step")
PROFILE = ("timeline", "relations", "conflicts", "operative_status", "authority", "provenance")
ARRAY_CORE = {"key_evidence", "uncertainty"}
ARRAY_PROFILE = {"timeline", "relations", "conflicts"}

V2_INSTRUCTION = (
    "输出 ec_v2 JSON。必须包含 schema_version、task_type、relevance、key_evidence、"
    "sufficiency、uncertainty、next_step、assessed_fields、profiles。"
    "缺失或未评估的字段用 null；已评估但没有条目才用空数组 []。"
    "assessed_fields 只列出材料确实支持并实际评估的字段。"
    "profiles 固定包含 timeline、relations、conflicts、operative_status、authority、provenance；"
    "没有证据的 profile 字段用 null。只输出 JSON，不输出 Markdown、解释或推理过程。"
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def update_prompt(prompt: str) -> str:
    # Remove the old variable-field instruction so v1 and v2 contracts do not conflict.
    base = re.split(r"\n输出字段包括", prompt, maxsplit=1)[0]
    return base + "\n" + V2_INSTRUCTION


def normalise_value(key: str, value):
    if key in ARRAY_CORE or key in ARRAY_PROFILE:
        return value if isinstance(value, list) else []
    return value


def convert(row: dict) -> tuple[dict, dict]:
    source = json.loads(row["completion"])
    assessed = [key for key in CORE + PROFILE if key in source]
    capsule = {
        "schema_version": "ec_v2",
        "task_type": row.get("task_type", "capsule"),
        "relevance": normalise_value("relevance", source.get("relevance")),
        "key_evidence": normalise_value("key_evidence", source.get("key_evidence", [])),
        "sufficiency": normalise_value("sufficiency", source.get("sufficiency")),
        "uncertainty": normalise_value("uncertainty", source["uncertainty"] if "uncertainty" in source else None),
        "next_step": source.get("next_step") if "next_step" in source else None,
        "assessed_fields": assessed,
        "profiles": {
            key: normalise_value(key, source[key]) if key in source else None
            for key in PROFILE
        },
    }
    out = dict(row)
    out["prompt"] = update_prompt(row["prompt"])
    out["completion"] = json.dumps(capsule, ensure_ascii=False)
    audit = {
        "source_keys": sorted(source),
        "assessed_fields": assessed,
        "missing_core": [key for key in CORE if key not in source],
        "missing_profile": [key for key in PROFILE if key not in source],
    }
    return out, audit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--out", default="data/r3/ec_v2")
    args = ap.parse_args()
    source_dir = Path(args.source)
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"source": {}, "outputs": {}, "files": {}}
    for split in ("train", "valid"):
        source_path = source_dir / f"{split}.jsonl"
        out_path = out_dir / f"{split}.jsonl"
        counters = {"rows": 0, "missing_core": Counter(), "missing_profile": Counter(), "assessed": Counter()}
        with source_path.open(encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as dst:
            for line in src:
                if not line.strip():
                    continue
                row, audit = convert(json.loads(line))
                dst.write(json.dumps(row, ensure_ascii=False) + "\n")
                counters["rows"] += 1
                counters["missing_core"].update(audit["missing_core"])
                counters["missing_profile"].update(audit["missing_profile"])
                counters["assessed"].update(audit["assessed_fields"])
        report["source"][split] = str(source_path)
        report["outputs"][split] = str(out_path)
        report["files"][split] = {
            "rows": counters["rows"],
            "sha256": sha256(out_path),
            "missing_core": dict(counters["missing_core"]),
            "missing_profile": dict(counters["missing_profile"]),
            "assessed": dict(counters["assessed"]),
        }
    (out_dir / "conversion_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["files"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
