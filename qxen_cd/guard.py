"""Deterministic guard and fallback for Evidence Capsule v1.

The language model is a proposer. This module decides whether its JSON can
enter stable agent context. Rejected output is preserved for human/main-agent
review and is never treated as a semantic answer.
"""
from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any

ALLOWED_STATUS = {"CURRENT", "STALE", "SUPERSEDED"}
ALLOWED_RELEVANCE = {"high", "medium", "low"}
ALLOWED_SUFFICIENCY = {"sufficient", "insufficient"}
ALLOWED_SOURCE_TYPES = {
    "data_file", "config", "report", "code", "model_weights", "log",
    "doc", "env_check", "other",
}


def parse_first_json(text: str) -> dict[str, Any] | None:
    """Parse the first balanced JSON object, tolerating surrounding text."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    quoted = escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : pos + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _source_lines(prompt: str) -> list[str]:
    return [match.group(1) for line in prompt.splitlines()
            if (match := re.match(r"^\s*来源\s*[：:]\s*(.+?)\s*$", line))]


def source_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).strip().lower()
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    return re.sub(r"\s+", "", value)


def source_base_key(value: str) -> str:
    """Remove only explicit page-citation suffixes from a source name."""
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    normalized = re.sub(r"\s*[（(]\s*\d+\s*页\s*[）)]\s*", " ", normalized,
                        flags=re.IGNORECASE)
    normalized = re.sub(r"\s*(?:第\s*)?\d+(?:\s*[-–—]\s*\d+)?\s*页\s*$",
                        "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*(?:pages?|页码)\s*[:：]\s*.*$", "", normalized,
                        flags=re.IGNORECASE)
    normalized = re.sub(r"\s*[:：]\s*$", "", normalized)
    return source_key(normalized)


def match_source(candidate: str, allowed_sources: list[str]) -> tuple[str | None, str]:
    exact = {source_key(value): value for value in allowed_sources}
    canonical = exact.get(source_key(candidate))
    if canonical is not None:
        return canonical, "exact" if candidate == canonical else "canonicalized"
    by_base = {source_base_key(value): value for value in allowed_sources}
    canonical = by_base.get(source_base_key(candidate))
    return (canonical, "page_suffix_canonicalized") if canonical else (None, "unmatched")


def source_similarity_candidates(candidate: str, allowed_sources: list[str], limit: int = 3) -> list[dict[str, Any]]:
    """Return review-only candidates; never used to accept a capsule."""
    candidate_key = source_key(candidate)
    ranked = sorted(
        ((SequenceMatcher(None, candidate_key, source_key(value)).ratio(), value)
         for value in allowed_sources), reverse=True,
    )
    return [{"source": value, "similarity": round(score, 4)}
            for score, value in ranked[:limit] if score >= 0.72]


def safe_fallback(raw: str, prompt: str, reason: str) -> dict[str, Any]:
    sources = _source_lines(prompt)
    capsule = {
        "relevance": None,
        "key_evidence": [],
        "sufficiency": "uncertain",
        "next_step": "GPT_REVIEW",
        "uncertainty": ["model_output_rejected:" + reason],
        "operative_status": None,
        "provenance": "guard_fallback",
    }
    return {
        "guard_status": "FALLBACK",
        "fallback_reason": reason,
        "source_match": "unavailable",
        "capsule": capsule,
        "gpt_context": {
            "context_mode": "GPT_REVIEW",
            "guard_status": "FALLBACK",
            "fallback_reason": reason,
            "capsule": capsule,
            "source": sources[0] if sources else None,
            "raw_model_output": raw,
            "preserve_original": True,
        },
    }


def guard_v1(raw: str, prompt: str) -> dict[str, Any]:
    """Validate model JSON, normalize harmless source formatting, or fallback."""
    capsule = parse_first_json(raw)
    if capsule is None:
        return safe_fallback(raw, prompt, "parse_error_or_truncation")
    evidence = capsule.get("key_evidence")
    if not isinstance(evidence, list) or not evidence:
        return safe_fallback(raw, prompt, "key_evidence_missing_or_invalid")
    required = ("capsule_id", "source_type", "relevance", "sufficiency")
    missing = [field for field in required if field not in capsule]
    if missing:
        return safe_fallback(raw, prompt, "required_field_missing:" + ",".join(missing))
    if not isinstance(capsule.get("capsule_id"), str) or not capsule["capsule_id"].strip():
        return safe_fallback(raw, prompt, "invalid_capsule_id")
    if capsule.get("source_type") not in ALLOWED_SOURCE_TYPES:
        return safe_fallback(raw, prompt, "illegal_source_type:" + str(capsule.get("source_type")))

    status = capsule.get("operative_status")
    if isinstance(status, str):
        capsule["operative_status"] = status.strip().upper()
        status = capsule["operative_status"]
    if status is not None and status not in ALLOWED_STATUS:
        return safe_fallback(raw, prompt, "illegal_operative_status:" + str(status))
    if capsule.get("relevance") not in ALLOWED_RELEVANCE:
        return safe_fallback(raw, prompt, "illegal_relevance:" + str(capsule.get("relevance")))
    if capsule.get("sufficiency") not in ALLOWED_SUFFICIENCY:
        return safe_fallback(raw, prompt, "illegal_sufficiency:" + str(capsule.get("sufficiency")))

    allowed_sources = _source_lines(prompt)
    if not allowed_sources:
        return safe_fallback(raw, prompt, "evidence_material_missing")
    fixed = deepcopy(capsule)
    canonicalized = 0
    match_types = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            return safe_fallback(raw, prompt, "key_evidence_item_invalid")
        candidate = item.get("source")
        if not isinstance(candidate, str):
            return safe_fallback(raw, prompt, "key_evidence_source_missing_or_invalid")
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
            fixed["key_evidence"][index]["source"] = canonical
            canonicalized += 1
        match_types.add(match_type)

    return {
        "guard_status": "ACCEPT",
        "capsule": fixed,
        "source_canonicalized": canonicalized,
        "source_match": ("page_suffix_canonicalized" if "page_suffix_canonicalized" in match_types
                          else "canonicalized" if "canonicalized" in match_types else "exact"),
        "preserve_original": True,
        "gpt_context": {"context_mode": "CAPSULE", "guard_status": "ACCEPT",
                        "capsule": fixed, "source_canonicalized": canonicalized,
                        "source_match": ("page_suffix_canonicalized" if "page_suffix_canonicalized" in match_types
                                          else "canonicalized" if "canonicalized" in match_types else "exact")},
    }
