#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LocalQwen 实时观察窗
====================

在 cn（Continue CLI）会话旁边开一个终端运行本脚本，实时看到本地 qwen 的每次调用：
DS 的文本生成不会出现在这里——这里刷的每一行都是 qwen 在干活。

    ./venv/bin/python scripts/watch_local_qwen.py            # 从日志末尾开始跟随
    ./venv/bin/python scripts/watch_local_qwen.py --all      # 先打印已有日志再跟随

显示格式：
    → 14:02:11 local_distill        输入 12.3K 字符，qwen 开始处理...
    ✓ 14:02:19 local_distill        8.1s → 输出 1.1K 字符 / 18 行（attempt 1）
    ✗ 14:03:02 local_classify       FALLBACK: invalid label（DS 将自行兜底）
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "日志" / "local_qwen.log"


def fmt_chars(n: int) -> str:
    return f"{n / 1000:.1f}K" if n >= 1000 else str(n)


def render(e: dict) -> str:
    tool = e.get("tool", "?")
    status = e.get("status", "?")
    hh = e.get("time", "")[11:19]  # ISO 时间里的 HH:MM:SS（UTC）
    pad = f"{tool:<22}"
    if status == "START":
        return f"→ {hh} {pad} 输入 {fmt_chars(e.get('input_chars', 0))} 字符，qwen 开始处理..."
    if status == "OK":
        if tool == "local_health":
            return (f"✓ {hh} {pad} {e.get('latency_s', '?')}s，"
                    f"ollama 在线（probe: {e.get('probe', '?')}）")
        out = (f"{e.get('latency_s', '?')}s → 输出 "
               f"{fmt_chars(e.get('output_chars', 0))} 字符 / {e.get('output_lines', '?')} 行"
               f"（attempt {e.get('attempt', '?')}）")
        return f"✓ {hh} {pad} {out}"
    if status == "FALLBACK":
        return (f"✗ {hh} {pad} FALLBACK: {e.get('reason', '?')[:60]} "
                f"（DS 将自行兜底）")
    if status == "ERROR":
        return f"✗ {hh} {pad} ERROR: {e.get('reason', '?')[:60]}"
    return f"? {hh} {pad} {status}"


def main() -> None:
    show_all = "--all" in sys.argv
    print(f"watching {LOG}  （此窗口刷的每一行 = 本地 qwen 在运行；"
          f"cn 里的文本生成 = DS）", flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.touch(exist_ok=True)
    with open(LOG, "r", encoding="utf-8") as f:
        if not show_all:
            f.seek(0, 2)  # 跳到末尾，只看新事件
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            try:
                print(render(json.loads(line)), flush=True)
            except json.JSONDecodeError:
                continue


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
