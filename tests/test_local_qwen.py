#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LocalQwen MCP Server 测试。

默认全部离线（monkeypatch _chat，不需要 ollama、不耗任何额度）。
设置 LOCAL_QWEN_RUN_REAL=1 时追加真实 ollama 调用测试（需本地 qwen3.5:9b 在线）。

运行：
    ./venv/bin/python -m pytest 测试/test_local_qwen.py -v
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import local_qwen_mcp as lq


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def isolate_log(tmp_path, monkeypatch):
    """审计日志重定向到临时目录，避免污染真实 日志/local_qwen.log。"""
    monkeypatch.setattr(lq, "LOG_PATH", tmp_path / "local_qwen.log")
    yield


@pytest.fixture
def fake_chat(monkeypatch):
    """可控的 _chat 替身：按队列返回文本或抛 QwenError。"""
    class Calls(dict):
        pass

    calls = Calls(n=0, prompts=[])
    queue = []

    def set_queue(items):
        queue.clear()
        queue.extend(items)

    def fake(prompt, num_predict):
        calls["n"] += 1
        calls["prompts"].append(prompt)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(lq, "_chat", fake)
    calls.set_queue = set_queue
    return calls


# ---------------------------------------------------------------- 纯函数

class TestClampLines:
    def test_within_limit_unchanged(self):
        assert lq._clamp_lines("a\nb", 3) == "a\nb"

    def test_over_limit_truncated_with_marker(self):
        out = lq._clamp_lines("1\n2\n3\n4\n5", 2)
        assert out.startswith("1\n2")
        assert "截断" in out and "5" in out


class TestCapText:
    def test_short_text_not_truncated(self):
        text, truncated = lq._cap_text("abc")
        assert text == "abc" and truncated is False

    def test_long_text_truncated(self, monkeypatch):
        monkeypatch.setattr(lq, "MAX_INPUT_CHARS", 10)
        text, truncated = lq._cap_text("x" * 100)
        assert len(text) == 10 and truncated is True


# ---------------------------------------------------------------- _run_tool 骨架

class TestRunTool:
    def test_ok_first_attempt(self, monkeypatch):
        monkeypatch.setattr(lq, "_chat", lambda p, n: "ok")
        result = lq._run_tool("t", 5, lambda a: ({"summary": "ok"}, 1))
        assert result["status"] == "OK"

    def test_retry_then_ok(self):
        attempts = []

        def fn(attempt):
            attempts.append(attempt)
            if attempt == 1:
                raise lq.QwenError("boom")
            return ({"summary": "ok"}, 1)

        result = lq._run_tool("t", 5, fn)
        assert result["status"] == "OK" and attempts == [1, 2]

    def test_fallback_after_two_failures(self):
        def fn(attempt):
            raise lq.QwenError(f"fail {attempt}")

        result = lq._run_tool("t", 5, fn)
        assert result["status"] == "FALLBACK"
        assert "fail 2" in result["reason"]
        assert "instructions" in result


class TestHealthCache:
    def test_valid_cache_skips_ollama_probe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lq, "HEALTH_CACHE_PATH", tmp_path / "health.json")
        lq._save_health_cache({"server": "local-qwen", "model": lq.MODEL,
                               "probe": "好", "latency_s": 0.1})

        def no_network(*args, **kwargs):
            raise AssertionError("valid health cache must skip network")

        monkeypatch.setattr(lq.httpx, "get", no_network)
        monkeypatch.setattr(lq, "_chat", no_network)
        result = run(lq.local_health())

        assert result["status"] == "OK"
        assert result["cached"] is True
        assert result["next_probe_after_s"] > 0

    def test_force_bypasses_valid_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lq, "HEALTH_CACHE_PATH", tmp_path / "health.json")
        lq._save_health_cache({"server": "local-qwen", "model": lq.MODEL,
                               "probe": "旧", "latency_s": 0.1})
        class Response:
            def json(self):
                return {"models": [{"name": lq.MODEL}]}
        monkeypatch.setattr(lq.httpx, "get", lambda *args, **kwargs: Response())
        monkeypatch.setattr(lq, "_chat", lambda *args, **kwargs: "好")
        result = run(lq.local_health(force=True))

        assert result["status"] == "OK"
        assert result["cached"] is False

class TestRunToolAudit:
    def test_audit_log_written(self):
        lq._run_tool("t_audit", 5, lambda a: ({"x": 1}, 1))
        lines = lq.LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # START + OK
        start, done = (json.loads(l) for l in lines)
        assert start["status"] == "START" and start["tool"] == "t_audit"
        assert done["tool"] == "t_audit" and done["status"] == "OK"
        assert "output_chars" in done and done["output_chars"] > 0

    def test_audit_log_written_on_fallback(self):
        def fn(a):
            raise lq.QwenError("x")

        lq._run_tool("t_fb", 5, fn)
        lines = lq.LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # START + FALLBACK
        entry = json.loads(lines[-1])
        assert entry["status"] == "FALLBACK"


