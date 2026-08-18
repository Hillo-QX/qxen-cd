#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QXEN-CD continuous audit ledger.

The ledger separates business work items from QXEN processing events. A
capsule/compact call is never counted as a new business task by itself.
Token savings are reported only for explicitly registered baseline-required
work items with a comparable baseline observation; unknown values stay
unknown instead of being guessed.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "日志" / "qxen_cd_audit.jsonl"
DEFAULT_LOCAL_QWEN_LOG = ROOT / "日志" / "local_qwen.log"
ORIGINS = {"user", "upstream_agent", "system_required", "qxen_cd_generated", "audit_only"}
BASELINE_MODES = {"direct_gpt", "existing_pipeline", "none", "unknown"}
OUTCOMES = {"success", "fail", "fallback", "unknown"}
PIPELINES = {"process", "ingest", "longtext_internal_generate", "compact", "bootstrap",
             "audit_assistant", "unknown"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def estimate_tokens(chars: int | None) -> int | None:
    if chars is None:
        return None
    return max(0, math.ceil(int(chars) / 4))


def append_event(event: dict[str, Any], path: Path = DEFAULT_LOG) -> dict[str, Any]:
    record = {"event_id": str(uuid.uuid4()), "time": now(), **event}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def register_work_item(work_item_id: str, title: str, *, origin: str = "upstream_agent",
                       baseline_required: bool | None = True,
                       baseline_mode: str = "unknown", parent_task_id: str = "",
                       workspace: str = "", session_id: str = "",
                       path: Path = DEFAULT_LOG) -> dict[str, Any]:
    if origin not in ORIGINS:
        raise ValueError(f"invalid origin: {origin}")
    if baseline_mode not in BASELINE_MODES:
        raise ValueError(f"invalid baseline_mode: {baseline_mode}")
    return append_event({
        "event_type": "work_item_registered",
        "unit_type": "business_work_item",
        "work_item_id": work_item_id,
        "title": title[:240],
        "origin": origin,
        "baseline_required": baseline_required,
        "baseline_mode": baseline_mode,
        "parent_task_id": parent_task_id,
        "workspace": workspace,
        "session_id": session_id,
        "count_as_business_task": True,
    }, path)


def record_processing(*, work_item_id: str = "", task_id: str = "", task: str = "",
                      origin: str = "system_required", baseline_required: bool | None = None,
                      baseline_mode: str = "unknown", source_chars: int = 0,
                      qxen_output_chars: int = 0, gpt_review_tokens: int | None = None,
                      capsule_id: str = "", overhead_chars: int = 0,
                      guard_status: str = "", fallback: bool = False,
                      pipeline: str = "process", baseline_scope: str = "evidence",
                      workspace: str = "", session_id: str = "",
                      path: Path = DEFAULT_LOG) -> dict[str, Any]:
    if origin not in ORIGINS:
        raise ValueError(f"invalid origin: {origin}")
    if baseline_mode not in BASELINE_MODES:
        raise ValueError(f"invalid baseline_mode: {baseline_mode}")
    if pipeline not in PIPELINES:
        raise ValueError(f"invalid pipeline: {pipeline}")
    return append_event({
        "event_type": "qxen_processing",
        "unit_type": "processing_event",
        "work_item_id": work_item_id,
        "task_id": task_id,
        "task": task,
        "pipeline": pipeline,
        "baseline_scope": baseline_scope,
        "origin": origin,
        "baseline_required": baseline_required,
        "baseline_mode": baseline_mode,
        "source_chars": source_chars,
        "source_tokens_est": estimate_tokens(source_chars),
        "qxen_output_chars": qxen_output_chars,
        "qxen_output_tokens_est": estimate_tokens(qxen_output_chars),
        "capsule_id": capsule_id,
        "overhead_chars": overhead_chars,
        "gpt_review_tokens": gpt_review_tokens,
        "guard_status": guard_status,
        "fallback": fallback,
        "workspace": workspace,
        "session_id": session_id,
        "count_as_business_task": False,
    }, path)


def record_usage(work_item_id: str, usage_id: str, *, baseline_mode: str,
                 eval_window: str, outcome: str,
                 baseline_gpt_tokens: int | None = None,
                 qxen_gpt_tokens: int | None = None, qxen_local_tokens: int | None = 0,
                 gpt_review_tokens: int | None = None,
                 fallback_replay_gpt_tokens: int | None = None,
                 source_chars: int | None = None, payload_chars: int | None = None,
                 pipeline: str = "process", baseline_scope: str = "evidence",
                 capsule_id: str = "", estimated: bool = False,
                 workspace: str = "", session_id: str = "", note: str = "",
                 path: Path = DEFAULT_LOG) -> dict[str, Any]:
    if not usage_id.strip():
        raise ValueError("usage_id is required for idempotent accounting")
    if baseline_mode not in BASELINE_MODES:
        raise ValueError(f"invalid baseline_mode: {baseline_mode}")
    if not eval_window.strip():
        raise ValueError("eval_window is required")
    if outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome}")
    if pipeline not in PIPELINES:
        raise ValueError(f"invalid pipeline: {pipeline}")
    return append_event({
        "event_type": "usage_observation",
        "unit_type": "usage_observation",
        "work_item_id": work_item_id,
        "usage_id": usage_id,
        "pipeline": pipeline,
        "baseline_scope": baseline_scope,
        "baseline_mode": baseline_mode,
        "eval_window": eval_window,
        "outcome": outcome,
        "baseline_gpt_tokens": baseline_gpt_tokens,
        "qxen_gpt_tokens": qxen_gpt_tokens,
        "qxen_local_tokens": qxen_local_tokens,
        "gpt_review_tokens": gpt_review_tokens,
        "fallback_replay_gpt_tokens": fallback_replay_gpt_tokens,
        "source_chars": source_chars,
        "payload_chars": payload_chars,
        "payload_tokens_est": estimate_tokens(payload_chars),
        "capsule_id": capsule_id,
        "estimated": estimated,
        "workspace": workspace,
        "session_id": session_id,
        "note": note[:240],
        "count_as_business_task": False,
    }, path)


