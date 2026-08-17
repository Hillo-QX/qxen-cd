#!/usr/bin/env python3
"""Deterministic tests for selective Codex-response routing."""
from __future__ import annotations

import json
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import response_capsule as rc


def main() -> int:
    assert rc.route("短答")["decision"] == "KEEP_RAW"
    assert rc.route("交接状态", "普通任务")["decision"] == "KEEP_RAW_REUSABLE"
    assert rc.route("结论", "金融回测")["risk"] == "high"
    assert rc.route("结论", "金融回测")["decision"] == "KEEP_RAW_HIGH_RISK"

    with tempfile.TemporaryDirectory() as tmp:
        old = rc.QUEUE
        old_log = rc.P1_LOG
        rc.QUEUE = Path(tmp)
        rc.P1_LOG = Path(tmp) / "p1.jsonl"
        response = "状态与证据\n" + ("x" * 5000)
        assert rc.record({"assistant_response": response, "session_id": "test/session", "task": "交接", "task_id": "T1"}) == 0
        envelopes = list(Path(tmp).glob("*.json"))
        raws = list(Path(tmp).glob("*.raw.txt"))
        assert len(envelopes) == 1 and len(raws) == 1
        data = json.loads(envelopes[0].read_text(encoding="utf-8"))
        assert data["status"] == "PENDING_QXEN"
        assert data["source"] == "codex_response"
        assert data["authority"] == "advisory_only"
        assert data["task_id"] == "T1"
        assert data["raw_pointer"] == str(raws[0])
        assert data["source_locator"]["sha256"] == data["response_hash"]
        assert data["consumption_policy"]["mode"] == "capsule_first_targeted_retrieval"
        assert data["consumption_policy"]["never_claim_full_source_replacement"] is True
        assert data["attempts"] == 0
        assert data["response_hash"]
        assert rc.record({"assistant_response": response, "session_id": "test/session", "task": "交接"}) == 0
        assert len(list(Path(tmp).glob("*.json"))) == 1
        output = StringIO()
        with redirect_stdout(output):
            rc.pending({"session_id": "test/session", "task": "金融回测"})
        assert "pending=0" in output.getvalue()
        output = StringIO()
        with redirect_stdout(output):
            rc.pending({"session_id": "test/session", "task": "继续交接状态"})
        assert "pending=1 trigger=task_related" in output.getvalue()
        output = StringIO()
        with redirect_stdout(output):
            rc.pending({"session_id": "test/session", "task": "无关任务", "context_pressure": 0.9})
        assert "pending=0 reason=no_pending" in output.getvalue()
        output = StringIO()
        with redirect_stdout(output):
            rc.pending({"session_id": "test/session", "task": "交接审查", "context_pressure": 0.9})
        assert "pending=1 trigger=context_pressure" in output.getvalue()
        capsule = str(envelopes[0])
        first = rc.transition_status(capsule, "claim", worker_id="test-1")
        assert first["ok"] and first["claim_token"]
        failed = rc.transition_status(capsule, "fail", "synthetic_failure",
                                      claim_token=first["claim_token"])
        assert failed["status"] == rc.PENDING_STATUS
        second = rc.transition_status(capsule, "claim", worker_id="test-2")
        assert second["ok"] and second["claim_token"] != first["claim_token"]
        failed = rc.transition_status(capsule, "fail", "synthetic_failure_again",
                                      claim_token=second["claim_token"])
        assert failed["status"] == rc.FAILED_STATUS
        data = json.loads(envelopes[0].read_text(encoding="utf-8"))
        assert data["status"] == "FAILED"
        assert data["attempts"] == 2
        output = StringIO()
        with redirect_stdout(output):
            rc.pending({"session_id": "test/session"})
        assert "pending=0" in output.getvalue()
        rc.QUEUE = old
        rc.P1_LOG = old_log

    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / "rollout.jsonl"
        lines = [
            {"payload": {"type": "message", "role": "assistant", "phase": "commentary",
                         "content": [{"type": "output_text", "text": "中间过程"}]}},
            {"payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                         "content": [{"type": "output_text", "text": "最终回复"}]}},
            {"payload": {"type": "task_complete", "last_agent_message": "最终回复"}},
        ]
        rollout.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines), encoding="utf-8")
        assert rc.extract_final_response(str(rollout)) == "最终回复"

        usage_line = {"payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 123456},
            "last_token_usage": {"input_tokens": 5000},
            "model_context_window": 10000,
        }}}
        with rollout.open("a", encoding="utf-8") as handle:
            handle.write("\n" + json.dumps(usage_line, ensure_ascii=False))
        pressure = rc.estimate_context_pressure("unused", str(rollout))
        assert pressure["pressure"] == 0.5
        assert pressure["observed_tokens"] == 5000
        assert pressure["source"] == "codex_rollout.token_count"

        rollout2 = Path(tmp) / "rollout2.jsonl"
        rollout2.write_text(json.dumps({"payload": {"type": "message", "role": "assistant", "phase": "commentary",
                            "content": [{"type": "output_text", "text": "只有过程"}]}}, ensure_ascii=False), encoding="utf-8")
        assert rc.extract_final_response(str(rollout2)) == "只有过程"

        rollout3 = Path(tmp) / "rollout3.jsonl"
        rollout3.write_text(json.dumps({"payload": {"type": "task_complete", "last_agent_message": "完成消息"}},
                            ensure_ascii=False), encoding="utf-8")
        assert rc.extract_final_response(str(rollout3)) == "完成消息"

        assert rc.extract_final_response("") == ""
        assert rc.extract_final_response(str(Path(tmp) / "missing.jsonl")) == ""
        bad = Path(tmp) / "bad.jsonl"
        bad.write_text("not json\n\n{\"payload\": null}\n", encoding="utf-8")
        assert rc.extract_final_response(str(bad)) == ""

    print("test_response_capsule: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
