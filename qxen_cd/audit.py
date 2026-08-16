"""Deterministic, auditable QXEN-CD usage ledger."""
from __future__ import annotations

import json
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ORIGINS = {"user", "upstream_agent", "system_required", "qxen_cd_generated", "audit_only"}
BASELINE_MODES = {"direct_gpt", "existing_pipeline", "none", "unknown"}
OUTCOMES = {"success", "fail", "fallback", "unknown"}
PIPELINES = {"process", "ingest", "compact", "bootstrap", "audit_assistant", "unknown"}


def estimate_tokens(chars: int | None) -> int | None:
    """Approximate tokens as ceil(chars / 4); the result is explicitly approximate."""
    return None if chars is None else max(0, math.ceil(int(chars) / 4))


def append(path: str | Path, event: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"event_id": str(uuid.uuid4()),
              "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def register_work_item(path: str | Path, work_item_id: str, title: str, *,
                       origin: str = "upstream_agent", baseline_required: bool | None = True,
                       baseline_mode: str = "unknown") -> None:
    if not work_item_id.strip():
        raise ValueError("work_item_id is required")
    if origin not in ORIGINS:
        raise ValueError(f"invalid origin: {origin}")
    if baseline_mode not in BASELINE_MODES:
        raise ValueError(f"invalid baseline_mode: {baseline_mode}")
    append(path, {"event_type": "work_item_registered", "unit_type": "business_work_item",
                  "work_item_id": work_item_id, "title": title[:240], "origin": origin,
                  "baseline_required": baseline_required, "baseline_mode": baseline_mode,
                  "count_as_business_task": True})


def record_processing(path: str | Path, *, work_item_id: str = "", task: str = "",
                      pipeline: str = "process", baseline_scope: str = "evidence",
                      source_chars: int = 0, qxen_output_chars: int = 0,
                      capsule_id: str = "", overhead_chars: int = 0,
                      guard_status: str = "", fallback: bool = False) -> None:
    if pipeline not in PIPELINES:
        raise ValueError(f"invalid pipeline: {pipeline}")
    append(path, {"event_type": "qxen_processing", "unit_type": "processing_event",
                  "work_item_id": work_item_id, "task": task, "pipeline": pipeline,
                  "baseline_scope": baseline_scope, "source_chars": source_chars,
                  "source_tokens_est": estimate_tokens(source_chars),
                  "qxen_output_chars": qxen_output_chars,
                  "qxen_output_tokens_est": estimate_tokens(qxen_output_chars),
                  "capsule_id": capsule_id, "overhead_chars": overhead_chars,
                  "guard_status": guard_status, "fallback": fallback,
                  "count_as_business_task": False})


def record_usage(path: str | Path, work_item_id: str, usage_id: str,
                 baseline_gpt_tokens: int | None, qxen_gpt_tokens: int | None,
                 gpt_review_tokens: int | None = None,
                 fallback_replay_gpt_tokens: int | None = None,
                 outcome: str = "success", estimated: bool = False, *,
                 source_chars: int | None = None, payload_chars: int | None = None,
                 pipeline: str = "process", baseline_scope: str = "evidence",
                 capsule_id: str = "") -> None:
    if not usage_id.strip():
        raise ValueError("usage_id is required")
    if outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome}")
    if pipeline not in PIPELINES:
        raise ValueError(f"invalid pipeline: {pipeline}")
    append(path, {"event_type": "usage_observation", "unit_type": "usage_observation",
                  "work_item_id": work_item_id, "usage_id": usage_id,
                  "baseline_mode": "direct_gpt", "baseline_gpt_tokens": baseline_gpt_tokens,
                  "qxen_gpt_tokens": qxen_gpt_tokens, "qxen_local_tokens": 0,
                  "gpt_review_tokens": gpt_review_tokens,
                  "fallback_replay_gpt_tokens": fallback_replay_gpt_tokens,
                  "outcome": outcome, "source_chars": source_chars,
                  "payload_chars": payload_chars, "payload_tokens_est": estimate_tokens(payload_chars),
                  "pipeline": pipeline, "baseline_scope": baseline_scope,
                  "capsule_id": capsule_id, "estimated": estimated,
                  "count_as_business_task": False})


def record_capsule_use(path: str | Path, capsule_id: str, work_item_id: str,
                       *, used_by: str = "gpt", outcome: str = "success") -> None:
    if not capsule_id.strip() or not work_item_id.strip():
        raise ValueError("capsule_id and work_item_id are required")
    if outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome}")
    append(path, {"event_type": "capsule_use", "unit_type": "utilization_observation",
                  "capsule_id": capsule_id, "work_item_id": work_item_id,
                  "used_by": used_by, "outcome": outcome, "count_as_business_task": False})