def record_capsule_use(capsule_id: str, work_item_id: str, *, used_by: str = "gpt",
                       outcome: str = "success", workspace: str = "",
                       session_id: str = "", path: Path = DEFAULT_LOG) -> dict[str, Any]:
    if not capsule_id.strip() or not work_item_id.strip():
        raise ValueError("capsule_id and work_item_id are required")
    if outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome}")
    return append_event({
        "event_type": "capsule_use",
        "unit_type": "utilization_observation",
        "capsule_id": capsule_id,
        "work_item_id": work_item_id,
        "used_by": used_by,
        "outcome": outcome,
        "workspace": workspace,
        "session_id": session_id,
        "count_as_business_task": False,
    }, path)


def record_path_distill(source_path: str, source_sha256: str, *, source_chars: int,
                        returned_chars: int, work_item_id: str = "", task_id: str = "",
                        capsule_id: str = "", workspace: str = "", session_id: str = "",
                        context_burden_ratio: float | None = None,
                        decision: str = "", accepted_capsules: int | None = None,
                        path: Path = DEFAULT_LOG) -> dict[str, Any]:
    """Record observable path input and final GPT context burden.

    ``returned_chars`` means the chars the main Agent should actually inject
    into GPT after QXEN, not the full MCP envelope or debug/audit metadata.
    """
    return append_event({
        "event_type": "path_distill_observation",
        "unit_type": "observable_context_accounting",
        "source_path": str(source_path),
        "source_sha256": str(source_sha256),
        "source_chars": max(0, int(source_chars)),
        "returned_chars": max(0, int(returned_chars)),
        "context_burden_ratio": context_burden_ratio,
        "decision": decision,
        "accepted_capsules": accepted_capsules,
        "work_item_id": work_item_id,
        "task_id": task_id,
        "capsule_id": capsule_id,
        "workspace": workspace,
        "session_id": session_id,
        "count_as_business_task": False,
    }, path)


def record_source_retrieval(source_path: str, source_sha256: str, *, returned_chars: int,
                            work_item_id: str = "", capsule_id: str = "",
                            workspace: str = "", session_id: str = "",
                            path: Path = DEFAULT_LOG) -> dict[str, Any]:
    """Record bounded original-text reread after capsule creation."""
    return append_event({
        "event_type": "source_retrieval_observation",
        "unit_type": "observable_context_accounting",
        "source_path": str(source_path),
        "source_sha256": str(source_sha256),
        "returned_chars": max(0, int(returned_chars)),
        "work_item_id": work_item_id,
        "capsule_id": capsule_id,
        "workspace": workspace,
        "session_id": session_id,
        "count_as_business_task": False,
    }, path)


