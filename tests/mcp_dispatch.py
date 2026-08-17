#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Dispatch Driver - 测试工具

用法:
    ./venv/bin/python mcp_dispatch.py <dispatcher_py> <overall_goal> <completed_json_file|->

通过 MCP stdio 调用 dispatcher 的 dispatch_next_task，输出返回的 TASK/DONE/BLOCKED。
只做"调用并打印"，不执行任务、不修改任何文件。
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VENV_PY = "/Users/hillo/Desktop/任务调度器/venv/bin/python"
CWD = "/Users/hillo/Desktop/任务调度器"


async def main() -> None:
    if len(sys.argv) < 3:
        print("usage: mcp_dispatch.py <dispatcher_py> <overall_goal> <completed_json_file|->")
        sys.exit(2)

    dispatcher = sys.argv[1]
    goal = sys.argv[2]
    if len(sys.argv) > 3 and sys.argv[3] != "-":
        with open(sys.argv[3], encoding="utf-8") as f:
            completed = json.load(f)
    else:
        completed = []

    params = StdioServerParameters(command=VENV_PY, args=[dispatcher], cwd=CWD)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(
                "dispatch_next_task",
                {
                    "overall_goal": goal,
                    "completed_tasks": completed,
                    "current_state": "",
                    "constraints": "",
                },
            )
            for c in res.content:
                if c.type == "text":
                    print(c.text)


if __name__ == "__main__":
    asyncio.run(main())
