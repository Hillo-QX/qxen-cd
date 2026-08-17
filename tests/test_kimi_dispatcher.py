#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kimi Dispatcher（kimi mode）自动化测试
======================================

与 test_dispatcher.py 同构，覆盖：
 1. MCP Server 可以启动
 2. dispatch_next_task / request_decision / dispatcher_health 可以被 MCP Client 发现
 3. 合法 TASK / DONE / BLOCKED / DECISION schema 可以通过
 4. 有界批次必填字段（allowed_actions / stop_conditions）缺失或为空必须被拒绝
 5. malformed JSON / tasks 数组 / 多 TASK 必须被拒绝
 6. parse_stream_json 能正确解析 kimi CLI 的 NDJSON 输出
 7. 本模式无 API key：源码中不得出现 DEEPSEEK_API_KEY 读取逻辑
 8. 真实 K3 调用成功（消耗少量订阅额度）
 9. 实际返回 status=TASK 时只包含一个 task

运行：
    ../venv/bin/python -m pytest test_kimi_dispatcher.py -v
跳过真实 K3 调用（省额度）：
    KIMI_DISPATCHER_SKIP_REAL=1 ../venv/bin/python -m pytest test_kimi_dispatcher.py -v
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "kimi_dispatcher_mcp.py"
VENV_PY = ROOT / "venv" / "bin" / "python"

sys.path.insert(0, str(ROOT))
import kimi_dispatcher_mcp as kdsp  # noqa: E402

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SKIP_REAL = os.environ.get("KIMI_DISPATCHER_SKIP_REAL") == "1"
skip_real = pytest.mark.skipif(SKIP_REAL, reason="KIMI_DISPATCHER_SKIP_REAL=1，跳过真实 K3 调用")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _valid_task(task_id: str = "T001") -> dict:
    return {
        "status": "TASK",
        "task_id": task_id,
        "title": "创建并验证 hello.txt",
        "goal": "在临时工作区创建 hello.txt，内容为 hello 并验证",
        "reason": "目标的第一步（有界批次）",
        "inputs": [],
        "allowed_paths": [str(ROOT / "测试" / "临时工作区")],
        "forbidden_paths": [],
        "actions": ["写入 hello.txt"],
        "allowed_actions": [
            "创建 hello.txt，内容严格为 hello（不含换行）",
            "读取 hello.txt 并字节级验证内容 == hello",
        ],
        "stop_conditions": [
            "hello.txt 创建失败或内容 != hello",
            "出现需要操作 allowed_paths 之外路径的情况",
        ],
        "acceptance_criteria": ["文件存在", "内容等于 hello"],
        "do_not_do": ["不要修改其他文件"],
    }


def _valid_done() -> dict:
    return {"status": "DONE", "reason": "总体目标已全部完成"}


def _valid_blocked() -> dict:
    return {
        "status": "BLOCKED",
        "reason": "缺少必要信息",
        "required_information": ["目标项目路径"],
    }


def _valid_decision() -> dict:
    return {
        "status": "DECISION",
        "decision": "采用方案 A",
        "reason": "方案 A 兼容性更好且改动面最小",
        "instructions": ["按方案 A 修改 dispatch_next_task", "运行 pytest 验证"],
    }


def _run_async(coro):
    return asyncio.run(coro)


def _server_params():
    return StdioServerParameters(command=str(VENV_PY), args=[str(SRC)])


async def _tool_names():
    async with stdio_client(_server_params()) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            res = await session.list_tools()
            return [t.name for t in res.tools]


def _call_tool(tool, **args):
    async def _inner():
        async with stdio_client(_server_params()) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                res = await session.call_tool(tool, args)
                text = ""
                for item in res.content:
                    if hasattr(item, "text"):
                        text += item.text
                if getattr(res, "isError", False):
                    raise AssertionError(f"MCP 工具调用返回错误: {text}")
                return json.loads(text)
    return _run_async(_inner())


# ---------------------------------------------------------------------------
# 1/2. MCP Server 启动与工具发现
# ---------------------------------------------------------------------------

def test_mcp_server_starts_and_tool_discovered():
    names = _run_async(_tool_names())
    assert isinstance(names, list), "MCP Server 未能返回工具列表"
    assert "dispatch_next_task" in names, f"未发现 dispatch_next_task，实际: {names}"
    assert "request_decision" in names, f"未发现 request_decision，实际: {names}"
    assert "dispatcher_health" in names, f"未发现 dispatcher_health，实际: {names}"


def test_health_reports_kimi_mode():
    result = _call_tool("dispatcher_health")
    assert result["status"] == "OK"
    assert result["server"] == "kimi-dispatcher"
    assert result["kimi_cli_present"] is True, "kimi CLI 未找到"
    assert result["subscription_credentials_present"] is True, "订阅登录凭证未找到"
    assert "api_key" not in result, "kimi mode 不应再报告 api_key"


# ---------------------------------------------------------------------------
# 3. 合法 schema 可以通过
# ---------------------------------------------------------------------------

def test_valid_task_schema_passes():
    out = kdsp.validate_response(_valid_task("T001"), expected_task_id="T001")
    assert out["status"] == "TASK"
    assert out["task_id"] == "T001"


def test_valid_done_schema_passes():
    assert kdsp.validate_response(_valid_done())["status"] == "DONE"


def test_valid_blocked_schema_passes():
    assert kdsp.validate_response(_valid_blocked())["status"] == "BLOCKED"


def test_valid_decision_schema_passes():
    out = kdsp.validate_response(_valid_decision())
    assert out["status"] == "DECISION"
    assert "decision" in out
    assert "instructions" in out


# ---------------------------------------------------------------------------
# 4/5. 拒绝路径（与 DeepSeek 版校验规则一致）
# ---------------------------------------------------------------------------