# ---------------------------------------------------------------- 各工具

class TestLocalDistill:
    def test_ok(self, fake_chat):
        fake_chat.set_queue(["事实一\n事实二"])
        result = run(lq.local_distill("很长的原文", "找错误", max_lines=5))
        assert result["status"] == "OK"
        assert result["summary"] == "事实一\n事实二"
        assert result["input_chars"] == 5

    def test_output_clamped_to_max_lines(self, fake_chat):
        fake_chat.set_queue(["\n".join(f"行{i}" for i in range(10))])
        result = run(lq.local_distill("原文", "目标", max_lines=3))
        assert result["status"] == "OK"
        assert result["summary"].count("\n") <= 3  # 3 行 + 截断标记
        assert "截断" in result["summary"]

    def test_fallback(self, fake_chat):
        fake_chat.set_queue([lq.QwenError("down"), lq.QwenError("down")])
        result = run(lq.local_distill("原文", "目标"))
        assert result["status"] == "FALLBACK"
        assert fake_chat["n"] == 2

    def test_path_mode(self, tmp_path, fake_chat):
        source = tmp_path / "long.txt"
        source.write_text("路径原文", encoding="utf-8")
        fake_chat.set_queue(["路径摘要"])
        result = run(lq.local_distill(goal="摘要", source_path=str(source)))
        assert result["status"] == "OK"
        assert result["input_mode"] == "local_path"
        assert result["source_path"] == str(source.resolve())
        assert result["source_sha256"]


class TestLocalSummarizeFiles:
    def test_ok_and_missing_file(self, tmp_path, fake_chat):
        f = tmp_path / "a.txt"
        f.write_text("内容", encoding="utf-8")
        fake_chat.set_queue(["摘要"])
        result = run(lq.local_summarize_files([str(f), str(tmp_path / "nope.txt")]))
        assert result["status"] == "OK"
        s0, s1 = result["summaries"]
        assert s0["status"] == "OK" and s0["summary"] == "摘要"
        assert s1["status"] == "ERROR"

    def test_all_fail_is_fallback(self, fake_chat):
        fake_chat.set_queue([lq.QwenError("x"), lq.QwenError("x")])
        result = run(lq.local_summarize_files(["/nonexistent/a", "/nonexistent/b"]))
        assert result["status"] == "FALLBACK"

    def test_file_count_capped(self, monkeypatch, tmp_path, fake_chat):
        monkeypatch.setattr(lq, "MAX_FILES_PER_CALL", 2)
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        fake_chat.set_queue(["s1", "s2"])
        result = run(lq.local_summarize_files([str(f)] * 5))
        assert result["file_count"] == 2


class TestLocalExtractFailure:
    def test_ok(self, fake_chat):
        fake_chat.set_queue(['前缀 {"test": "t1", "expected": "e", "actual": "a"} 后缀'])
        result = run(lq.local_extract_failure("日志"))
        assert result["status"] == "OK"
        assert (result["test"], result["expected"], result["actual"]) == ("t1", "e", "a")

    def test_invalid_json_retries_then_fallback(self, fake_chat):
        fake_chat.set_queue(["没有JSON", lq.QwenError("invalid JSON: x")])
        result = run(lq.local_extract_failure("日志"))
        # 第一次无 JSON -> QwenError；第二次返回异常 -> FALLBACK
        assert result["status"] == "FALLBACK"

    def test_missing_keys_fallback(self, fake_chat):
        fake_chat.set_queue(['{"test": "t"}', '{"test": "t"}'])
        result = run(lq.local_extract_failure("日志"))
        assert result["status"] == "FALLBACK"
        assert "missing keys" in result["reason"]

    def test_log_path_mode(self, tmp_path, fake_chat):
        source = tmp_path / "pytest.log"
        source.write_text("FAILED expected=1 actual=2", encoding="utf-8")
        fake_chat.set_queue(['{"test":"t","expected":"1","actual":"2"}'])
        result = run(lq.local_extract_failure(log_path=str(source)))
        assert result["status"] == "OK"
        assert result["input_mode"] == "local_path"
        assert result["source_sha256"]


class TestLocalClassify:
    @pytest.mark.parametrize("label", lq.LABELS)
    def test_valid_labels(self, fake_chat, label):
        fake_chat.set_queue([label])
        result = run(lq.local_classify("上下文块"))
        assert result["status"] == "OK" and result["label"] == label

    def test_label_with_trailing_text_accepted(self, fake_chat):
        fake_chat.set_queue(["KEEP 因为…"])
        result = run(lq.local_classify("块"))
        assert result["label"] == "KEEP"

    def test_invalid_label_fallback(self, fake_chat):
        fake_chat.set_queue(["不知道", "也不知道"])
        result = run(lq.local_classify("块"))
        assert result["status"] == "FALLBACK"
        assert "invalid label" in result["reason"]

    def test_source_path_mode(self, tmp_path, fake_chat):
        source = tmp_path / "context.txt"
        source.write_text("需保留的上下文", encoding="utf-8")
        fake_chat.set_queue(["KEEP"])
        result = run(lq.local_classify(source_path=str(source)))
        assert result["status"] == "OK"
        assert result["input_mode"] == "local_path"


