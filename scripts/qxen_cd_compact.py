#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic QXEN-CD rolling-context compactor.

ACCEPT and longtext ADVISORY capsules enter stable state. Longtext FALLBACK
records receive a pointer-only degraded capsule; high-risk FALLBACK records
remain pending GPT review. This module does not make semantic decisions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "qxen_cd_context_v1"


def _source_ref(record: dict[str, Any]) -> str | None:
    source = record.get("source") or record.get("raw_pointer")
    if not source:
        return None
    return " ".join(str(source).split())


def _degraded_longtext(record: dict[str, Any]) -> dict[str, Any] | None:
    if record.get("guard_status") == "BYPASS" or record.get("status") == "BYPASS_QXEN":
        return None
    task = str(record.get("task") or record.get("task_id") or "")
    if "longtext" not in task and "faithful_chunk" not in task and record.get("review_policy") != "conditional":
        return None
    source = _source_ref(record)
    return {
        "relevance": "uncertain",
        "sufficiency": "uncertain",
        "summary": [],
        "source": source,
        "raw_pointer": record.get("raw_pointer") or source,
        "provenance": "qxen_longtext_fallback_pointer",
        "advisory_only": True,
        "review_policy": "conditional",
        "uncertainty": [record.get("fallback_reason", "longtext_distill_failed")],
    }


def _read_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        value = json.loads(text)
        return [x for x in value if isinstance(x, dict)]
    records = []
    for line in text.splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
    return records


