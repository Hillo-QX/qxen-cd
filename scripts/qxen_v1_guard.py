#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic runtime guard for the QXEN Evidence Capsule v1 adapter.

The model is only a proposer. This module decides whether its JSON may enter
the main-agent context. Rejected output is never treated as a semantic answer.
"""
from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher

ALLOWED_STATUS = {"CURRENT", "STALE", "SUPERSEDED"}
# Known model-language aliases only. Unknown values remain hard failures.
OPERATIVE_STATUS_ALIASES = {
    "PROVISIONAL": "CURRENT",
    "SUCCEEDED": "CURRENT",
}
ALLOWED_RELEVANCE = {"high", "medium", "low"}
ALLOWED_SUFFICIENCY = {"sufficient", "insufficient"}


def parse_first_json(text: str):
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    quoted = False
    escaped = False
    for pos in range(start, len(text)):
        ch = text[pos]
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : pos + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _source_lines(prompt: str) -> list[str]:
    values = []
    for line in prompt.splitlines():
        match = re.match(r"^\s*来源\s*[：:]\s*(.+?)\s*$", line)
        if match:
            values.append(match.group(1))
    return values


def source_key(value: str) -> str:
    """Comparison key for harmless path formatting differences only."""
    value = unicodedata.normalize("NFKC", str(value)).strip().lower()
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    value = re.sub(r"\s+", "", value)
    return value


def source_base_key(value: str) -> str:
    """Normalize a source citation while preserving the underlying filename.

    Model citations often append page ranges, for example ``（61 页）：第 1-10
    页``.  That suffix is metadata about the citation, not a new source.  Only
    explicit page markers are removed; arbitrary trailing text remains strict.
    """
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    normalized = re.sub(r"\s*[（(]\s*\d+\s*页\s*[）)]\s*", " ", normalized,
                        flags=re.IGNORECASE)
    normalized = re.sub(r"\s*(?:第\s*)?\d+(?:\s*[-–—]\s*\d+)?\s*页\s*$",
                        "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*[:：]\s*(?:第\s*)?\d+(?:\s*[-–—]\s*\d+)?\s*页\s*$",
                        "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*(?:pages?|页码)\s*[:：]\s*.*$", "", normalized,
                        flags=re.IGNORECASE)
    normalized = re.sub(r"\s*[:：]\s*$", "", normalized)
    return source_key(normalized)


def match_source(candidate: str, allowed_sources: list[str]) -> tuple[str | None, str]:
    """Return (canonical source, match type) without widening source authority."""
    exact = {source_key(value): value for value in allowed_sources}
    canonical = exact.get(source_key(candidate))
    if canonical is not None:
        return canonical, "exact" if candidate == canonical else "canonicalized"
    base = {source_base_key(value): value for value in allowed_sources}
    canonical = base.get(source_base_key(candidate))
    if canonical is not None:
        return canonical, "page_suffix_canonicalized"
    return None, "unmatched"


def source_similarity_candidates(candidate: str, allowed_sources: list[str], limit: int = 3) -> list[dict]:
    """Return review-only candidates; never used to accept a capsule."""
    candidate_key = source_key(candidate)
    ranked = sorted(
        ((SequenceMatcher(None, candidate_key, source_key(value)).ratio(), value)
         for value in allowed_sources), reverse=True,
    )
    return [{"source": value, "similarity": round(score, 4)}
            for score, value in ranked[:limit] if score >= 0.72]


def _status(capsule: dict):
    if "operative_status" in capsule:
        return capsule.get("operative_status"), "operative_status"
    profiles = capsule.get("profiles")
    if isinstance(profiles, dict) and "operative_status" in profiles:
        return profiles.get("operative_status"), "profiles.operative_status"
    return None, None


def _set_source(capsule: dict, index: int, canonical: str) -> None:
    evidence = capsule["key_evidence"][index]
    evidence["source"] = canonical


def safe_fallback(raw: str, prompt: str, reason: str) -> dict:
    """Return a non-semantic envelope for GPT review."""
    sources = _source_lines(prompt)
    result = {
        "guard_status": "FALLBACK",
        "fallback_reason": reason,
        "capsule": {
            "relevance": None,
            "key_evidence": [],
            "sufficiency": "uncertain",
            "next_step": "GPT_REVIEW",
            "uncertainty": ["model_output_rejected:" + reason],
            "operative_status": None,
            "provenance": "guard_fallback",
        },
        "source": sources[0] if sources else None,
        "preserve_original": True,
        "raw_model_output": raw,
        "source_match": "unavailable",
    }
    result["gpt_context"] = {
        "context_mode": "GPT_REVIEW",
        "guard_status": "FALLBACK",
        "fallback_reason": reason,
        "capsule": result["capsule"],
        "source": result["source"],
        # Full raw is retained; no arbitrary 1500-character truncation.
        "raw_model_output": raw,
        "preserve_original": True,
    }
    return result


def guard_v1(raw: str, prompt: str) -> dict:
    capsule = parse_first_json(raw)
    if capsule is None:
        return safe_fallback(raw, prompt, "parse_error_or_truncation")

    evidence = capsule.get("key_evidence")
    if not isinstance(evidence, list):
        # key_evidence is optional for advisory/long-text outputs. Keep an empty
        # list for the source loop; absence must not trigger a hard fallback.
        evidence = []

    status, status_field = _status(capsule)
    if isinstance(status, str):
        # 先做大小写/空白归一化，再仅映射已知同义词；未知值仍硬失败。
        normalized = status.strip().upper()
        alias = OPERATIVE_STATUS_ALIASES.get(normalized)
        if alias is not None:
            normalized = alias
            capsule.setdefault("guard_repairs", []).append({
                "field": status_field,
                "from": status,
                "to": normalized,
                "kind": "operative_status_alias",
            })
        if normalized != status:
            if status_field == "operative_status":
                capsule["operative_status"] = normalized
            elif status_field == "profiles.operative_status":
                capsule["profiles"]["operative_status"] = normalized
            status = normalized
    # operative_status is advisory-only; it must not block an otherwise valid evidence capsule.
    # GPT owns the final interpretation and may review unknown values.
    relevance = capsule.get("relevance")
    if relevance is not None and relevance not in ALLOWED_RELEVANCE:
        return safe_fallback(raw, prompt, "illegal_relevance:" + str(relevance))
    sufficiency = capsule.get("sufficiency")
    if sufficiency is not None and sufficiency not in ALLOWED_SUFFICIENCY:
        return safe_fallback(raw, prompt, "illegal_sufficiency:" + str(sufficiency))

    allowed_sources = _source_lines(prompt)
    if not allowed_sources:
        return safe_fallback(raw, prompt, "evidence_material_missing")
    fixed = deepcopy(capsule)
    canonicalized = 0
    match_types = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or not isinstance(item.get("source"), str):
            return safe_fallback(raw, prompt, "key_evidence_source_missing_or_invalid")
        candidate = item["source"]
        canonical, match_type = match_source(candidate, allowed_sources)
        if canonical is None:
            result = safe_fallback(raw, prompt, "source_not_in_evidence_material")
            candidates = source_similarity_candidates(candidate, allowed_sources)
            if candidates:
                result["fallback_reason"] = "source_similarity_candidate"
                result["source_match"] = "similarity_candidate"
                result["source_candidates"] = candidates
                result["gpt_context"]["fallback_reason"] = result["fallback_reason"]
                result["gpt_context"]["source_candidates"] = candidates
            return result
        if candidate != canonical:
            _set_source(fixed, index, canonical)
            canonicalized += 1
        match_types.add(match_type)

    result = {
        "guard_status": "ACCEPT",
        "capsule": fixed,
        "status_field": status_field,
        "source_canonicalized": canonicalized,
        "source_match": ("page_suffix_canonicalized" if "page_suffix_canonicalized" in match_types
                         else "canonicalized" if "canonicalized" in match_types
                         else "exact"),
        "preserve_original": True,
    }
    # The GPT path receives the complete validated compression capsule. The
    # raw model text is intentionally omitted on ACCEPT to save context tokens.
    result["gpt_context"] = {
        "context_mode": "CAPSULE",
        "guard_status": "ACCEPT",
        "capsule": fixed,
        "source_canonicalized": canonicalized,
        "source_match": result["source_match"],
    }
    return result


def guard_text(raw: str, prompt: str) -> dict:
    """Alias used by runtime integrations."""
    return guard_v1(raw, prompt)