class TestResearchDelegationTools:
    def test_research_log_distill_schema(self, fake_chat):
        fake_chat.set_queue(['{"status":"PASS","summary":"完成","key_metrics":["IC=0.04"],"evidence":["日志显示成功"],"next_action":"归档"}'])
        result = run(lq.local_research_log_distill("日志"))
        assert result["status"] == "PASS"
        assert result["key_metrics"] == ["IC=0.04"]

    def test_research_log_path_mode(self, tmp_path, fake_chat):
        source = tmp_path / "research.log"
        source.write_text("研究任务完成", encoding="utf-8")
        fake_chat.set_queue(['{"status":"PASS","summary":"完成","key_metrics":[],"evidence":[],"next_action":"归档"}'])
        result = run(lq.local_research_log_distill(log_path=str(source)))
        assert result["status"] == "PASS"
        assert result["input_mode"] == "local_path"


class TestMonitorPathInput:
    def test_log_path_mode(self, tmp_path, fake_chat):
        source = tmp_path / "monitor.log"
        source.write_text("CUDA out of memory", encoding="utf-8")
        fake_chat.set_queue(['{"verdict":"recoverable","failure_cluster":"oom","evidence":["OOM"],"alert":"内存不足"}'])
        result = run(lq.local_monitor_analyze("训练异常", log_path=str(source)))
        assert result["status"] == "OK"
        assert result["input_mode"] == "local_path"

    def test_factor_generate_exact_count(self, fake_chat):
        fake_chat.set_queue(['{"hypothesis":"反转","candidates":['
                             '{"expression":"sub(ma(close,5),ma(close,20))","mechanism":"反转","rationale":"价差"},'
                             '{"expression":"delta(volume,5)","mechanism":"量能","rationale":"变化"}]}'])
        result = run(lq.local_factor_generate("反转", ["close", "volume"], ["ma", "sub", "delta"], count=2))
        assert result["status"] == "OK"
        assert result["count"] == 2
        assert result["candidates"][0]["hard_flags"] == []

    def test_factor_review_hard_flag_cannot_keep(self, fake_chat):
        fake_chat.set_queue(['{"reviews":[{"expression":"lead(close,5)","decision":"KEEP","risk_flags":[],"reason":"看起来合理"}]}'])
        result = run(lq.local_factor_review([{"expression":"lead(close,5)"}]))
        assert result["status"] == "OK"
        assert result["reviews"][0]["decision"] == "REVIEW"
        assert "possible_lookahead_token" in result["reviews"][0]["risk_flags"]

    def test_failure_cluster_schema(self, fake_chat):
        fake_chat.set_queue(['{"clusters":[{"name":"字段缺失","count":2,"failure_ids":["F1","F2"],"evidence":["missing"],"pattern":"字段不存在"}],"unclustered_ids":[],"summary":"一类"}'])
        result = run(lq.local_failure_cluster([{"id":"F1"},{"id":"F2"}]))
        assert result["status"] == "OK"
        assert result["clusters"][0]["failure_ids"] == ["F1", "F2"]


# ---------------------------------------------------------------- MCP 注册

def test_all_tools_registered():
    names = set(lq.mcp._tool_manager._tools.keys())
    assert names == {"local_health", "local_distill", "local_summarize_files",
                     "local_extract_failure", "local_classify",
                     "local_research_log_distill", "local_factor_generate",
                     "local_factor_review", "local_failure_cluster",
                     "local_monitor_analyze"}


# ---------------------------------------------------------------- 真实 ollama（可选）

@pytest.mark.skipif(os.environ.get("LOCAL_QWEN_RUN_REAL") != "1",
                    reason="需 LOCAL_QWEN_RUN_REAL=1 且本地 ollama qwen3.5:9b 在线")
class TestRealOllama:
    def test_health(self):
        result = run(lq.local_health())
        assert result["status"] == "OK", result.get("reason")

    def test_classify_real(self):
        result = run(lq.local_classify("import os\nimport sys\n\n# 工具导入，与任务无关"))
        assert result["status"] == "OK"
        assert result["label"] in lq.LABELS

    def test_distill_real(self):
        result = run(lq.local_distill("日志" * 500, "找错误", max_lines=3))
        assert result["status"] == "OK"
        assert len(result["summary"].splitlines()) <= 4  # 3 行 + 可能的截断标记
