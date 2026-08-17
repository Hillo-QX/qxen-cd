#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek Dispatcher 自动化测试
==============================

覆盖：
 1. MCP Server 可以启动
 2. dispatch_next_task / request_decision / dispatcher_health 可以被 MCP Client 发现
 3. 合法 TASK（有界批次）schema 可以通过
 4. 合法 DONE schema 可以通过
 5. 合法 BLOCKED schema 可以通过
 6. 合法 DECISION schema 可以通过
 7. 有界批次必填字段（allowed_actions / stop_conditions）缺失或为空必须被拒绝
 8. malformed JSON 被拒绝
 9. {"tasks": [...]} 必须被拒绝
10. 一次返回多个 TASK 必须被拒绝
11. TASK 缺少 acceptance_criteria 必须被拒绝
12. API key 不出现在 stdout / 日志
13. 真实 DeepSeek API 调用成功
14. 实际返回 status=TASK 时只包含一个 task

运行：
    ../venv/bin/python -m pytest test_dispatcher.py -v
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "deepseek_dispatcher_mcp.py"
VENV_PY = ROOT / "venv" / "bin" / "python"
LOG_FILE = ROOT / "日志" / "dispatcher.log"
ENV_FILE = ROOT / ".env.local"

sys.path.insert(0, str(ROOT))
import deepseek_dispatcher_mcp as dsp  # noqa: E402

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _load_key() -> str:
    from dotenv import dotenv_values
    return dotenv_values(ENV_FILE).get("DEEPSEEK_API_KEY", "")


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


def _call_tool(overall_goal, completed_tasks=None, current_state=None, constraints=None):
    async def _inner():
        async with stdio_client(_server_params()) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                res = await session.call_tool(
                    "dispatch_next_task",
                    {
                        "overall_goal": overall_goal,
                        "completed_tasks": completed_tasks or [],
                        "current_state": current_state or "",
                        "constraints": constraints or "",
                    },
                )
                # 提取文本内容（JSON 字符串）
                text = ""
                for item in res.content:
                    if hasattr(item, "text"):
                        text += item.text
                if getattr(res, "isError", False):
                    raise AssertionError(f"MCP 工具调用返回错误: {text}")
                return json.loads(text)
    return _run_async(_inner())


# ---------------------------------------------------------------------------
# 1. MCP Server 可以启动
# 2. dispatch_next_task 可以被 MCP Client 发现
# ---------------------------------------------------------------------------

def test_mcp_server_starts_and_tool_discovered():
    names = _run_async(_tool_names())
    assert isinstance(names, list), "MCP Server 未能返回工具列表"
    assert "dispatch_next_task" in names, f"未发现 dispatch_next_task，实际: {names}"
    assert "request_decision" in names, f"未发现 request_decision，实际: {names}"
    assert "dispatcher_health" in names, f"未发现 dispatcher_health，实际: {names}"


# ---------------------------------------------------------------------------
# 3/4/5. 合法 schema 可以通过
# ---------------------------------------------------------------------------

def test_valid_task_schema_passes():
    out = dsp.validate_response(_valid_task("T001"), expected_task_id="T001")
    assert out["status"] == "TASK"
    assert out["task_id"] == "T001"


def test_valid_done_schema_passes():
    out = dsp.validate_response(_valid_done())
    assert out["status"] == "DONE"


def test_valid_blocked_schema_passes():
    out = dsp.validate_response(_valid_blocked())
    assert out["status"] == "BLOCKED"


def test_valid_decision_schema_passes():
    out = dsp.validate_response(_valid_decision())
    assert out["status"] == "DECISION"
    assert "decision" in out
    assert "instructions" in out


def test_decision_missing_decision_field_rejected():
    payload = _valid_decision()
    del payload["decision"]
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload)


def test_decision_empty_instructions_rejected():
    payload = _valid_decision()
    payload["instructions"] = []
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload)


# ---------------------------------------------------------------------------
# 有界批次（bounded batch）字段校验
# ---------------------------------------------------------------------------

