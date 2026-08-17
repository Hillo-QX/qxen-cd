#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监控语义层 CLI bridge —— 供 shell 巡检脚本（watch_r3a_v2.sh）调用。

把「异常摘要 + 日志末尾片段」蒸馏成可恢复性判断 + 失败聚类 + 告警胶囊，
复用 local_qwen_mcp 的 ollama 调用（_chat / _monitor_prompt / _parse_monitor）
与审计（_audit），保证 prompt 与 schema 与 MCP 工具 local_monitor_analyze 完全一致。

设计：shell 负责采数 + 硬阈值判定（确定性数字），qwen 只做语义层。
qwen 连续 2 次失败 -> status=FALLBACK，脚本仍返回 0，watch 降级用原始摘要，
绝不让监控链死在 9B 手上。

用法：
    scripts/monitor_analyze.py --alert "进程已退出..." --log logs/r3/r3a_v2_train.log [--tail-lines 80]

输出（stdout）：单行 JSON
    {"status": "OK", "verdict": "...", "failure_cluster": "...",
     "evidence": [...], "alert_capsule": "..."}
    {"status": "FALLBACK", "reason": "...", "alert": "原始摘要"}
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import local_qwen_mcp as lq  # 复用 _chat / _monitor_prompt / _parse_monitor / _audit / _now


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", required=True, help="shell 判定的异常摘要（确定性）")
    ap.add_argument("--log", required=True, help="训练日志路径")
    ap.add_argument("--tail-lines", type=int, default=80, help="读日志末尾行数（默认 80）")
    args = ap.parse_args()

    log_path = Path(args.log)
    if not log_path.is_file():
        out = {"status": "ERROR", "reason": f"log not found: {args.log}", "alert": args.alert}
        print(json.dumps(out, ensure_ascii=False))
        return 0  # 不让 watch 因日志缺失而中断

    tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-args.tail_lines:])
    combined = f"异常摘要：{args.alert}\n日志片段：\n{tail}"
    input_chars = len(combined)

    t0 = time.time()
    lq._audit({"time": lq._now(), "tool": "local_monitor_analyze", "status": "START",
               "input_chars": input_chars, "model": lq.MODEL})
    last_err = ""
    for attempt in (1, 2):
        try:
            out_text = lq._chat(lq._monitor_prompt(combined, attempt), num_predict=400)
            result = lq._parse_monitor(out_text)
            lq._audit({
                "time": lq._now(), "tool": "local_monitor_analyze", "status": "OK",
                "input_chars": input_chars,
                "output_chars": len(json.dumps(result, ensure_ascii=False)),
                "output_lines": 1 + len(result["evidence"]),
                "attempt": attempt, "latency_s": round(time.time() - t0, 2),
                "model": lq.MODEL,
            })
            print(json.dumps({"status": "OK", **result}, ensure_ascii=False))
            return 0
        except lq.QwenError as e:
            last_err = str(e)
    lq._audit({
        "time": lq._now(), "tool": "local_monitor_analyze", "status": "FALLBACK",
        "input_chars": input_chars, "reason": last_err, "attempt": 2,
        "latency_s": round(time.time() - t0, 2), "model": lq.MODEL,
    })
    print(json.dumps({"status": "FALLBACK", "reason": last_err, "alert": args.alert}, ensure_ascii=False))
    return 0  # 降级：watch 用原始摘要兜底，不中断监控链


if __name__ == "__main__":
    sys.exit(main())
