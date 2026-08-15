"""Deterministic rolling-context compaction.

Only accepted capsules enter stable state. Fallback records remain pending
review. This module deduplicates and enforces a budget; it makes no semantic
decision about authority, validity, conflict, or action.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA_VERSION = "qxen_cd_context_v1"


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _unwrap(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    if record.get("guard_status") == "ACCEPT":
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
        if status != "ACCEPT" or capsule is None:
            pending.append({"reason": record.get("fallback_reason", "requires_gpt_review"),
                            "source": record.get("source"), "raw_preserved": True})
            continue
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
    current.update(targets)
    while len(json.dumps(current, ensure_ascii=False)) > max(1000, max_chars) and current["accepted_capsules"]:
        current["accepted_capsules"].pop(0)
        dropped["over_budget_capsules"] = dropped.get("over_budget_capsules", 0) + 1
    current["dropped_summary"] = dropped
    return current