def summarize_observable_paths(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Assign each source-slice reread to the latest prior distill of that path/hash."""
    observations: list[dict[str, Any]] = []
    latest: dict[tuple[str, str], int] = {}
    unmatched_retrievals = 0
    for row in rows:
        event_type = row.get("event_type")
        key = (str(row.get("source_path") or ""), str(row.get("source_sha256") or ""))
        if event_type == "path_distill_observation" and key[0]:
            observations.append({
                "source_path": key[0], "source_sha256": key[1],
                "source_chars": int(row.get("source_chars") or 0),
                "returned_chars": int(row.get("returned_chars") or 0),
                "reread_chars": 0, "reread_events": 0,
                "context_burden_ratio": row.get("context_burden_ratio"),
                "decision": row.get("decision", ""),
                "accepted_capsules": row.get("accepted_capsules"),
                "work_item_id": row.get("work_item_id", ""),
                "capsule_id": row.get("capsule_id", ""),
            })
            latest[key] = len(observations) - 1
        elif event_type == "source_retrieval_observation" and key[0]:
            index = latest.get(key)
            if index is None and not key[1]:
                candidates = [i for k, i in latest.items() if k[0] == key[0]]
                index = candidates[-1] if candidates else None
            if index is None:
                unmatched_retrievals += 1
                continue
            observations[index]["reread_chars"] += int(row.get("returned_chars") or 0)
            observations[index]["reread_events"] += 1

    for item in observations:
        item["net_avoided_chars"] = max(
            0, item["source_chars"] - item["returned_chars"] - item["reread_chars"])
        item["net_avoided_tokens_est"] = estimate_tokens(item["net_avoided_chars"])
        item["raw_reread_observed"] = item["reread_events"] > 0
        denominator = item["source_chars"] or 1
        item["observed_context_burden_ratio"] = round(
            (item["returned_chars"] + item["reread_chars"]) / denominator, 6)
    return {
        "path_distill_calls": len(observations),
        "source_chars": sum(x["source_chars"] for x in observations),
        "returned_capsule_chars": sum(x["returned_chars"] for x in observations),
        "reread_chars": sum(x["reread_chars"] for x in observations),
        "reread_events": sum(x["reread_events"] for x in observations),
        "net_avoided_chars": sum(x["net_avoided_chars"] for x in observations),
        "net_avoided_tokens_est": sum(x["net_avoided_tokens_est"] for x in observations),
        "context_burden_ratio": (round(
            (sum(x["returned_chars"] for x in observations) + sum(x["reread_chars"] for x in observations)) /
            sum(x["source_chars"] for x in observations), 6)
            if sum(x["source_chars"] for x in observations) else None),
        "injected_qxen_calls": sum(x.get("decision") == "INJECT_QXEN" for x in observations),
        "bypassed_qxen_calls": sum(x.get("decision") == "BYPASS_QXEN" for x in observations),
        "unmatched_retrieval_events": unmatched_retrievals,
        "observations": observations[-50:],
        "coverage": "MCP path distill + qxen_cd_source_slice rereads",
        "blind_spot": "direct shell/Read access outside MCP is not observable here",
    }


def load(path: Path = DEFAULT_LOG) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except json.JSONDecodeError:
                continue
    return rows


def load_local_qwen(path: Path = DEFAULT_LOCAL_QWEN_LOG) -> list[dict[str, Any]]:
    """Load LocalQwen terminal audit rows; malformed lines are ignored."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def summarize_local_qwen(rows: list[dict[str, Any]], workspace: str = "",
                         session_id: str = "") -> dict[str, Any]:
    """Summarize Qwen use separately from GPT savings.

    Local tokens are estimates from logged characters. They are costs/overhead
    for attribution, never GPT savings and never business-task counts.
    """
    if workspace:
        # Older local logs have no workspace field; retain them for the global
        # workspace report rather than silently discarding historical evidence.
        rows = [r for r in rows if not r.get("workspace") or r.get("workspace") == workspace]
    if session_id:
        rows = [r for r in rows if not r.get("session_id") or r.get("session_id") == session_id]
    terminal = [r for r in rows if r.get("status") in {"OK", "FALLBACK", "ERROR"}]
    starts = [r for r in rows if r.get("status") == "START"]
    by_tool: dict[str, dict[str, Any]] = {}
    by_class: dict[str, dict[str, Any]] = {}
    total_input = total_output = total_tokens = 0
    fallbacks = errors = cached_health = retries = 0
    for row in terminal:
        tool = str(row.get("tool", "unknown"))
        cls = str(row.get("usage_class") or row.get("audit_class") or "legacy_unclassified")
        input_chars = int(row.get("input_chars") or 0)
        output_chars = int(row.get("output_chars") or 0)
        token_est = max(0, math.ceil((input_chars + output_chars) / 4))
        total_input += input_chars
        total_output += output_chars
        total_tokens += token_est
        fallbacks += row.get("status") == "FALLBACK"
        errors += row.get("status") == "ERROR"
        cached_health += tool == "local_health" and bool(row.get("cached"))
        retries += max(0, int(row.get("attempt") or 1) - 1)
        for key, bucket in ((tool, by_tool), (cls, by_class)):
            item = bucket.setdefault(key, {"calls": 0, "input_chars": 0,
                                           "output_chars": 0, "tokens_est": 0,
                                           "fallbacks": 0, "errors": 0})
            item["calls"] += 1
            item["input_chars"] += input_chars
            item["output_chars"] += output_chars
            item["tokens_est"] += token_est
            item["fallbacks"] += row.get("status") == "FALLBACK"
            item["errors"] += row.get("status") == "ERROR"
    path_rows = [r for r in terminal if r.get("input_mode") == "local_path"]
    path_input = sum(int(r.get("input_chars") or 0) for r in path_rows)
    path_output = sum(int(r.get("output_chars") or 0) for r in path_rows)
    return {
        "schema_version": "local_qwen_audit_v1",
        "invocation_rows": len(terminal),
        "start_rows": len(starts),
        "calls_est": len(terminal),
        "successful_calls": sum(r.get("status") == "OK" for r in terminal),
        "fallback_calls": fallbacks,
        "error_calls": errors,
        "retry_count": retries,
        "health_cache_hits": cached_health,
        "input_chars": total_input,
        "output_chars": total_output,
        "local_tokens_est": total_tokens,
        "counted_as_gpt_saving": False,
        "audit_only_calls": sum(r.get("usage_class") == "audit_only" for r in terminal),
        "business_assist_calls": sum(r.get("usage_class") not in {"audit_only", "health_probe"}
                                      for r in terminal),
        "observable_path_accounting": {
            "path_calls": len(path_rows),
            "source_chars": path_input,
            "returned_chars": path_output,
            "net_avoided_chars_before_reread": max(0, path_input - path_output),
            "net_avoided_tokens_est_before_reread": estimate_tokens(max(0, path_input - path_output)),
            "reread_tracking": "not_available_without_qxen_cd_source_slice",
        },
        "by_tool": by_tool,
        "by_usage_class": by_class,
        "note": "字符/4估算；LocalQwen成本单列，不冒充GPT节省，health缓存命中仍记调用但不记Ollama生成成本。",
    }


