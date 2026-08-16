"""Deterministic P0/P1 capsule state and surfacing primitives.

This module is host-neutral: callers provide a queue directory and keep model
inference outside the state machine.  Every transition is atomic and lease
based so a crashed worker cannot permanently strand a capsule.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

PENDING = "PENDING_QXEN"
RUNNING = "RUNNING_QXEN"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
EXPIRED = "EXPIRED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def active_context_pressure(history: Iterable[dict[str, Any]], context_limit: int) -> dict[str, Any]:
    """Use the latest turn usage; cumulative session totals are audit-only."""
    observed = 0
    for item in reversed(list(history)):
        usage = (item.get("message") or {}).get("usage") or item.get("usage") or {}
        observed = int(usage.get("prompt_tokens", usage.get("promptTokens", 0)) or 0)
        if observed:
            break
    limit = max(1, int(context_limit))
    return {
        "pressure": round(min(1.0, max(0.0, observed / limit)), 4),
        "observed_tokens": observed,
        "limit_tokens": limit,
        "source": "history.latest_usage.prompt_tokens" if observed else "missing_active_usage",
    }


def _fresh(data: dict[str, Any], current: datetime, max_age_seconds: int) -> bool:
    created = _parse(str(data.get("created_at", "")))
    return bool(created and (current - created).total_seconds() <= max_age_seconds)


def should_surface(capsule: dict[str, Any], *, session_id: str, task_id: str = "",
                   current_terms: set[str] | None = None, pressure: float = 0.0,
                   now: datetime | None = None, pressure_threshold: float = 0.80,
                   pressure_max_age_seconds: int = 24 * 60 * 60) -> tuple[bool, str, int]:
    """Return (surface, reason, overlap) using deterministic P1 rules."""
    current = now or _now()
    if capsule.get("status") != PENDING or not session_id:
        return False, "not_pending_or_missing_session", 0
    if capsule.get("session_id") != session_id:
        return False, "different_session", 0
    same_task = bool(task_id and capsule.get("task_id") == task_id)
    terms = current_terms or set()
    stored = set(capsule.get("route", {}).get("relevance_terms", []))
    overlap = len(terms.intersection(stored))
    if same_task or overlap >= 2:
        return True, "task_related", overlap
    pressure_related = (pressure >= pressure_threshold and overlap >= 1
                        and _fresh(capsule, current, pressure_max_age_seconds))
    return (True, "context_pressure", overlap) if pressure_related else (False, "unrelated_task", overlap)


class CapsuleStore:
    """File-backed capsule store with atomic claim, lease recovery and idempotency."""

    def __init__(self, root: str | Path, *, lease_seconds: int = 1800,
                 max_attempts: int = 2, lock_timeout_seconds: float = 5.0):
        self.root = Path(root)
        self.lease_seconds = max(60, int(lease_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.lock_timeout_seconds = lock_timeout_seconds
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, capsule_id: str) -> Path:
        path = (self.root / f"{capsule_id}.json").resolve()
        if path.parent != self.root.resolve() or path.suffix != ".json":
            raise ValueError("capsule_id must resolve inside the store")
        return path

    @contextlib.contextmanager
    def _lock(self, path: Path):
        descriptor = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.lock_timeout_seconds
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

    @staticmethod
    def _write(path: Path, data: dict[str, Any]) -> None:
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

    def create(self, capsule_id: str, *, session_id: str, task_id: str = "",
               task: str = "", raw_pointer: str = "", route: dict[str, Any] | None = None) -> dict[str, Any]:
        current = _now()
        data = {
            "capsule_id": capsule_id, "status": PENDING, "attempts": 0,
            "claim_token": "", "claimed_at": "", "lease_expires_at": "", "worker_id": "",
            "created_at": _iso(current), "updated_at": _iso(current),
            "session_id": session_id, "task_id": task_id, "task": task,
            "raw_pointer": raw_pointer, "route": route or {},
        }
        path = self._path(capsule_id)
        with self._lock(path):
            if path.exists():
                raise FileExistsError(path)
            self._write(path, data)
        return data

    def transition(self, capsule_id: str, action: str, *, claim_token: str = "",
                   worker_id: str = "", reason: str = "", latency_s: float | None = None) -> dict[str, Any]:
        path = self._path(capsule_id)
        current = _now()
        with self._lock(path):
            data = json.loads(path.read_text(encoding="utf-8"))
            status = data.get("status")
            attempts = int(data.get("attempts", 0) or 0)
            recovered = False
            lease = _parse(str(data.get("lease_expires_at", "")))
            if status == RUNNING and (lease is None or lease <= current):
                data.update({"status": PENDING, "recovery_reason": "lease_expired",
                             "claim_token": "", "claimed_at": "", "lease_expires_at": "", "worker_id": ""})
                status = PENDING
                recovered = True
            result: dict[str, Any] = {"ok": True, "changed": False, "recovered": recovered,
                                      "status": status, "attempts": attempts, "claim_token": ""}
            if action == "claim":
                if status != PENDING:
                    result.update(ok=False, reason=f"claim_unavailable:{status}")
                elif attempts >= self.max_attempts:
                    data.update(status=FAILED, fallback_reason="max_attempts_exceeded")
                    result.update(ok=False, changed=True, status=FAILED, reason="max_attempts_exceeded")
                else:
                    token = uuid.uuid4().hex
                    data.update(status=RUNNING, attempts=attempts + 1, claim_token=token,
                                claimed_at=_iso(current),
                                lease_expires_at=_iso(current + timedelta(seconds=self.lease_seconds)),
                                worker_id=worker_id or f"pid-{os.getpid()}")
                    result.update(changed=True, status=RUNNING, attempts=attempts + 1, claim_token=token)
            elif action in {"complete", "fail"}:
                if action == "complete" and status == COMPLETED:
                    result["idempotent"] = True
                elif action == "fail" and status in {FAILED, EXPIRED}:
                    result["idempotent"] = True
                elif status != RUNNING:
                    result.update(ok=False, reason=f"{action}_unavailable:{status}")
                elif data.get("claim_token") != claim_token:
                    result.update(ok=False, reason="stale_claim_token")
                elif action == "complete":
                    data.update(status=COMPLETED, processed_at=_iso(current), claim_token="",
                                claimed_at="", lease_expires_at="", worker_id="")
                    if latency_s is not None:
                        data["latency_s"] = float(latency_s)
                    result.update(changed=True, status=COMPLETED)
                else:
                    next_status = PENDING if attempts < self.max_attempts else FAILED
                    data.update(status=next_status, fallback_reason=reason or "qxen_failure",
                                claim_token="", claimed_at="", lease_expires_at="", worker_id="")
                    result.update(changed=True, status=next_status)
            elif action == "expire":
                data.update(status=EXPIRED, claim_token="", lease_expires_at="", worker_id="")
                result.update(changed=True, status=EXPIRED)
            else:
                raise ValueError(action)
            if recovered or result["changed"]:
                data["updated_at"] = _iso(current)
                self._write(path, data)
            result.update(status=data.get("status"), attempts=int(data.get("attempts", 0) or 0))
            return result
