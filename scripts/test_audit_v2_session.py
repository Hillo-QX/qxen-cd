#!/usr/bin/env python3
"""Regression test: pre-baseline raw bypass is historical debt only."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import audit_v2_session as audit


def tool_state(name: str, chars: int) -> dict:
    return {"toolCall": {"function": {"name": name}}, "output": [{"content": "x" * chars}]}


def main() -> int:
    session_id = "audit-isolation-test"
    session = {
        "sessionId": session_id,
        "history": [
            {"toolCallStates": [tool_state("Bash", 3000)]},
            {"toolCallStates": [tool_state("Bash", 3000)]},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        old = audit.BASELINE_DIR
        audit.BASELINE_DIR = Path(tmp)
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        (Path(tmp) / f"audit_baseline_{digest}").write_text(json.dumps({
            "session_id": session_id,
            "tool_calls_before_window": 1,
            "available": True,
        }), encoding="utf-8")
        result = audit.audit_raw_bypass(session)
        assert result["verdict"] == "WARN"
        assert result["bypass_chars"] == 3000
        assert result["historical_bypass_chars"] == 3000
        assert result["baseline_tool_calls"] == 1
        audit.BASELINE_DIR = old

    window_result = audit.audit_token_economics({
        "history": [
            {"message": {"role": "assistant", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}},
            {"message": {"role": "assistant", "usage": {"prompt_tokens": 2000, "completion_tokens": 200}}},
        ]
    }, [], {"history_items_before_window": 1, "prompt_tokens_before_window": 1000,
            "completion_tokens_before_window": 100})
    assert window_result["prompt_tokens"] == 2000
    assert window_result["historical_prompt_tokens"] == 1000
    assert window_result["session_total_prompt_tokens"] == 3000
    assert window_result["current_window_history_items"] == 1
    assert window_result["verdict"] == "N/A"

    qxen_empty = audit.audit_qxen_economics({"sessionId": "missing-qxen"})
    assert qxen_empty["verdict"] == "N/A"
    assert qxen_empty["qxen_data"] == "absent"

    with tempfile.TemporaryDirectory() as tmp:
        old_qxen_log = audit.QXEN_AUDIT_LOG
        audit.QXEN_AUDIT_LOG = Path(tmp) / "qxen_cd_audit.jsonl"
        audit.QXEN_AUDIT_LOG.write_text(
            json.dumps({
                "event_type": "usage_observation",
                "session_id": session_id,
                "work_item_id": "wi-1",
                "usage_id": "use-1",
                "eval_window": "w1",
                "outcome": "success",
                "baseline_mode": "direct_gpt",
                "baseline_gpt_tokens": 1000,
                "qxen_gpt_tokens": 200,
                "qxen_local_tokens": 100,
                "gpt_review_tokens": 50,
                "fallback_replay_gpt_tokens": 25,
                "source_chars": 4000,
                "payload_chars": 400,
                "estimated": False,
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        qxen_summary = audit.audit_qxen_economics({"sessionId": session_id})
        assert qxen_summary["verdict"] == "PASS"
        assert qxen_summary["qxen_calls"] == 1
        assert qxen_summary["qxen_avoided_tokens"] == 625

        combined = audit.audit_token_economics({"history": []}, [], {}, qxen_summary)
        assert combined["qxen_calls"] == 1
        assert combined["combined_avoided_tokens"] == 625
        assert combined["combined_est_savings_usd"] > 0

        audit.QXEN_AUDIT_LOG.write_text(
            audit.QXEN_AUDIT_LOG.read_text(encoding="utf-8") +
            json.dumps({
                "event_type": "usage_observation",
                "session_id": "other-session",
                "work_item_id": "wi-2",
                "usage_id": "use-2",
                "eval_window": "w2",
                "outcome": "success",
                "baseline_mode": "direct_gpt",
                "baseline_gpt_tokens": 200,
                "qxen_gpt_tokens": 100,
                "qxen_local_tokens": 50,
                "gpt_review_tokens": 0,
                "fallback_replay_gpt_tokens": 0,
                "source_chars": 800,
                "payload_chars": 80,
                "estimated": True,
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        historical = audit.historical_qxen_economics()
        assert historical["scope"] == "historical_cumulative"
        assert historical["qxen_calls"] == 2
        assert historical["qxen_avoided_tokens"] == 625
        assert historical["qxen_estimated_avoided_tokens"] == 50
        audit.QXEN_AUDIT_LOG = old_qxen_log

    print("test_audit_v2_session: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