def load(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize paired savings without inventing missing payload measurements."""
    registered = {r.get("work_item_id"): r for r in rows
                  if r.get("event_type") == "work_item_registered" and r.get("work_item_id")}
    usage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    processing = [r for r in rows if r.get("event_type") == "qxen_processing"]
    seen_usage: set[str] = set()
    duplicate_usage = 0
    for row in rows:
        key = row.get("work_item_id")
        if row.get("event_type") != "usage_observation" or not key:
            continue
        usage_id = row.get("usage_id")
        if not usage_id or usage_id in seen_usage:
            duplicate_usage += 1
            continue
        seen_usage.add(usage_id)
        usage[key].append(row)

    categories = {"baseline_required": [], "system_required": [], "qxen_added": [], "unknown": []}
    for key, item in registered.items():
        if item.get("baseline_required") is True:
            categories["baseline_required"].append(key)
        elif item.get("origin") in {"qxen_cd_generated", "audit_only"} or item.get("baseline_required") is False:
            categories["qxen_added"].append(key)
        elif item.get("origin") == "system_required" or item.get("baseline_required") is None:
            categories["system_required"].append(key)
        else:
            categories["unknown"].append(key)

    baseline = qxen = review = replay = 0.0
    raw_chars = payload_chars = payload_observations = pairs = successful = 0
    confirmed_rows = []
    used_capsules = {r.get("capsule_id") for r in rows if r.get("event_type") == "capsule_use"}
    for key in categories["baseline_required"]:
        comparable = [r for r in usage.get(key, [])
                      if isinstance(r.get("baseline_gpt_tokens"), (int, float))
                      and isinstance(r.get("qxen_gpt_tokens"), (int, float))
                      and (r.get("pipeline") or "process") == "process"]
        if not comparable:
            continue
        row = comparable[0]
        baseline += float(row["baseline_gpt_tokens"])
        qxen += float(row["qxen_gpt_tokens"])
        review += float(row.get("gpt_review_tokens") or 0)
        replay += float(row.get("fallback_replay_gpt_tokens") or 0)
        raw_chars += int(row.get("source_chars") or 0)
        if isinstance(row.get("payload_chars"), (int, float)):
            payload_chars += int(row["payload_chars"])
            payload_observations += 1
        pairs += 1
        successful += row.get("outcome") == "success"
        if row.get("capsule_id") in used_capsules and row.get("capsule_id"):
            confirmed_rows.append(row)

    by_pipeline: dict[str, dict[str, int]] = {}
    for event in processing:
        pipe = str(event.get("pipeline") or "unknown")
        bucket = by_pipeline.setdefault(pipe, {"events": 0, "source_chars": 0,
                                                "qxen_output_chars": 0, "overhead_chars": 0,
                                                "fallback_events": 0})
        bucket["events"] += 1
        bucket["source_chars"] += int(event.get("source_chars") or 0)
        bucket["qxen_output_chars"] += int(event.get("qxen_output_chars") or 0)
        bucket["overhead_chars"] += int(event.get("overhead_chars") or 0)
        bucket["fallback_events"] += bool(event.get("fallback"))

    net = baseline - qxen - review - replay
    confirmed_baseline = sum(float(r.get("baseline_gpt_tokens") or 0) for r in confirmed_rows)
    confirmed_net = confirmed_baseline - sum(float(r.get("qxen_gpt_tokens") or 0) for r in confirmed_rows)
    confirmed_net -= sum(float(r.get("gpt_review_tokens") or 0) for r in confirmed_rows)
    confirmed_net -= sum(float(r.get("fallback_replay_gpt_tokens") or 0) for r in confirmed_rows)
    accepted = {r.get("capsule_id") for r in processing if r.get("capsule_id")}
    return {
        "schema_version": "qxen_cd_audit_v2",
        "business_work_items": len(registered),
        "business_work_items_by_category": {k: len(v) for k, v in categories.items()},
        "qxen_processing_events": len(processing),
        "fallback_events": sum(bool(r.get("fallback")) for r in processing),
        "utilization": {"accepted_capsule_count_observed": len(accepted),
                         "accepted_capsule_use_count_observed": len(accepted & used_capsules),
                         "accepted_capsule_utilization": (len(accepted & used_capsules) / len(accepted)
                                                           if accepted else None)},
        "comparable_usage_pairs": pairs, "successful_usage_pairs": successful,
        "token_accounting": {
            "baseline_gpt_tokens": baseline, "qxen_gpt_tokens": qxen,
            "gpt_review_tokens": review, "fallback_replay_gpt_tokens": replay,
            "gross_gpt_tokens_saved": baseline - qxen, "net_gpt_tokens_saved": net,
            "saving_rate": net / baseline if baseline else None,
            "raw_source_chars": raw_chars,
            "qxen_payload_chars": payload_chars if payload_observations == pairs else None,
            "chars_avoided": raw_chars - payload_chars if payload_observations == pairs else None,
            "compression_rate": ((raw_chars - payload_chars) / raw_chars
                                  if raw_chars and payload_observations == pairs else None),
            "payload_chars_observations": payload_observations,
            "confirmed_capsule_use_pairs": len(confirmed_rows),
            "actual_used_net_gpt_tokens_saved": confirmed_net if confirmed_rows else None,
            "actual_used_saving_rate": confirmed_net / confirmed_baseline if confirmed_baseline else None,
        },
        "pipeline_accounting": {"by_pipeline": by_pipeline, "business_saving_pipeline": "process",
                                 "ingest_compact_excluded_from_business_saving": True,
                                 "bootstrap_system_only": True, "audit_assistant_excluded": True},
        "data_quality": {"duplicate_usage_rows_ignored": duplicate_usage,
                          "qxen_added_excluded_from_savings": len(categories["qxen_added"])},
        "report_status": "descriptive_only_need_50_pairs" if pairs < 50 else "eligible_for_inference",
    }
