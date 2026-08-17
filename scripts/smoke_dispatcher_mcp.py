#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deterministic Smoke Dispatcher MCP Server
==========================================

与 deepseek_dispatcher_mcp.py 完全同构（工具名 / TASK schema / stdio 协议一致），
但不调用 DeepSeek API，无任何外部依赖，固定状态机返回任务。

用途：隔离故障层。
    deterministic smoke FAIL  -> Continue / MCP / Executor orchestration 有问题
    deterministic smoke PASS  -> 但 DeepSeek Dispatcher FAIL -> 问题只在 Dispatcher/DeepSeek 层

状态机（每次调用只返回唯一一个 TASK / DONE）：
    无 completed_tasks                  -> T001 创建 alpha.txt  (内容 ALPHA_OK)
    T001 完成                           -> T002 创建 beta.txt   (内容 BETA_OK)
    T001,T002 完成                      -> T003 创建 result.txt (内容 SMOKE_DONE)
    T001,T002,T003 全部完成             -> DONE

运行：
    ./venv/bin/python smoke_dispatcher_mcp.py
"""

import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("smoke-dispatcher")

WORKSPACE = "/Users/hillo/Desktop/任务调度器/测试/smoke_workspace"

TASK_T001 = {
    "status": "TASK",
    "task_id": "T001",
    "title": "create alpha.txt",
    "goal": "在 smoke_workspace 创建 alpha.txt，内容严格等于 ALPHA_OK",
    "reason": "smoke test 第一步：验证文件创建任务的分发与执行",
    "inputs": [WORKSPACE],
    "allowed_paths": [WORKSPACE],
    "forbidden_paths": [],
    "actions": [
        "在 smoke_workspace 写入 alpha.txt，内容严格为 ALPHA_OK（不含换行、不含多余字符）",
    ],
    "allowed_actions": [
        "创建 alpha.txt，内容严格为 ALPHA_OK（不含换行）",
        "读取 alpha.txt 并字节级验证内容 == ALPHA_OK",
    ],
    "stop_conditions": [
        "alpha.txt 创建失败或内容 != ALPHA_OK",
        "出现需要操作 smoke_workspace 之外路径的情况",
    ],
    "acceptance_criteria": [
        "alpha.txt 存在于 smoke_workspace",
        "alpha.txt 内容严格等于 ALPHA_OK",
    ],
    "do_not_do": ["不要创建其他文件", "不要在 alpha.txt 内容外添加换行或空格"],
}

TASK_T002 = {
    "status": "TASK",
    "task_id": "T002",
    "title": "create beta.txt",
    "goal": "在 smoke_workspace 创建 beta.txt，内容严格等于 BETA_OK",
    "reason": "smoke test 第二步：验证第二个任务按顺序分发",
    "inputs": [WORKSPACE],
    "allowed_paths": [WORKSPACE],
    "forbidden_paths": [],
    "actions": [
        "在 smoke_workspace 写入 beta.txt，内容严格为 BETA_OK（不含换行、不含多余字符）",
    ],
    "allowed_actions": [
        "创建 beta.txt，内容严格为 BETA_OK（不含换行）",
        "读取 beta.txt 并字节级验证内容 == BETA_OK",
    ],
    "stop_conditions": [
        "beta.txt 创建失败或内容 != BETA_OK",
        "出现需要操作 smoke_workspace 之外路径的情况",
    ],
    "acceptance_criteria": [
        "beta.txt 存在于 smoke_workspace",
        "beta.txt 内容严格等于 BETA_OK",
    ],
    "do_not_do": ["不要创建其他文件", "不要在 beta.txt 内容外添加换行或空格"],
}

TASK_T003 = {
    "status": "TASK",
    "task_id": "T003",
    "title": "create result.txt",
    "goal": "在 smoke_workspace 创建 result.txt，内容严格等于 SMOKE_DONE",
    "reason": "smoke test 第三步：验证最终结果文件与多任务循环收尾",
    "inputs": [WORKSPACE],
    "allowed_paths": [WORKSPACE],
    "forbidden_paths": [],
    "actions": [
        "在 smoke_workspace 写入 result.txt，内容严格为 SMOKE_DONE（不含换行、不含多余字符）",
    ],
    "allowed_actions": [
        "创建 result.txt，内容严格为 SMOKE_DONE（不含换行）",
        "读取 result.txt 并字节级验证内容 == SMOKE_DONE",
    ],
    "stop_conditions": [
        "result.txt 创建失败或内容 != SMOKE_DONE",
        "出现需要操作 smoke_workspace 之外路径的情况",
    ],
    "acceptance_criteria": [
        "result.txt 存在于 smoke_workspace",
        "result.txt 内容严格等于 SMOKE_DONE",
    ],
    "do_not_do": ["不要创建其他文件", "不要在 result.txt 内容外添加换行或空格"],
}

DECISION_FIX = {
    "status": "DECISION",
    "decision": "按 stop_conditions 停止当前批次，回到 Dispatcher 请求下一步",
    "reason": "smoke 决策路径验证：stop_condition 触发时的标准响应",
    "instructions": [
        "立即停止当前批次的本地执行",
        "整理蒸馏后的 current_state 并调用 dispatch_next_task",
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _completed_ids(completed_tasks: list | None) -> list:
    return [
        t.get("task_id")
        for t in (completed_tasks or [])
        if isinstance(t, dict) and t.get("task_id")
    ]


@mcp.tool()
async def dispatcher_health() -> dict:
    """Deterministic smoke Dispatcher 健康检查。不调用任何外部 API。"""
    return {
        "status": "OK",
        "server": "smoke-dispatcher",
        "deterministic": True,
        "time": _now(),
    }


@mcp.tool()
async def request_decision(
    question: str,
    context: str,
    options: list | None = None,
    constraints: str | None = None,
) -> dict:
    """Deterministic smoke 决策工具：固定返回标准 DECISION，验证决策路径可用。"""
    return DECISION_FIX


@mcp.tool()
async def dispatch_next_task(
    overall_goal: str,
    completed_tasks: list | None = None,
    current_state: str | None = None,
    constraints: str | None = None,
) -> dict:
    """根据固定状态机返回唯一一个下一步 TASK（或 DONE）。

    与真实 Dispatcher 工具签名完全一致。
    """
    request_id = uuid.uuid4().hex[:12]
    done = _completed_ids(completed_tasks)

    if "T001" not in done:
        return TASK_T001
    if "T002" not in done:
        return TASK_T002
    if "T003" not in done:
        return TASK_T003
    return {
        "status": "DONE",
        "reason": "smoke test 全部任务已验收完成：alpha.txt + beta.txt + result.txt",
        "request_id": request_id,
    }


if __name__ == "__main__":
    mcp.run()