def test_bounded_task_missing_allowed_actions_rejected():
    payload = _valid_task("T001")
    del payload["allowed_actions"]
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T001")


def test_bounded_task_missing_stop_conditions_rejected():
    payload = _valid_task("T001")
    del payload["stop_conditions"]
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T001")


def test_bounded_task_empty_allowed_actions_rejected():
    payload = _valid_task("T001")
    payload["allowed_actions"] = []
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T001")


def test_bounded_task_empty_stop_conditions_rejected():
    payload = _valid_task("T001")
    payload["stop_conditions"] = []
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T001")


# ---------------------------------------------------------------------------
# 6. malformed JSON 被拒绝
# ---------------------------------------------------------------------------

def test_malformed_json_rejected():
    for bad in ["这不是 JSON", '{"status": "TASK", "broken"', "{", ""]:
        with pytest.raises(dsp.ValidationError):
            dsp.extract_json(bad)


def test_plain_text_rejected():
    with pytest.raises(dsp.ValidationError):
        dsp.extract_json("好的，我来完成整个任务，先做 A 再做 B。")


# ---------------------------------------------------------------------------
# 7. {"tasks": [...]} 必须被拒绝
# ---------------------------------------------------------------------------

def test_tasks_array_rejected():
    payload = {"tasks": [_valid_task("T001"), _valid_task("T002")]}
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload)


def test_task_list_key_rejected():
    payload = {"task_list": [_valid_task("T001")]}
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload)


# ---------------------------------------------------------------------------
# 8. 一次返回多个 TASK 必须被拒绝
# ---------------------------------------------------------------------------

def test_multiple_tasks_list_rejected():
    # 响应整体是一个数组（多个 TASK）
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response([_valid_task("T001"), _valid_task("T002")])


def test_multiple_tasks_in_text_rejected():
    text = json.dumps([_valid_task("T001"), _valid_task("T002")])
    with pytest.raises(dsp.ValidationError):
        dsp.extract_json(text)  # 提取结果不是 dict


def test_invalid_status_rejected():
    payload = _valid_task("T001")
    payload["status"] = "ERROR"
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload)


# ---------------------------------------------------------------------------
# 9. TASK 缺少 acceptance_criteria 必须被拒绝
# ---------------------------------------------------------------------------

def test_missing_acceptance_criteria_rejected():
    payload = _valid_task("T001")
    del payload["acceptance_criteria"]
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T001")


def test_empty_acceptance_criteria_rejected():
    payload = _valid_task("T001")
    payload["acceptance_criteria"] = []
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T001")


def test_missing_required_field_rejected():
    payload = _valid_task("T001")
    del payload["goal"]
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T001")


def test_duplicate_task_id_rejected():
    payload = _valid_task("T002")
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T002", completed_task_ids=["T002"])


def test_wrong_task_id_rejected():
    payload = _valid_task("T007")
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T001")


def test_illegal_task_id_rejected():
    payload = _valid_task("X99")
    with pytest.raises(dsp.ValidationError):
        dsp.validate_response(payload, expected_task_id="T001")


# ---------------------------------------------------------------------------
# 10/11. API key 保护
# ---------------------------------------------------------------------------

def test_api_key_not_in_source_code_or_docs():
    key = _load_key()
    assert key, "测试前提：.env.local 中存在 API key"
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() == ENV_FILE.resolve():
            continue
        if path.name.startswith(".env.local.bak"):
            continue  # 密钥备份文件（权限 600，与 .env.local 同级别保护）
        if ".venv" in path.parts or "venv" in path.parts:
            continue
        if path.suffix in (".pyc", ".log"):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        assert key not in content, f"API key 泄露到文件: {path}"
    # 明确检查 README / 测试 / 主程序
    for f in (ROOT / "README.md", SRC, Path(__file__)):
        assert key not in f.read_text(encoding="utf-8"), f"API key 泄露: {f}"


