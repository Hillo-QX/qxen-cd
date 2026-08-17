"""Deterministic rolling-context compaction.

Accepted/advisory capsules enter stable state. Longtext fallback records become
pointer-only degraded capsules; high-risk fallbacks remain pending review.
This module never decides authority, validity, conflict, or action.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = "qxen_cd_context_v1"


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_ref(record: dict[str, Any]) -> str | None:
    value = record.get("source") or record.get("raw_pointer")
    return " ".join(str(value).split()) if value else None


def _degraded_longtext(record: dict[str, Any]) -> dict[str, Any] | None:
    task = str(record.get("task") or record.get("task_id") or "")
    if "longtext" not in task and "faithful_chunk" not in task and record.get("review_policy") != "conditional":
        return None
    source = _source_ref(record)
    return {"relevance": "uncertain", "sufficiency": "uncertain", "summary": [],
            "source": source, "raw_pointer": record.get("raw_pointer") or source,
            "provenance": "qxen_longtext_fallback_pointer", "advisory_only": True,
            "review_policy": "conditional",
            "uncertainty": [record.get("fallback_reason", "longtext_distill_failed")]}


def _unwrap(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    if record.get("guard_status") in {"ACCEPT", "ADVISORY"}:
        context = record.get("gpt_context") or {}
        capsule = context.get("capsule") if isinstance(context, dict) else None
        return ("ACCEPT", capsule) if isinstance(capsule, dict) else ("FALLBACK", None)
    if record.get("guard_status") == "FALLBACK" or record.get("requires_gpt_review"):
        return "FALLBACK", None
    return ("ACCEPT", record) if isinstance(record.get("key_evidence"), list) else ("FALLBACK", None)


def compact(records: list[dict[str, Any]], state: dict[str, Any] | None = None,
            max_items: int = 64, max_chars: int = 24000) -> dict[str, Any]:
    """Merge guarded records into a bounded, auditable rolling state."""
    current = dict(state or {})
    current.setdefault("schema_version", SCHEMA_VERSION)
    for key, default in (("accepted_capsules", []), ("pending_gpt_review", []),
                         ("verbatim_evidence", []), ("timeline", []),
                         ("candidate_conflicts", []), ("uncertainties", []),
                         ("dropped_summary", {})):
        current.setdefault(key, default)
    accepted = list(current["accepted_capsules"])
    pending = list(current["pending_gpt_review"])
    dropped = dict(current["dropped_summary"])
    seen = {_hash(item) for item in accepted}
    for record in records:
        status, capsule = _unwrap(record)
        degraded = _degraded_longtext(record) if capsule is None else None
        if degraded is not None:
            key = _hash(degraded)
            if key not in seen:
                seen.add(key); accepted.append(degraded)
            else:
                dropped["duplicate_capsules"] = dropped.get("duplicate_capsules", 0) + 1
            dropped["longtext_fallback_pointers"] = dropped.get("longtext_fallback_pointers", 0) + 1
            continue
        if status != "ACCEPT" or capsule is None:
            pending.append({"reason": record.get("fallback_reason", "requires_gpt_review"),
                            "source": record.get("source"), "raw_preserved": True})
            continue
        capsule.setdefault("source", _source_ref(record))
        key = _hash(capsule)
        if key in seen:
            dropped["duplicate_capsules"] = dropped.get("duplicate_capsules", 0) + 1
            continue
        seen.add(key)
        accepted.append(capsule)
    current["accepted_capsules"] = accepted[-max(1, max_items):]
    current["pending_gpt_review"] = pending[-max(1, max_items):]

    targets = {"verbatim_evidence": [], "timeline": [],
               "candidate_conflicts": [], "uncertainties": []}
    seen_targets = {key: set() for key in targets}
    for capsule in current["accepted_capsules"]:
        for item in capsule.get("key_evidence", []):
            if isinstance(item, dict) and item.get("preserve_verbatim"):
                key = _hash({"text": item.get("text"), "source": item.get("source")})
                if key not in seen_targets["verbatim_evidence"]:
                    seen_targets["verbatim_evidence"].add(key)
                    targets["verbatim_evidence"].append(item)
        for field, target in (("timeline", "timeline"), ("conflicts", "candidate_conflicts"),
                              ("uncertainty", "uncertainties")):
            values = capsule.get(field, [])
            values = values if isinstance(values, list) else [values]
            for value in values:
                key = _hash(value)
                if key not in seen_targets[target]:
                    seen_targets[target].add(key)
                    targets[target].append(value)
    evidence_sources = {}
    for capsule in current["accepted_capsules"]:
        for item in capsule.get("key_evidence", []):
            if isinstance(item, dict) and item.get("text"):
                evidence_sources.setdefault(str(item["text"]).strip(), set()).add(str(item.get("source") or ""))
    for text, sources in evidence_sources.items():
        if len(sources) > 1:
            targets["candidate_conflicts"].append({"type": "same_evidence_multiple_sources",
                                                    "text": text, "sources": sorted(sources),
                                                    "advisory_only": True})
    current.update(targets)
    while len(json.dumps(current, ensure_ascii=False)) > max(1000, max_chars) and current["accepted_capsules"]:
        current["accepted_capsules"].pop(0)
        dropped["over_budget_capsules"] = dropped.get("over_budget_capsules", 0) + 1
    current["dropped_summary"] = dropped
    return current
