from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from qxen_cd.capsule_state import COMPLETED, CapsuleStore, active_context_pressure, should_surface

def test_atomic_claim_and_idempotent_complete(tmp_path):
    store = CapsuleStore(tmp_path); store.create("race", session_id="s1", task_id="t1")
    with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(lambda i: store.transition("race", "claim", worker_id=f"w{i}"), range(8)))
    winners = [item for item in results if item["ok"]]; assert len(winners) == 1; token = winners[0]["claim_token"]
    assert store.transition("race", "complete", claim_token=token)["status"] == COMPLETED
    assert store.transition("race", "complete", claim_token=token)["idempotent"]

def test_lease_recovery_rejects_stale_worker(tmp_path):
    store = CapsuleStore(tmp_path, lease_seconds=60); store.create("stale", session_id="s1"); old = store.transition("stale", "claim", worker_id="old")
    path = tmp_path / "stale.json"; data = json.loads(path.read_text()); data["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(); path.write_text(json.dumps(data))
    new = store.transition("stale", "claim", worker_id="new"); assert new["ok"] and new["recovered"] and new["claim_token"] != old["claim_token"]
    assert store.transition("stale", "complete", claim_token=old["claim_token"])["reason"] == "stale_claim_token"

def test_active_pressure_and_related_surface(tmp_path):
    pressure = active_context_pressure([{"message": {"usage": {"prompt_tokens": 100}}}], 1000); assert pressure["pressure"] == 0.1
    store = CapsuleStore(tmp_path); store.create("c", session_id="s1", task_id="t1", route={"relevance_terms": ["qxen", "audit"]}); capsule = json.loads((tmp_path / "c.json").read_text())
    assert should_surface(capsule, session_id="s1", current_terms={"audit"}, pressure=0.9)[0]
    assert not should_surface(capsule, session_id="other", current_terms={"audit"}, pressure=0.9)[0]