def summarize(rows: list[dict[str, Any]], workspace: str = "", session_id: str = "") -> dict[str, Any]:
    if workspace:
        rows = [r for r in rows if r.get("workspace") == workspace]
    if session_id:
        rows = [r for r in rows if r.get("session_id") == session_id]
    registered = {r.get("work_item_id"): r for r in rows
                  if r.get("event_type") == "work_item_registered" and r.get("work_item_id")}
    usage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    processing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_usage_ids: set[str] = set()
    duplicate_usage = 0
    for row in rows:
        key = row.get("work_item_id")
        if not key:
            continue
        if row.get("event_type") == "usage_observation":
            usage_id = row.get("usage_id")
            if not usage_id or usage_id in seen_usage_ids:
                duplicate_usage += 1
                continue
            seen_usage_ids.add(usage_id)
            usage[key].append(row)
        elif row.get("event_type") == "qxen_processing":
            processing[key].append(row)

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

    baseline_tokens = qxen_tokens = local_tokens = 0
    raw_source_chars = payload_chars = 0
    payload_chars_known = 0
    review_tokens = fallback_replay_tokens = 0
    paired = 0
    successful_pairs = 0
    extra_valid_usage = 0
    for key in categories["baseline_required"]:
        obs = usage.get(key, [])
        comparable = [x for x in obs if isinstance(x.get("baseline_gpt_tokens"), (int, float))
                      and isinstance(x.get("qxen_gpt_tokens"), (int, float))
                      and x.get("baseline_mode") == registered[key].get("baseline_mode")
                      and (x.get("pipeline") or "process") == "process"
                      and x.get("eval_window") and x.get("outcome") in OUTCOMES]
        if comparable:
            row = comparable[0]
            extra_valid_usage += max(0, len(comparable) - 1)
            b = float(row["baseline_gpt_tokens"])
            q = float(row["qxen_gpt_tokens"])
            baseline_tokens += b
            qxen_tokens += q
            raw_source_chars += int(row.get("source_chars") or 0)
            if isinstance(row.get("payload_chars"), (int, float)):
                payload_chars += int(row["payload_chars"])
                payload_chars_known += 1
            local_tokens += float(row.get("qxen_local_tokens") or 0)
            review_tokens += float(row.get("gpt_review_tokens") or 0)
            fallback_replay_tokens += float(row.get("fallback_replay_gpt_tokens") or 0)
            paired += 1
            successful_pairs += row.get("outcome") == "success"

    processing_events = [r for r in rows if r.get("event_type") == "qxen_processing"]
    processing_by_pipeline: dict[str, dict[str, Any]] = {}
    for event in processing_events:
        pipeline = str(event.get("pipeline") or "unknown")
        bucket = processing_by_pipeline.setdefault(pipeline, {
            "events": 0, "source_chars": 0, "qxen_output_chars": 0,
            "overhead_chars": 0, "fallback_events": 0,
        })
        bucket["events"] += 1
        bucket["source_chars"] += int(event.get("source_chars") or 0)
        bucket["qxen_output_chars"] += int(event.get("qxen_output_chars") or 0)
        bucket["overhead_chars"] += int(event.get("overhead_chars") or 0)
        bucket["fallback_events"] += bool(event.get("fallback"))
    fallback_count = sum(bool(r.get("fallback")) for r in processing_events)
    l1_keys = categories["baseline_required"]
    processed_l1 = {r.get("work_item_id") for r in processing_events if r.get("work_item_id") in l1_keys}
    capsule_accepted = {r.get("capsule_id") for r in processing_events if r.get("capsule_id")}
    capsule_used = {r.get("capsule_id") for r in rows if r.get("event_type") == "capsule_use"}
    valid_capsule_ids = capsule_accepted - {None}
    confirmed_usage_rows: list[dict[str, Any]] = []
    for key in categories["baseline_required"]:
        for row in usage.get(key, []):
            capsule_id = row.get("capsule_id")
            if capsule_id and capsule_id in capsule_used and (row.get("pipeline") or "process") == "process":
                confirmed_usage_rows.append(row)
                break
    confirmed_baseline = sum(float(r.get("baseline_gpt_tokens") or 0) for r in confirmed_usage_rows)
    confirmed_qxen = sum(float(r.get("qxen_gpt_tokens") or 0) for r in confirmed_usage_rows)
    confirmed_review = sum(float(r.get("gpt_review_tokens") or 0) for r in confirmed_usage_rows)
    confirmed_replay = sum(float(r.get("fallback_replay_gpt_tokens") or 0) for r in confirmed_usage_rows)
    overhead_chars = sum(int(r.get("overhead_chars") or 0) for r in processing_events)
    net_saved = baseline_tokens - qxen_tokens - review_tokens - fallback_replay_tokens
    local_qwen = summarize_local_qwen(load_local_qwen(), workspace, session_id)
    return {
        "schema_version": "qxen_cd_audit_v1",
        "scope": {"workspace": workspace or "*", "session_id": session_id or "*"},
        "business_work_items": len(registered),
        "business_work_items_by_category": {
            k: len(v) for k, v in categories.items()
        },
        "business_work_item_ids": categories,
        "qxen_processing_events": len(processing_events),
        "qxen_events_not_new_business_tasks": True,
        "fallback_events": fallback_count,
        "utilization": {
            "qxen_task_utilization": (len(processed_l1) / len(l1_keys) if l1_keys else None),
            "accepted_capsule_count_observed": len(valid_capsule_ids),
            "accepted_capsule_use_count_observed": len(valid_capsule_ids & capsule_used),
            "accepted_capsule_utilization": (len(valid_capsule_ids & capsule_used) / len(valid_capsule_ids)
                                              if valid_capsule_ids else None),
            "note": "没有 capsule_id 或后续引用打点时保持 null，不推测利用率。",
        },
        "comparable_usage_pairs": paired,
        "successful_usage_pairs": successful_pairs,
        "token_accounting": {
            "baseline_gpt_tokens": baseline_tokens,
            "qxen_gpt_tokens": qxen_tokens,
            "raw_source_chars": raw_source_chars,
            "qxen_payload_chars": (payload_chars if payload_chars_known == paired else None),
            "chars_avoided": (raw_source_chars - payload_chars
                               if payload_chars_known == paired else None),
            "compression_rate": ((raw_source_chars - payload_chars) / raw_source_chars
                                  if raw_source_chars and payload_chars_known == paired else None),
            "payload_chars_observations": payload_chars_known,
            "confirmed_capsule_use_pairs": len(confirmed_usage_rows),
            "actual_used_net_gpt_tokens_saved": (confirmed_baseline - confirmed_qxen
                                                  - confirmed_review - confirmed_replay),
            "actual_used_saving_rate": ((confirmed_baseline - confirmed_qxen
                                          - confirmed_review - confirmed_replay) / confirmed_baseline
                                         if confirmed_baseline else None),
            "qxen_local_tokens": local_tokens,
            "gpt_review_tokens": review_tokens,
            "fallback_replay_gpt_tokens": fallback_replay_tokens,
            "gross_gpt_tokens_saved": baseline_tokens - qxen_tokens,
            "net_gpt_tokens_saved": net_saved,
            "saving_rate": (net_saved / baseline_tokens
                            if baseline_tokens else None),
            "note": "未登记 capsule_use 的配对只算观测值，不宣称已进入GPT上下文；QXEN本地token不冒充GPT节省；fallback原文重放单独扣除。",
        },
        "pipeline_accounting": {
            "by_pipeline": processing_by_pipeline,
            "business_saving_pipeline": "process",
            "ingest_compact_excluded_from_business_saving": True,
            "bootstrap_system_only": True,
            "audit_assistant_excluded": True,
        },
        "data_quality": {
            "unpaired_baseline_items": sum(1 for k in categories["baseline_required"] if not usage.get(k)),
            "unknown_baseline_items": len(categories["unknown"]),
            "qxen_added_excluded_from_savings": len(categories["qxen_added"]),
            "duplicate_usage_rows_ignored": duplicate_usage,
            "extra_valid_usage_rows_ignored": extra_valid_usage,
            "unpaired_or_mismatched_usage_rows": sum(len(v) for k, v in usage.items()
                                                      if k not in categories["baseline_required"]),
        },
        "overhead": {
            "processing_event_count": len(processing_events),
            "explicit_overhead_chars": overhead_chars,
            "explicit_overhead_tokens_est": estimate_tokens(overhead_chars),
            "input_source_chars_not_overhead": sum(int(r.get("source_chars") or 0) for r in processing_events),
            "processing_events_excluded_from_savings": True,
            "audit_log_events_excluded_from_savings": True,
        },
        "local_qwen_audit": local_qwen,
        "observable_path_accounting": summarize_observable_paths(rows),
        "primary_savings_metric": "observable_path_accounting.net_avoided_tokens_est",
        "report_status": ("descriptive_only_need_50_pairs"
                          if paired < 50 else "eligible_for_inference"),
        "generated_at": now(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="QXEN-CD audit ledger summary")
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--workspace", default="")
    ap.add_argument("--session-id", default="")
    args = ap.parse_args()
    print(json.dumps(summarize(load(Path(args.log)), args.workspace, args.session_id),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
