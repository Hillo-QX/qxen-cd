#!/usr/bin/env python3
"""Route long/reusable Codex responses into an auditable QXEN work queue.

Hooks do not have a reliable PostResponse contract. This script therefore
stores only an explicit assistant response and a deterministic envelope; the
main Agent calls QXEN-CD directly for the queued raw file.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "调度状态" / "response_capsules"
P1_LOG = ROOT / "日志" / "p1_trigger_events.jsonl"
MIN_REUSABLE_BYTES = 4096
MAX_ATTEMPTS = 2
DEFAULT_LEASE_SECONDS = 30 * 60
PRESSURE_MAX_AGE_SECONDS = 24 * 60 * 60
LOCK_TIMEOUT_SECONDS = 5.0
CONTEXT_PRESSURE_THRESHOLD = 0.80
DEFAULT_CONTEXT_WINDOW_TOKENS = 5_000_000
PENDING_STATUS = "PENDING_QXEN"
RUNNING_STATUS = "RUNNING_QXEN"
COMPLETED_STATUS = "COMPLETED"
FAILED_STATUS = "FAILED"
EXPIRED_STATUS = "EXPIRED"
RISK_WORDS = re.compile(r"金融|财务|回测|交易|收益|风险|训练|模型|权重|checkpoint|gate|上线|审计|法律|医疗", re.I)
REUSE_WORDS = re.compile(r"交接|handoff|状态|state|下一轮|后续|gate|checkpoint|失败|复用|总结|审计", re.I)
RESPONSE_KEYS = ("assistant_response", "last_assistant_response", "response_text", "codex_response", "assistant_output")
TASK_KEYS = ("task", "task_type", "user_prompt", "prompt")
TASK_ID_KEYS = ("task_id", "taskId", "work_item_id", "workItemId")
TERM_STOPWORDS = {"请", "帮我", "一下", "当前", "这个", "任务", "内容", "检查", "测试", "进行", "继续", "需要"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def lease_seconds() -> int:
    try:
        return max(60, int(os.environ.get("QXEN_CAPSULE_LEASE_SECONDS", DEFAULT_LEASE_SECONDS)))
    except ValueError:
        return DEFAULT_LEASE_SECONDS


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@contextlib.contextmanager
def _capsule_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"capsule lock timeout: {path.name}")
                time.sleep(0.01)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_write_json(path: Path, data: dict) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def _running_is_stale(data: dict, current: datetime | None = None) -> bool:
    if data.get("status") != RUNNING_STATUS:
        return False
    current = current or datetime.now(timezone.utc)
    expires = _parse_time(str(data.get("lease_expires_at", "")))
    if expires is None:
        claimed = _parse_time(str(data.get("claimed_at", ""))) or _parse_time(str(data.get("updated_at", "")))
        expires = claimed + timedelta(seconds=lease_seconds()) if claimed else current
    return expires <= current


def _recover_stale(data: dict, current: datetime | None = None) -> bool:
    current = current or datetime.now(timezone.utc)
    if not _running_is_stale(data, current):
        return False
    data.update({
        "status": PENDING_STATUS,
        "recovery_reason": "lease_expired",
        "recovered_at": current.isoformat(),
        "updated_at": current.isoformat(),
        "claim_token": "",
        "claimed_at": "",
        "lease_expires_at": "",
        "worker_id": "",
    })
    return True


def first_value(obj, keys):
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in obj.values():
            found = first_value(value, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = first_value(value, keys)
            if found:
                return found
    return ""


def extract_final_response(transcript_path: str) -> str:
    """Read a Codex rollout transcript and return the last final assistant message.

    SessionEnd payloads carry no assistant text, only transcript_path. Prefers the
    last assistant message with phase=final_answer; falls back to the latest
    task_complete last_agent_message, then any assistant output_text.
    """
    path = Path(transcript_path or "")
    if not path.is_file():
        return ""
    final_answer = ""
    task_complete_msg = ""
    any_assistant = ""
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload") if isinstance(record, dict) else None
            if not isinstance(payload, dict):
                continue
            ptype = payload.get("type")
            if ptype == "message" and payload.get("role") == "assistant":
                parts = payload.get("content")
                if not isinstance(parts, list):
                    continue
                text = "".join(
                    part.get("text", "")
                    for part in parts
                    if isinstance(part, dict) and part.get("type") == "output_text"
                ).strip()
                if not text:
                    continue
                any_assistant = text
                if payload.get("phase") == "final_answer":
                    final_answer = text
            elif ptype == "task_complete":
                msg = payload.get("last_agent_message")
                if isinstance(msg, str) and msg.strip():
                    task_complete_msg = msg.strip()
    return final_answer or task_complete_msg or any_assistant


def locate_codex_transcript(session_id: str, explicit_path: str = "") -> Path | None:
    """Resolve the current Codex rollout without relying on Continue state."""
    explicit = Path(explicit_path).expanduser() if explicit_path else None
    if explicit and explicit.is_file():
        return explicit
    if not session_id:
        return None
    root = Path.home() / ".codex" / "sessions"
    matches = list(root.glob(f"**/*{session_id}*.jsonl")) if root.is_dir() else []
    return max(matches, key=lambda item: item.stat().st_mtime) if matches else None


def _latest_codex_usage(path: Path) -> tuple[int, int, int]:
    observed = cumulative = limit = 0
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return observed, cumulative, limit
    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload") if isinstance(record, dict) else None
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            last = info.get("last_token_usage") or {}
            total = info.get("total_token_usage") or {}
            observed = int(last.get("input_tokens") or last.get("prompt_tokens") or observed)
            cumulative = int(total.get("input_tokens") or total.get("prompt_tokens") or cumulative)
            limit = int(info.get("model_context_window") or limit)
    return observed, cumulative, limit


def keyword_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for word in re.findall(r"[A-Za-z0-9_/-]{3,}", text.lower()):
        terms.add(word)
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return {term for term in terms if term not in TERM_STOPWORDS}


def pressure_value(payload: dict) -> float:
    value = payload.get("context_pressure", payload.get("contextPressure", payload.get("pressure", "")))
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0


def context_window_tokens() -> int:
    try:
        return max(1, int(os.environ.get("CODEX_CONTEXT_WINDOW_TOKENS", DEFAULT_CONTEXT_WINDOW_TOKENS)))
    except ValueError:
        return DEFAULT_CONTEXT_WINDOW_TOKENS


def estimate_context_pressure(session_id: str, transcript_path: str = "") -> dict:
    """Estimate active pressure from the latest turn, not cumulative usage."""
    codex_path = locate_codex_transcript(session_id, transcript_path)
    observed = 0
    cumulative = 0
    source = "missing_session_usage"
    detected_limit = 0
    if codex_path:
        observed, cumulative, detected_limit = _latest_codex_usage(codex_path)
        source = "codex_rollout.token_count" if observed else "codex_rollout_without_usage"
    limit = detected_limit or context_window_tokens()
    return {
        "pressure": round(min(1.0, max(0.0, observed / limit)), 4),
        "observed_tokens": observed,
        "cumulative_prompt_tokens": cumulative,
        "limit_tokens": limit,
        "source": source,
    }


def _task_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _log_p1_event(event: dict) -> None:
    try:
        P1_LOG.parent.mkdir(parents=True, exist_ok=True)
        with P1_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _load_candidate(path: Path) -> tuple[dict, float]:
    current = datetime.now(timezone.utc)
    with _capsule_lock(path):
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = _recover_stale(data, current)
        created = _parse_time(str(data.get("created_at", "")))
        if created is None:
            created = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        age_seconds = max(0.0, (current - created).total_seconds())
        if age_seconds > 7 * 86400 and data.get("status") in {PENDING_STATUS, RUNNING_STATUS}:
            data.update({"status": EXPIRED_STATUS, "updated_at": current.isoformat()})
            changed = True
        if changed:
            _atomic_write_json(path, data)
    return data, age_seconds


def route(response: str, task: str = "", reusable: bool = False) -> dict:
    size = len(response.encode("utf-8"))
    text = f"{task}\n{response}"
    risk = bool(RISK_WORDS.search(text))
    reuse = reusable or bool(REUSE_WORDS.search(text))
    long = size > MIN_REUSABLE_BYTES
    if risk and not long:
        decision, reason = "KEEP_RAW_HIGH_RISK", "high_risk_short_preserve"
    elif reuse and not long:
        decision, reason = "KEEP_RAW_REUSABLE", "reusable_but_below_qxen_minimum"
    else:
        decision = "QUEUE_QXEN" if long else "KEEP_RAW"
        reason = "long_above_qxen_minimum" if long else "short_low_reuse"
    return {
        "decision": decision,
        "reason": reason,
        "risk": "high" if risk else "normal",
        "reuse": "high" if reuse else "low",
        "bytes": size,
        "relevance_terms": sorted(keyword_terms(f"{task}\n{response[:800]}"))[:32],
    }


def record(payload: dict) -> int:
    response = first_value(payload, RESPONSE_KEYS)
    if not response:
        print("[response-capsule] status=SKIP reason=no_explicit_assistant_response")
        return 0
    task = first_value(payload, TASK_KEYS)
    task_id = first_value(payload, TASK_ID_KEYS)
    session_id = first_value(payload, ("session_id", "sessionId", "conversation_id")) or "unknown"
    decision = route(response, task, bool(payload.get("reusable")))
    if decision["decision"] != "QUEUE_QXEN":
        print(f"[response-capsule] status=SKIP decision={decision['decision']} reason={decision['reason']} bytes={decision['bytes']}")
        return 0
    QUEUE.mkdir(parents=True, exist_ok=True)
    response_hash = hashlib.sha256(response.encode("utf-8")).hexdigest()
    for existing in QUEUE.glob("*.json"):
        try:
            prior = json.loads(existing.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if prior.get("session_id") == session_id and prior.get("response_hash") == response_hash:
            print(f"[response-capsule] status=DEDUP capsule_id={prior.get('capsule_id')} current={prior.get('status')}")
            return 0
    stamp = now()
    safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id)[:80] or "unknown"
    prefix = QUEUE / f"{stamp}_{safe_session}"
    raw_path = prefix.with_suffix(".raw.txt")
    envelope_path = prefix.with_suffix(".json")
    raw_path.write_text(response, encoding="utf-8")
    envelope = {
        "capsule_id": prefix.name,
        "status": "PENDING_QXEN",
        "attempts": 0,
        "claim_token": "",
        "claimed_at": "",
        "lease_expires_at": "",
        "worker_id": "",
        "processed_at": "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "fallback_reason": "",
        "source": "codex_response",
        "source_path": str(raw_path),
        "source_span": "full_response",
        "session_id": session_id,
        "task": task,
        "task_id": task_id,
        "route": decision,
        "response_hash": response_hash,
        "raw_pointer": str(raw_path),
        "source_locator": {
            "path": str(raw_path),
            "sha256": response_hash,
            "bytes": len(response.encode("utf-8")),
            "span": "full_response",
        },
        "consumption_policy": {
            "mode": "capsule_first_targeted_retrieval",
            "equivalence": "task_scoped_not_source_equivalent",
            "use_capsule_first": True,
            "retrieve_original_when": [
                "exact_quote_or_value_required",
                "code_edit_or_line_level_review",
                "conflict_or_missing_evidence",
                "high_risk_decision",
            ],
            "never_claim_full_source_replacement": True,
        },
        "distill_model": "qxen-cd-clean-v1",
        "authority": "advisory_only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires": "next_task_or_7d",
    }
    _atomic_write_json(envelope_path, envelope)
    print(f"[response-capsule] status={PENDING_STATUS} attempts=0 bytes={decision['bytes']} risk={decision['risk']} reuse={decision['reuse']} raw_pointer={raw_path}")
    return 0


def pending(payload: dict) -> int:
    session_id = first_value(payload, ("session_id", "sessionId", "conversation_id"))
    current_task = first_value(payload, TASK_KEYS)
    current_task_id = first_value(payload, TASK_ID_KEYS)
    current_terms = keyword_terms(current_task)
    pressure = pressure_value(payload)
    pressure_observed = payload.get("context_pressure_observed_tokens", 0)
    pressure_limit = payload.get("context_pressure_limit_tokens", context_window_tokens())
    pressure_source = payload.get("context_pressure_source", "unknown")
    task_hash = _task_fingerprint(current_task) if current_task else ""
    items = sorted(QUEUE.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if QUEUE.is_dir() else []
    if not items:
        _log_p1_event({"time": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
                       "task_hash": task_hash, "triggered": False, "reason": "no_pending",
                       "pressure": pressure, "observed_tokens": pressure_observed,
                       "limit_tokens": pressure_limit, "pressure_source": pressure_source})
        print("[response-capsule] pending=0")
        return 0
    data = None
    for item in items:
        try:
            candidate, age_seconds = _load_candidate(item)
        except (OSError, json.JSONDecodeError, TimeoutError):
            continue
        if candidate.get("status") != PENDING_STATUS:
            continue
        if not session_id or candidate.get("session_id") != session_id:
            continue
        same_task_id = bool(current_task_id and candidate.get("task_id") and current_task_id == candidate.get("task_id"))
        overlap = len(current_terms.intersection(set(candidate.get("route", {}).get("relevance_terms", []))))
        strongly_related = same_task_id or overlap >= 2
        pressure_related = (
            pressure >= CONTEXT_PRESSURE_THRESHOLD
            and age_seconds <= PRESSURE_MAX_AGE_SECONDS
            and overlap >= 1
        )
        if strongly_related or pressure_related:
            candidate["trigger"] = "task_related" if strongly_related else "context_pressure"
            candidate["keyword_overlap"] = overlap
            candidate["context_pressure"] = pressure
            candidate["capsule_age_s"] = round(age_seconds, 3)
            data = candidate
            break
    if data is None:
        _log_p1_event({"time": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
                       "task_hash": task_hash, "triggered": False, "reason": "unrelated_task",
                       "pressure": pressure, "observed_tokens": pressure_observed,
                       "limit_tokens": pressure_limit, "pressure_source": pressure_source})
        print("[response-capsule] pending=0 reason=no_pending")
        return 0
    _log_p1_event({"time": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
                   "task_hash": task_hash, "capsule_id": data.get("capsule_id"),
                   "triggered": True, "reason": data.get("trigger"),
                   "pressure": pressure, "observed_tokens": pressure_observed,
                   "limit_tokens": pressure_limit, "pressure_source": pressure_source,
                   "keyword_overlap": data.get("keyword_overlap", 0),
                   "capsule_age_s": data.get("capsule_age_s", 0)})
    print(f"[response-capsule] pending=1 trigger={data.get('trigger')} overlap={data.get('keyword_overlap', 0)} pressure={data.get('context_pressure', 0):.2f} status={data.get('status')} attempts={data.get('attempts', 0)} capsule_id={data.get('capsule_id')} envelope={QUEUE / (data.get('capsule_id', '') + '.json')} raw_pointer={data.get('raw_pointer')} next=claim_then_qxen_longtext_compact_then_complete_with_result_file")
    return 0


def resolve_capsule(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = QUEUE / f"{value}.json"
    path = path.resolve()
    if path.parent != QUEUE.resolve() or path.suffix != ".json":
        raise ValueError("capsule must be an envelope inside response_capsules")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def transition_status(capsule: str, action: str, reason: str = "", latency: str = "",
                      claim_token: str = "", worker_id: str = "", result_file: str = "",
                      compact_state: str = "", result_payload: dict | None = None) -> dict:
    """Atomically transition one capsule and return machine-readable state."""
    path = resolve_capsule(capsule)
    current = datetime.now(timezone.utc)
    with _capsule_lock(path):
        data = json.loads(path.read_text(encoding="utf-8"))
        recovered = _recover_stale(data, current)
        status = data.get("status")
        attempts = int(data.get("attempts", 0) or 0)
        result = {"ok": True, "changed": False, "recovered": recovered,
                  "status": status, "capsule_id": data.get("capsule_id", "")}

        if action == "claim":
            if status != PENDING_STATUS:
                result.update({"ok": False, "reason": f"claim_unavailable:{status}"})
            elif attempts >= MAX_ATTEMPTS:
                data["status"] = FAILED_STATUS
                data["fallback_reason"] = "max_attempts_exceeded"
                result.update({"ok": False, "changed": True, "reason": "max_attempts_exceeded"})
            else:
                token = uuid.uuid4().hex
                data.update({
                    "status": RUNNING_STATUS,
                    "attempts": attempts + 1,
                    "claim_token": token,
                    "claimed_at": current.isoformat(),
                    "lease_expires_at": (current + timedelta(seconds=lease_seconds())).isoformat(),
                    "worker_id": worker_id or f"pid-{os.getpid()}",
                })
                result.update({"changed": True, "claim_token": token})
        elif action == "complete":
            if status == COMPLETED_STATUS:
                result["idempotent"] = True
            elif status != RUNNING_STATUS:
                result.update({"ok": False, "reason": f"complete_unavailable:{status}"})
            elif data.get("claim_token") and claim_token != data.get("claim_token"):
                result.update({"ok": False, "reason": "stale_claim_token"})
            else:
                data.update({"status": COMPLETED_STATUS, "processed_at": current.isoformat(),
                             "claim_token": "", "lease_expires_at": "", "worker_id": ""})
                if latency:
                    data["distill_latency_s"] = float(latency)
                if result_file:
                    result_path = Path(result_file).expanduser().resolve()
                    data["distill_result_path"] = str(result_path)
                    if result_path.is_file():
                        raw_result = result_path.read_bytes()
                        data["distill_result_sha256"] = hashlib.sha256(raw_result).hexdigest()
                        data["distill_result_bytes"] = len(raw_result)
                        if len(raw_result) <= 24000:
                            try:
                                data["distilled_result"] = json.loads(raw_result.decode("utf-8"))
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                pass
                if compact_state:
                    data["compact_state_path"] = str(Path(compact_state).expanduser().resolve())
                if isinstance(result_payload, dict):
                    encoded = json.dumps(result_payload, ensure_ascii=False).encode("utf-8")
                    data["distill_result_sha256"] = hashlib.sha256(encoded).hexdigest()
                    data["distill_result_bytes"] = len(encoded)
                    sidecar = path.with_suffix(".result.json")
                    _atomic_write_json(sidecar, result_payload)
                    data["distill_result_path"] = str(sidecar)
                    if len(encoded) <= 24000:
                        data["distilled_result"] = result_payload
                result["changed"] = True
        elif action == "fail":
            if status in {FAILED_STATUS, EXPIRED_STATUS}:
                result["idempotent"] = True
            elif status != RUNNING_STATUS:
                result.update({"ok": False, "reason": f"fail_unavailable:{status}"})
            elif data.get("claim_token") and claim_token != data.get("claim_token"):
                result.update({"ok": False, "reason": "stale_claim_token"})
            else:
                data.update({
                    "fallback_reason": reason or "qxen_failure",
                    "status": PENDING_STATUS if attempts < MAX_ATTEMPTS else FAILED_STATUS,
                    "claim_token": "",
                    "claimed_at": "",
                    "lease_expires_at": "",
                    "worker_id": "",
                })
                result["changed"] = True
        elif action == "expire":
            if status == EXPIRED_STATUS:
                result["idempotent"] = True
            else:
                data.update({"status": EXPIRED_STATUS, "claim_token": "",
                             "lease_expires_at": "", "worker_id": ""})
                result["changed"] = True
        else:
            raise ValueError(action)

        if recovered or result["changed"]:
            data["updated_at"] = current.isoformat()
            _atomic_write_json(path, data)
        result.update({"status": data.get("status"), "attempts": int(data.get("attempts", 0) or 0)})
        return result


def update_status(capsule: str, action: str, reason: str = "", latency: str = "",
                  claim_token: str = "", worker_id: str = "", result_file: str = "",
                  compact_state: str = "") -> int:
    result = transition_status(capsule, action, reason, latency, claim_token, worker_id,
                               result_file, compact_state)
    marker = result["status"] if result.get("ok") else "NOOP"
    print(f"[response-capsule] status={marker} current={result.get('status')} attempts={result.get('attempts', 0)} capsule_id={result.get('capsule_id')} reason={result.get('reason', reason)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pending", action="store_true")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--claim", action="store_true")
    actions.add_argument("--complete", action="store_true")
    actions.add_argument("--fail", action="store_true")
    actions.add_argument("--expire", action="store_true")
    parser.add_argument("--capsule", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--latency", default="")
    parser.add_argument("--claim-token", default="")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--result-file", default="")
    parser.add_argument("--compact-state", default="")
    args = parser.parse_args()
    if args.pending:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except (OSError, json.JSONDecodeError):
            payload = {}
        return pending(payload)
    action = next((name for name in ("claim", "complete", "fail", "expire") if getattr(args, name)), "")
    if action:
        if not args.capsule:
            parser.error("--capsule is required for a status action")
        return update_status(args.capsule, action, args.reason, args.latency,
                             args.claim_token, args.worker_id, args.result_file, args.compact_state)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (OSError, json.JSONDecodeError):
        payload = {}
    return record(payload)


if __name__ == "__main__":
    raise SystemExit(main())
