#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v2 自动审计脚本测试（全部离线，合成会话数据）。

运行：
    ./venv/bin/python -m pytest 测试/test_audit_v2.py -v
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import audit_v2_session as av


def mcp_output(payload: dict) -> str:
    """模拟 MCP 工具输出的双层 JSON 结构。"""
    return json.dumps([{"type": "text", "text": json.dumps(payload)}],
                      ensure_ascii=False)


def make_session(tmp_path: Path, history: list) -> Path:
    p = tmp_path / "sess.json"
    p.write_text(json.dumps({"sessionId": "test-session", "history": history}),
                 encoding="utf-8")
    return p


def user(text: str) -> dict:
    return {"message": {"role": "user", "content": text}}


def assistant(text: str, model: str = "deepseek-v4-flash") -> dict:
    return {"message": {"role": "assistant", "content": text,
                        "usage": {"model": model, "prompt_tokens": 100,
                                  "completion_tokens": 10}}}


def tool_call(name: str, output: str, model: str = "deepseek-v4-flash") -> dict:
    return {
        "message": {"role": "assistant", "content": "",
                    "usage": {"model": model, "prompt_tokens": 100,
                              "completion_tokens": 10}},
        "toolCallStates": [{
            "toolCall": {"function": {"name": name, "arguments": "{}"}},
            "status": "done",
            "output": [{"content": output}],
        }],
    }


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "LOCAL_LOG", tmp_path / "no_log.log")
    monkeypatch.setattr(av, "AUDIT_DIR", tmp_path / "audit")
    yield


GOOD_HISTORY = [
    user("继续完成训练"),
    tool_call("local_health", mcp_output({"status": "OK"})),
    tool_call("dispatcher_health", mcp_output({"status": "OK"})),
    tool_call("dispatch_next_task", mcp_output({"status": "TASK"})),
    tool_call("local_summarize_files", mcp_output({"status": "OK", "summaries": []})),
    tool_call("local_distill", mcp_output({"status": "OK", "summary": "三行\n蒸馏\n结果"})),
    assistant("完成"),
]


class TestGoodSession:
    def test_overall_pass(self, tmp_path):
        report = av.audit(make_session(tmp_path, GOOD_HISTORY))
        assert report["overall"] in ("PASS", "WARN")  # WARN 仅可能来自无日志窗口
        assert report["checks"]["model_compliance"]["verdict"] == "PASS"
        assert report["checks"]["boot_check"]["verdict"] == "PASS"
        assert report["checks"]["raw_bypass"]["verdict"] == "PASS"
        assert report["checks"]["schema_limits"]["verdict"] == "PASS"

    def test_pipeline_counts(self, tmp_path):
        report = av.audit(make_session(tmp_path, GOOD_HISTORY))
        calls = report["checks"]["local_pipeline"]["calls_in_session"]
        assert calls["local_health"] == 1 and calls["local_distill"] == 1


class TestViolations:
    def test_wrong_model_fails(self, tmp_path):
        h = GOOD_HISTORY + [assistant("乱入", model="qwen3.5:9b")]
        report = av.audit(make_session(tmp_path, h))
        c = report["checks"]["model_compliance"]
        assert c["verdict"] == "FAIL"
        assert c["violations"] == {"qwen3.5:9b": 1}
        assert report["overall"] == "FAIL"

    def test_missing_boot_fails(self, tmp_path):
        h = [user("干活"), tool_call("dispatch_next_task", mcp_output({"status": "TASK"}))]
        report = av.audit(make_session(tmp_path, h))
        assert report["checks"]["boot_check"]["verdict"] == "FAIL"

    def test_raw_bypass_warn_and_fail(self, tmp_path):
        h = GOOD_HISTORY + [tool_call("Read", "x" * 5000)]
        report = av.audit(make_session(tmp_path, h))
        c = report["checks"]["raw_bypass"]
        assert c["verdict"] == "WARN" and c["bypass_chars"] == 5000

        h2 = GOOD_HISTORY + [tool_call("Bash", "y" * 25000)]
        report2 = av.audit(make_session(tmp_path, h2))
        assert report2["checks"]["raw_bypass"]["verdict"] == "FAIL"
        assert report2["overall"] == "FAIL"

    def test_local_outputs_not_counted_as_bypass(self, tmp_path):
        h = GOOD_HISTORY + [tool_call("local_distill", mcp_output({"summary": "z" * 5000}))]
        report = av.audit(make_session(tmp_path, h))
        assert report["checks"]["raw_bypass"]["verdict"] == "PASS"

    def test_schema_violation_detected(self, tmp_path):
        long_summary = "\n".join(f"行{i}" for i in range(30))
        h = GOOD_HISTORY + [tool_call("local_distill", mcp_output({"summary": long_summary}))]
        report = av.audit(make_session(tmp_path, h))
        c = report["checks"]["schema_limits"]
        assert c["verdict"] == "FAIL"
        assert c["violations"][0]["tool"] == "local_distill"

    def test_bad_classify_label_detected(self, tmp_path):
        h = GOOD_HISTORY + [tool_call("local_classify", mcp_output({"label": "MAYBE"}))]
        report = av.audit(make_session(tmp_path, h))
        assert report["checks"]["schema_limits"]["verdict"] == "FAIL"