def test_api_key_not_in_server_stdout():
    key = _load_key()
    proc = subprocess.Popen(
        [str(VENV_PY), str(SRC)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(2)
    proc.terminate()
    try:
        out, err = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    combined = out + err
    assert key not in combined, "API key 出现在 MCP Server stdout/stderr"
    assert key[:8] not in combined, "API key 前缀出现在 MCP Server 输出"


def test_api_key_not_in_logs():
    key = _load_key()
    # 先触发一次真实调用，确保日志文件有内容（测试 12 会执行；此处兜底）
    if not LOG_FILE.exists():
        pytest.skip("日志文件尚不存在")
    content = LOG_FILE.read_text(encoding="utf-8", errors="ignore")
    assert key not in content, "API key 出现在日志中"
    assert key[:8] not in content, "API key 前缀出现在日志中"


# ---------------------------------------------------------------------------
# 12. 真实 DeepSeek API 调用成功
# 13. status=TASK 时只包含一个 task
# ---------------------------------------------------------------------------

def test_real_deepseek_api_call_success():
    result = _call_tool("无害测试：在临时工作区创建 hello.txt 并验证。")
    assert isinstance(result, dict), f"返回不是 dict: {result}"
    assert result.get("status") in ("TASK", "DONE", "BLOCKED"), (
        f"status 非法: {result.get('status')}"
    )
    # 关键断言：单个对象，没有 tasks 数组
    assert "tasks" not in result, "响应包含被禁止的 tasks 数组"


def test_real_task_returns_single_task_only():
    result = _call_tool(
        "无害测试：在临时工作区创建 hello.txt，内容为 hello。",
        constraints="每次只能返回一个任务。",
    )
    assert isinstance(result, dict), f"返回不是单个 dict: {result}"
    assert result.get("status") == "TASK", f"期望首个任务为 TASK，实际: {result}"
    # 只包含一个 task：单 dict + 有且仅有一个 task_id + 无 tasks 数组
    assert "task_id" in result, "TASK 缺少 task_id"
    assert "tasks" not in result, "禁止的 tasks 数组出现"
    assert "task_list" not in result, "禁止的 task_list 出现"
    # 校验通过（与调度器内部一致的严格校验）
    dsp.validate_response(result, expected_task_id=result["task_id"])


# ---------------------------------------------------------------------------
# 任务编号推断
# ---------------------------------------------------------------------------

def test_infer_next_task_id():
    assert dsp.infer_next_task_id([]) == "T001"
    assert dsp.infer_next_task_id([{"task_id": "T001"}]) == "T002"
    assert dsp.infer_next_task_id([{"task_id": "T001"}, {"task_id": "T002"}]) == "T003"
    assert dsp.infer_next_task_id([{"task_id": "T001"}, {"task_id": "T003"}]) == "T004"


def test_infer_next_task_id_str_summary_format():
    """userRules 规定的字符串蒸馏摘要格式（"T001: PASS xxx"）。"""
    assert dsp.infer_next_task_id(["T001: PASS 数据扩充完成"]) == "T002"
    assert dsp.infer_next_task_id(["T001: PASS a", "T002: PASS b"]) == "T003"
    assert dsp.infer_next_task_id(["T005: PASS 校验"]) == "T006"
    assert dsp.infer_next_task_id(["T001: PASS a", {"task_id": "T003", "status": "PASS"}]) == "T004"
    assert dsp.infer_next_task_id(["完成了一些工作（无编号）"]) == "T001"


def test_build_dispatcher_prompt_accepts_str_summary():
    """字符串摘要格式不得崩溃（回归：'str' object has no attribute 'get'）。"""
    p = dsp.build_dispatcher_prompt(
        "goal", ["T001: PASS a", "T002: PASS b"]
    )
    assert "T001: PASS a" in p
    assert "T002: PASS b" in p
    # dict 格式仍向后兼容
    p2 = dsp.build_dispatcher_prompt(
        "goal", [{"task_id": "T001", "status": "PASS", "summary": "xxx"}]
    )
    assert "T001: PASS xxx" in p2
