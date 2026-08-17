#!/usr/bin/env python3
"""Concurrency, lease recovery, and idempotency tests for response capsules."""
from __future__ import annotations

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import response_capsule as rc


def make_capsule(session_id: str) -> Path:
    rc.record({"assistant_response": "交接状态\n" + "x" * 5000,
               "session_id": session_id, "task": "交接状态"})
    return next(rc.QUEUE.glob(f"*_{session_id}.json"))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        old_queue, old_log = rc.QUEUE, rc.P1_LOG
        rc.QUEUE = Path(tmp)
        rc.P1_LOG = Path(tmp) / "p1.jsonl"

        race = make_capsule("race-session")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda n: rc.transition_status(str(race), "claim", worker_id=f"worker-{n}"),
                range(8),
            ))
        winners = [result for result in results if result["ok"]]
        assert len(winners) == 1
        token = winners[0]["claim_token"]
        result_file = Path(tmp) / "qxen-result.json"
        result_file.write_text(json.dumps({"summary": ["状态已压缩"]}, ensure_ascii=False), encoding="utf-8")
        completed = rc.transition_status(str(race), "complete", claim_token=token,
                                         result_file=str(result_file), compact_state="/tmp/compact.json",
                                         result_payload={"guard_status": "ADVISORY", "summary": ["回写成功"]})
        assert completed["status"] == rc.COMPLETED_STATUS
        saved = json.loads(race.read_text(encoding="utf-8"))
        assert saved["distilled_result"]["summary"] == ["回写成功"]
        assert saved["distill_result_sha256"]
        assert Path(saved["distill_result_path"]).is_file()
        assert saved["compact_state_path"] == str(Path("/tmp/compact.json").resolve())
        duplicate = rc.transition_status(str(race), "complete", claim_token=token)
        assert duplicate["ok"] and duplicate["idempotent"]

        stale = make_capsule("stale-session")
        first = rc.transition_status(str(stale), "claim", worker_id="old-worker")
        data = json.loads(stale.read_text(encoding="utf-8"))
        data["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        rc._atomic_write_json(stale, data)
        recovered = rc.transition_status(str(stale), "claim", worker_id="new-worker")
        assert recovered["ok"] and recovered["recovered"]
        assert recovered["claim_token"] != first["claim_token"]
        rejected = rc.transition_status(str(stale), "complete", claim_token=first["claim_token"])
        assert not rejected["ok"] and rejected["reason"] == "stale_claim_token"
        completed = rc.transition_status(str(stale), "complete",
                                         claim_token=recovered["claim_token"])
        assert completed["status"] == rc.COMPLETED_STATUS

        rc.QUEUE, rc.P1_LOG = old_queue, old_log

    print("test_response_capsule_recovery: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