def _state(path: Path | None, task_id: str, as_of: str) -> dict[str, Any]:
    if path and path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.setdefault("accepted_capsules", [])
            value.setdefault("verbatim_evidence", [])
            value.setdefault("timeline", [])
            value.setdefault("candidate_conflicts", [])
            value.setdefault("uncertainties", [])
            value.setdefault("pending_gpt_review", [])
            value.setdefault("dropped_summary", {})
            return value
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "as_of": as_of,
        "accepted_capsules": [],
        "verbatim_evidence": [],
        "timeline": [],
        "candidate_conflicts": [],
        "uncertainties": [],
        "pending_gpt_review": [],
        "dropped_summary": {},
    }


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _unwrap(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    status = record.get("guard_status")
    if status == "ACCEPT":
        context = record.get("gpt_context", {})
        capsule = context.get("capsule") if isinstance(context, dict) else None
        return "ACCEPT", capsule if isinstance(capsule, dict) else None
    if status == "ADVISORY":
        context = record.get("gpt_context", {})
        capsule = context.get("capsule") if isinstance(context, dict) else None
        if isinstance(capsule, dict):
            capsule = dict(capsule)
            capsule.setdefault("source", _source_ref(record))
            capsule.setdefault("raw_preserved", bool(record.get("raw_preserved") or record.get("raw_model_output")))
            return "ADVISORY", capsule
    if status == "FALLBACK" or record.get("requires_gpt_review"):
        return "FALLBACK", None
    if status == "BYPASS" or record.get("status") == "BYPASS_QXEN":
        return "BYPASS", None
    # Accept direct guarded records for deterministic unit tests/integrations.
    return ("ACCEPT", record) if isinstance(record.get("key_evidence"), list) else ("FALLBACK", None)


def compact(records: list[dict[str, Any]], state: dict[str, Any], max_items: int = 64,
            max_chars: int = 24000) -> dict[str, Any]:
    expanded_records: list[dict[str, Any]] = []
    for record in records:
        payload = record.get("gpt_context_payload")
        capsules = payload.get("capsules") if isinstance(payload, dict) else None
        if record.get("guard_status") == "ADVISORY" and isinstance(capsules, list) and capsules:
            for capsule in capsules:
                if not isinstance(capsule, dict):
                    continue
                clone = dict(record)
                clone["gpt_context"] = {"context_mode": "ADVISORY_ONLY", "capsule": capsule}
                clone["source"] = capsule.get("source") or record.get("source") or record.get("raw_pointer")
                expanded_records.append(clone)
            continue
        expanded_records.append(record)
    records = expanded_records
    accepted = list(state.get("accepted_capsules", []))
    pending = list(state.get("pending_gpt_review", []))
    dropped = dict(state.get("dropped_summary", {}))
    seen_capsules = {_hash(x) for x in accepted}

    for record in records:
        status, capsule = _unwrap(record)
        if status in {"ACCEPT", "ADVISORY"} and capsule is not None:
            capsule.setdefault("source", record.get("source"))
            capsule.setdefault("provenance", "qxen_longtext_distill" if status == "ADVISORY" else "guard_accept")
            if record.get("raw_pointer"):
                capsule.setdefault("raw_pointer", record.get("raw_pointer"))
            if isinstance(record.get("source_locator"), dict):
                capsule.setdefault("source_locator", record.get("source_locator"))
            if isinstance(record.get("consumption_policy"), dict):
                capsule.setdefault("consumption_policy", record.get("consumption_policy"))
            if status == "ADVISORY":
                capsule["review_policy"] = "conditional"
            digest = _hash(capsule)
            if digest not in seen_capsules:
                accepted.append(capsule)
                seen_capsules.add(digest)
            else:
                dropped["duplicate_capsules"] = dropped.get("duplicate_capsules", 0) + 1
            continue
        degraded = _degraded_longtext(record) if capsule is None else None
        if degraded is not None:
            digest = _hash(degraded)
            if digest not in seen_capsules:
                accepted.append(degraded)
                seen_capsules.add(digest)
            else:
                dropped["duplicate_capsules"] = dropped.get("duplicate_capsules", 0) + 1
            dropped["longtext_fallback_pointers"] = dropped.get("longtext_fallback_pointers", 0) + 1
            continue
        if status == "BYPASS":
            dropped["context_burden_bypass"] = dropped.get("context_burden_bypass", 0) + 1
            continue
        if status != "ACCEPT" or capsule is None:
            pending.append({
                "reason": record.get("fallback_reason", "requires_gpt_review"),
                "source": record.get("source"),
                "raw_preserved": bool(record.get("raw_model_output") or record.get("raw_preserved")),
            })
            continue
        key = _hash(capsule)
        if key in seen_capsules:
            dropped["duplicate_capsules"] = dropped.get("duplicate_capsules", 0) + 1
            continue
        seen_capsules.add(key)
        accepted.append(capsule)

    # Stable ordering is deterministic and newest records are retained under the cap.
    accepted = accepted[-max_items:]
    state["accepted_capsules"] = accepted
    state["pending_gpt_review"] = pending[-max_items:]

    verbatim = []
    timelines = []
    conflicts = []
    uncertainties = []
    seen_evidence = set()
    evidence_sources: dict[str, set[str]] = {}
    seen_timeline = set()
    seen_conflicts = set()
    seen_uncertainties = set()
    for capsule in accepted:
        for item in capsule.get("key_evidence", []):
            if not isinstance(item, dict) or not item.get("preserve_verbatim"):
                continue
            item_key = _hash({"text": item.get("text"), "source": item.get("source")})
            if item_key not in seen_evidence:
                seen_evidence.add(item_key)
                verbatim.append(item)
            text_key = str(item.get("text") or "").strip()
            if text_key:
                evidence_sources.setdefault(text_key, set()).add(str(item.get("source") or ""))
        for field, target, seen in (("timeline", timelines, seen_timeline),
                                    ("conflicts", conflicts, seen_conflicts),
                                    ("uncertainty", uncertainties, seen_uncertainties)):
            values = capsule.get(field, [])
            if not isinstance(values, list):
                values = [values]
            for value in values:
                key = _hash(value)
                if key not in seen:
                    seen.add(key)
                    target.append(value)

    state["verbatim_evidence"] = verbatim
    state["timeline"] = timelines
    state["candidate_conflicts"] = conflicts
    state["uncertainties"] = uncertainties
    for text_key, sources in evidence_sources.items():
        if len(sources) > 1:
            conflicts.append({"type": "same_evidence_multiple_sources", "text": text_key,
                              "sources": sorted(sources), "advisory_only": True})
    state["candidate_conflicts"] = conflicts
    state["dropped_summary"] = dropped

    # Budget is applied to the stable JSON, dropping whole low-priority arrays first.
    while len(json.dumps(state, ensure_ascii=False)) > max_chars and state["accepted_capsules"]:
        state["accepted_capsules"].pop(0)
        dropped["over_budget_capsules"] = dropped.get("over_budget_capsules", 0) + 1
    state["dropped_summary"] = dropped
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="QXEN-CD rolling context compact")
    ap.add_argument("--input", required=True, help="guarded JSONL/JSON records")
    ap.add_argument("--output", required=True)
    ap.add_argument("--state")
    ap.add_argument("--task-id", default="")
    ap.add_argument("--as-of", default="")
    ap.add_argument("--max-items", type=int, default=64)
    ap.add_argument("--max-chars", type=int, default=24000)
    args = ap.parse_args()
    state = _state(Path(args.state) if args.state else None, args.task_id, args.as_of)
    result = compact(_read_records(Path(args.input)), state, args.max_items, args.max_chars)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "OK", "accepted": len(result["accepted_capsules"]),
                      "pending_gpt_review": len(result["pending_gpt_review"]),
                      "dropped": result["dropped_summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
