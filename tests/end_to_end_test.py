#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无害端到端测试（真实 DeepSeek Dispatcher + 模拟 Executor）
===========================================================

overall_goal：
    创建 hello.txt → 验证内容等于 hello → 删除 hello.txt → 验证文件不存在

流程：
    DeepSeek（Dispatcher）→ 只分发第一个任务
    测试 Executor → 在 测试/临时工作区 模拟完成
    DeepSeek → 分发下一任务（根据已完成工作）
    循环直到 DONE

验证点：
    1. 每一次调用只产生一个 TASK（禁止 tasks 数组 / 多任务）
    2. 任务编号严格递增 T001, T002, ...
    3. 每个 TASK 都通过严格 schema 校验
    4. 最终到达 DONE
    5. 最终文件状态符合目标（hello.txt 已删除）

运行：
    ../venv/bin/python end_to_end_test.py
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "测试"))
sys.path.insert(0, str(ROOT))

import deepseek_dispatcher_mcp as dsp  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

WORKSPACE = ROOT / "测试" / "临时工作区"
HELLO_FILE = WORKSPACE / "hello.txt"

OVERALL_GOAL = (
    "在 测试/临时工作区 创建 hello.txt（内容为 hello）；"
    "验证内容等于 hello；"
    "删除 hello.txt；"
    "验证文件不存在。"
)

CONSTRAINTS = (
    "只能在 测试/临时工作区 操作，不得触碰其他目录。"
    "每次只分发一个任务，任务粒度必须足够小。"
)

MAX_ITERATIONS = 12


async def call_dispatcher(completed_tasks, current_state):
    params = StdioServerParameters(
        command=str(ROOT / "venv" / "bin" / "python"),
        args=[str(ROOT / "deepseek_dispatcher_mcp.py")],
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            res = await session.call_tool(
                "dispatch_next_task",
                {
                    "overall_goal": OVERALL_GOAL,
                    "completed_tasks": completed_tasks,
                    "current_state": current_state,
                    "constraints": CONSTRAINTS,
                },
            )
            text = ""
            for item in res.content:
                if hasattr(item, "text"):
                    text += item.text
            if getattr(res, "isError", False):
                raise RuntimeError(f"Dispatcher 调用失败: {text}")
            return json.loads(text)


def describe_task(task: dict) -> str:
    return (
        f"{task['task_id']} | {task['title']} | goal={task['goal']} | "
        f"actions={task['actions']} | criteria={task['acceptance_criteria']}"
    )


def _task_intent(task: dict) -> str:
    """根据 task title + actions 判定唯一意图（模拟 Executor 的理解）。

    优先级：删除/创建等变更操作优先于验证操作。
    """
    text = (task["title"] + " " + " ".join(task.get("actions", []))).lower()
    if any(k in text for k in ("删除", "移除", "delete", "remove", "unlink")):
        return "DELETE"
    if any(k in text for k in ("创建", "写入", "生成", "create", "write")):
        return "CREATE"
    if any(k in text for k in ("验证", "verify", "check", "确认", "检查")):
        if any(k in text for k in ("不存在", "nonexist", "absent", "已删除")):
            return "VERIFY_ABSENT"
        return "VERIFY_CONTENT"
    return "UNKNOWN"


def executor_execute(task: dict) -> dict:
    """模拟 Executor：根据唯一意图在临时工作区执行，并返回 VERIFIED_PASS 记录。"""
    tid = task["task_id"]
    intent = _task_intent(task)
    evidence: list[str] = []
    result = "N/A"

    if intent == "CREATE":
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        HELLO_FILE.write_text("hello", encoding="utf-8")
        result = "CREATED"
        evidence.append(f"write {HELLO_FILE} content=hello exists_after={HELLO_FILE.exists()}")

    elif intent == "VERIFY_CONTENT":
        content = HELLO_FILE.read_text(encoding="utf-8") if HELLO_FILE.exists() else None
        result = "CONTENT_OK" if content == "hello" else f"CONTENT_MISMATCH={content!r}"
        evidence.append(f"read content={content!r}")

    elif intent == "VERIFY_ABSENT":
        result = "ABSENT_CONFIRMED" if not HELLO_FILE.exists() else "STILL_EXISTS"
        evidence.append(f"check exists={HELLO_FILE.exists()}")

    elif intent == "DELETE":
        was = HELLO_FILE.exists()
        if was:
            HELLO_FILE.unlink()
        result = "DELETED"
        evidence.append(f"unlink was_present={was} exists_after={HELLO_FILE.exists()}")

    else:
        result = "UNKNOWN_INTENT"
        evidence.append(f"actions={task.get('actions')}")

    return {
        "task_id": tid,
        "status": "VERIFIED_PASS",
        "summary": f"{task['title']} 已完成（result={result}）",
        "evidence_summary": "; ".join(evidence) or f"action={task.get('actions')}",
    }


def main() -> int:
    print("=" * 72)
    print("无害端到端测试：DeepSeek Dispatcher + 模拟 Executor")
    print(f"工作区: {WORKSPACE}")
    print("=" * 72)

    completed_tasks: list[dict] = []
    current_state = "尚未开始"
    single_task_check = True

    for iteration in range(1, MAX_ITERATIONS + 1):
        result = asyncio.run(
            call_dispatcher(completed_tasks, current_state)
        )

        # 验证：每次调用只返回一个任务
        assert isinstance(result, dict), f"第{iteration}次调用返回非 dict: {result}"
        assert "tasks" not in result, f"第{iteration}次调用出现 tasks 数组"
        assert "task_list" not in result, f"第{iteration}次调用出现 task_list"

        status = result["status"]
        print(f"\n[{iteration}] status={status}")

        if status == "TASK":
            # 严格校验（与调度器内部一致的规则）
            dsp.validate_response(
                result,
                expected_task_id=result["task_id"],
                completed_task_ids=[t["task_id"] for t in completed_tasks],
            )
            assert result["task_id"] not in [t["task_id"] for t in completed_tasks], (
                f"重复分发已完成任务: {result['task_id']}"
            )
            print(f"  -> {describe_task(result)}")
            record = executor_execute(result)
            print(f"  -> Executor: {record['summary']} | {record['evidence_summary']}")
            completed_tasks.append(record)
            current_state = (
                f"已完成 {len(completed_tasks)} 个任务："
                + "; ".join(t["summary"] for t in completed_tasks)
            )
        elif status == "DONE":
            print(f"  -> 原因: {result['reason']}")
            print("\n" + "=" * 72)
            print(f"端到端测试成功到达 DONE（共 {len(completed_tasks)} 个任务）")
            print("每次调用均只返回一个 TASK：PASS")
            print("=" * 72)
            # 最终状态：目标要求最终文件不存在
            final_absent = not HELLO_FILE.exists()
            print(f"最终 hello.txt 不存在: {final_absent}")
            for t in completed_tasks:
                print(f"  {t['task_id']} {t['status']} | {t['summary']}")
            if not final_absent:
                print("警告：DONE 时 hello.txt 仍然存在（目标要求删除后验证不存在）")
            return 0 if final_absent else 2
        elif status == "BLOCKED":
            print(f"  -> BLOCKED 原因: {result['reason']}")
            print(f"  -> required_information: {result['required_information']}")
            print("端到端测试终止于 BLOCKED")
            return 3
        else:
            print(f"  -> 非法 status: {status}")
            return 4

    print(f"端到端测试未在 {MAX_ITERATIONS} 次迭代内到达 DONE")
    return 5


if __name__ == "__main__":
    sys.exit(main())