def test_bounded_task_missing_allowed_actions_rejected():
    payload = _valid_task("T001")
    del payload["allowed_actions"]
    with pytest.raises(kdsp.ValidationError):
        kdsp.validate_response(payload, expected_task_id="T001")


def test_bounded_task_empty_stop_conditions_rejected():
    payload = _valid_task("T001")
    payload["stop_conditions"] = []
    with pytest.raises(kdsp.ValidationError):
        kdsp.validate_response(payload, expected_task_id="T001")


def test_malformed_json_rejected():
    for bad in ["这不是 JSON", '{"status": "TASK", "broken"', "{", ""]:
        with pytest.raises(kdsp.ValidationError):
            kdsp.extract_json(bad)


def test_tasks_array_rejected():
    payload = {"tasks": [_valid_task("T001"), _valid_task("T002")]}
    with pytest.raises(kdsp.ValidationError):
        kdsp.validate_response(payload)


def test_multiple_tasks_list_rejected():
    with pytest.raises(kdsp.ValidationError):
        kdsp.validate_response([_valid_task("T001"), _valid_task("T002")])


def test_missing_acceptance_criteria_rejected():
    payload = _valid_task("T001")
    del payload["acceptance_criteria"]
    with pytest.raises(kdsp.ValidationError):
        kdsp.validate_response(payload, expected_task_id="T001")


def test_duplicate_task_id_rejected():
    with pytest.raises(kdsp.ValidationError):
        kdsp.validate_response(
            _valid_task("T002"), expected_task_id="T002", completed_task_ids=["T002"]
        )


def test_wrong_task_id_rejected():
    with pytest.raises(kdsp.ValidationError):
        kdsp.validate_response(_valid_task("T007"), expected_task_id="T001")


def test_infer_next_task_id():
    assert kdsp.infer_next_task_id([]) == "T001"
    assert kdsp.infer_next_task_id([{"task_id": "T001"}]) == "T002"
    assert kdsp.infer_next_task_id([{"task_id": "T001"}, {"task_id": "T003"}]) == "T004"


# ---------------------------------------------------------------------------
# 6. parse_stream_json（kimi CLI NDJSON 输出解析）
# ---------------------------------------------------------------------------

def test_parse_stream_json_basic():
    stdout = "\n".join([
        json.dumps({"role": "meta", "type": "system.version", "version": "0.35.0"}),
        json.dumps({"role": "assistant", "content": '{"status":"DONE","reason":"ok"}'}),
        json.dumps({"role": "meta", "type": "session.resume_hint", "session_id": "x"}),
    ])
    assert kdsp.parse_stream_json(stdout) == '{"status":"DONE","reason":"ok"}'


def test_parse_stream_json_takes_last_assistant():
    stdout = "\n".join([
        json.dumps({"role": "assistant", "content": "first"}),
        json.dumps({"role": "assistant", "content": "second"}),
    ])
    assert kdsp.parse_stream_json(stdout) == "second"


def test_parse_stream_json_tolerates_non_json_lines():
    stdout = "kimi version 0.35.0\n" + json.dumps(
        {"role": "assistant", "content": "ok"}
    ) + "\n"
    assert kdsp.parse_stream_json(stdout) == "ok"


def test_parse_stream_json_no_assistant_rejected():
    with pytest.raises(kdsp.DispatcherError):
        kdsp.parse_stream_json('{"role":"meta","type":"system.version"}')
    with pytest.raises(kdsp.DispatcherError):
        kdsp.parse_stream_json("")


# ---------------------------------------------------------------------------
# 7. kimi mode 无 API key：源码不得读取 DEEPSEEK_API_KEY
# ---------------------------------------------------------------------------

def test_no_api_key_logic_in_source():
    content = SRC.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY" not in content
    assert "api.deepseek.com" not in content
    assert "OpenAI(" not in content


# ---------------------------------------------------------------------------
# 8/9. 真实 K3 调用（消耗少量订阅额度）
# ---------------------------------------------------------------------------

@skip_real
def test_real_k3_dispatch_success():
    result = _call_tool(
        "dispatch_next_task",
        overall_goal="无害测试：在临时工作区创建 hello.txt 并验证。",
        completed_tasks=[],
        current_state="",
        constraints="",
    )
    assert isinstance(result, dict), f"返回不是 dict: {result}"
    assert result.get("status") in ("TASK", "DONE", "BLOCKED"), (
        f"status 非法: {result.get('status')}"
    )
    assert "tasks" not in result, "响应包含被禁止的 tasks 数组"


@skip_real
def test_real_k3_task_returns_single_task_only():
    result = _call_tool(
        "dispatch_next_task",
        overall_goal="无害测试：在临时工作区创建 hello.txt，内容为 hello。",
        completed_tasks=[],
        current_state="",
        constraints="每次只能返回一个任务。",
    )
    assert isinstance(result, dict), f"返回不是单个 dict: {result}"
    assert result.get("status") == "TASK", f"期望首个任务为 TASK，实际: {result}"
    assert "task_id" in result, "TASK 缺少 task_id"
    assert "tasks" not in result, "禁止的 tasks 数组出现"
    kdsp.validate_response(result, expected_task_id=result["task_id"])


@skip_real
def test_real_k3_decision_success():
    result = _call_tool(
        "request_decision",
        question="hello.txt 的内容应该是 hello 还是 Hello？",
        context="overall_goal: 创建 hello.txt；verified_facts: 文件名固定为 hello.txt",
        options=["hello", "Hello"],
        constraints="只做一个明确决定",
    )
    assert isinstance(result, dict), f"返回不是 dict: {result}"
    assert result.get("status") in ("DECISION", "BLOCKED"), (
        f"status 非法: {result.get('status')}"
    )