class TestChatOnlySession:
    def test_no_work_no_boot_required(self, tmp_path):
        h = [user("你好"), assistant("你好！")]
        report = av.audit(make_session(tmp_path, h))
        assert report["checks"]["boot_check"]["verdict"] == "PASS"
        assert report["overall"] in ("PASS", "WARN")


class TestTokenEconomics:
    def test_totals_and_baseline(self, tmp_path):
        report = av.audit(make_session(tmp_path, GOOD_HISTORY))
        c = report["checks"]["token_economics"]
        assert c["prompt_tokens"] == 600  # 6 条带 usage 的 assistant 消息
        assert c["baseline_f0b59135"]["ds_prompt_tokens"] == 5_247_396
        assert c["llm_calls"] == 6
        assert c["local_calls"] == 2  # health 不计入经济引擎

    def test_no_local_calls_warns(self, tmp_path):
        h = [user("你好"), assistant("你好！")]
        report = av.audit(make_session(tmp_path, h))
        c = report["checks"]["token_economics"]
        assert c["verdict"] == "WARN"
        assert c["avoided_retransmit_tokens"] == 0

    def test_avoided_retransmit_math(self, tmp_path, monkeypatch):
        # 场景：4 次 LLM 调用；第 2 次调用时 local_distill 输入 4000 字符、
        # 输出 400 字符。避免进入历史 3600 字符 = 900 token，
        # 后续还有 2 次调用 -> avoided = 900 × 2 = 1800。
        h = [
            user("干活"),
            tool_call("local_health", mcp_output({"status": "OK"})),       # call 1
            tool_call("local_distill", mcp_output({"status": "OK",
                                                   "summary": "x" * 400})),  # call 2
            assistant("中间"),                                             # call 3
            assistant("结束"),                                             # call 4
        ]
        sess = make_session(tmp_path, h)
        log = tmp_path / "local_qwen.log"
        from datetime import datetime, timezone
        ts = datetime.fromtimestamp(sess.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        log.write_text(json.dumps({
            "time": ts, "tool": "local_distill", "status": "OK",
            "input_chars": 4000, "output_chars": 400}), encoding="utf-8")
        monkeypatch.setattr(av, "LOCAL_LOG", log)
        report = av.audit(sess)
        c = report["checks"]["token_economics"]
        assert c["llm_calls"] == 4
        assert c["local_calls"] == 1
        assert c["avoided_retransmit_tokens"] == 1800
        assert c["distill_ratio"] == "10.0%"
        assert c["est_savings_usd"] == round(1800 * av.IMPLIED_RATE, 4)
        assert c["verdict"] == "PASS"

    def test_weak_distill_ratio_warns(self, tmp_path, monkeypatch):
        h = GOOD_HISTORY[:]
        sess = make_session(tmp_path, h)
        log = tmp_path / "local_qwen.log"
        from datetime import datetime, timezone
        ts = datetime.fromtimestamp(sess.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        # 输入 1000 输出 600 -> 蒸馏比 60% -> WARN
        entries = [
            {"time": ts, "tool": "local_summarize_files", "status": "OK",
             "input_chars": 500, "output_chars": 300},
            {"time": ts, "tool": "local_distill", "status": "OK",
             "input_chars": 500, "output_chars": 300},
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
        monkeypatch.setattr(av, "LOCAL_LOG", log)
        report = av.audit(sess)
        assert report["checks"]["token_economics"]["verdict"] == "WARN"


class TestLogWindow:
    def test_fallback_rate_from_log(self, tmp_path, monkeypatch):
        log = tmp_path / "local_qwen.log"
        sess = make_session(tmp_path, GOOD_HISTORY)
        # 会话文件时间窗内的 4 条调用，2 条 FALLBACK -> rate 0.5 -> WARN
        from datetime import datetime, timezone
        ts = datetime.fromtimestamp(sess.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        lines = [json.dumps({"time": ts, "tool": "local_distill",
                             "status": s, "input_chars": 100})
                 for s in ("OK", "OK", "FALLBACK", "FALLBACK")]
        log.write_text("\n".join(lines), encoding="utf-8")
        monkeypatch.setattr(av, "LOCAL_LOG", log)
        report = av.audit(sess)
        c = report["checks"]["local_pipeline"]
        assert c["log_calls"] == 4 and c["log_fallbacks"] == 2
        assert c["fallback_rate"] == 0.5 and c["verdict"] == "WARN"
