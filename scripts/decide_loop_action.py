#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""loop 事件驱动决策器 —— 只在「状态变化需要 GPT 介入」时才输出 notify=true。

与 update_loop_state.py（采数+写 state）配合，本脚本做「状态机」：
    读当前 state + 上次通知状态(loop_notify_state.json)，
    判断本轮是否该唤醒 GPT（推 codex exec），并把决策结果 + 人类可读 alert 输出为单行 JSON。

唤醒 GPT 的 5 种事件（其余全部静默）：
    done      : 训练已完成，待跑 Gate 评估
    failed    : 训练进程退出但未正常完成，待自修
    mem_danger: 峰值内存 >= 18GB 红线
    stall     : training 阶段 iter 连续 STALL_THRESHOLD 次采样无进展（卡死）
    needs_decision : 状态明确要求人工复核；自动流程只记录，不自动咨询专家

静默场景：正常训练推进（iter 增加）、idle（无训练）、done/failed 已通知过（不重复刷）。

持久化：调度状态/loop_notify_state.json —— 记录上次通知的 phase/reason/iter，
        避免同一事件反复推 codex 开新聊天框。

用法：
    scripts/decide_loop_action.py
输出（stdout 单行 JSON）：
    {"notify": true,  "reason": "done", "alert": "...", "adapter": "...",
     "phase": "done", "iter": "2160/2160", "mem_gb": "7.5", "proc_alive": false}
    {"notify": false, "reason": "normal", "adapter": "...", "phase": "training", ...}
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import update_loop_state as uls  # 复用 discover_adapter / load_cfg / read_progress / build_state

NOTIFY_PATH = ROOT / "调度状态" / "loop_notify_state.json"
MEM_REDLINE_GB = 18.0
STALL_THRESHOLD = 6  # launchd 每 15 分钟跑一次，6 次 = 1.5 小时无进展视为卡死


def decision_marker(current: dict) -> tuple[bool, str]:
    """识别需要人工复核的显式状态；不自动调用专家。"""
    if current.get("expert_pending"):
        return True, "expert_optional"
    phase = current.get("phase")
    if phase in {"gate_fail", "needs_decision"}:
        return True, phase
    if int(current.get("repair_attempt", 0) or 0) >= 2:
        return True, "repair_attempt_2"
    return False, ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_notify() -> dict:
    if NOTIFY_PATH.is_file():
        try:
            return json.loads(NOTIFY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "adapter": None,
        "last_notified_phase": None,
        "last_notified_reason": None,
        "last_seen_iter": 0,
        "last_seen_at": None,
        "stall_count": 0,
        "notified_at": None,
    }


def save_notify(n: dict) -> None:
    NOTIFY_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTIFY_PATH.write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")


def decide(current: dict) -> dict:
    """核心状态机。current 是 update_loop_state.build_state 的输出。"""
    n = load_notify()
    adapter = current.get("adapter")
    phase = current.get("phase", "idle")
    last_iter = current.get("last_iter", 0)
    mem = current.get("peak_mem_gb") or 0
    try:
        mem = float(mem)
    except (TypeError, ValueError):
        mem = 0.0

    # adapter 变化 → 重置通知状态（新训练）
    if n.get("adapter") != adapter:
        n = {
            "adapter": adapter,
            "last_notified_phase": None,
            "last_notified_reason": None,
            "last_seen_iter": last_iter,
            "last_seen_at": now_iso(),
            "stall_count": 0,
            "notified_at": None,
        }

    # ---- 更新 stall 计数（iter 有无进展）----
    # 只在已看到过 iter 进展(last_iter>0)后才计数；last_iter==0（训练刚启动/新日志
    # 还没到首个 report 点）不算卡死，避免 resume 训练启动期误报。
    if last_iter > 0 and last_iter == n.get("last_seen_iter", 0):
        n["stall_count"] = int(n.get("stall_count", 0)) + 1
    else:
        n["stall_count"] = 0
    n["last_seen_iter"] = last_iter
    n["last_seen_at"] = now_iso()

    # ---- 判断是否唤醒 ----
    notify = False
    reason = "normal"
    alert = ""

    needs_expert, expert_reason = decision_marker(current)

    if needs_expert and n.get("last_notified_reason") != expert_reason:
        notify, reason = True, "needs_decision"
        alert = f"需要人工复核：{expert_reason}（自动流程暂停 Gate/晋级/专家调用）"
    elif phase == "idle":
        reason = "idle"
    elif phase == "done" and n.get("last_notified_phase") != "done":
        notify, reason = True, "done"
        alert = f"训练 {adapter} 已完成（iter {last_iter}/{current.get('target_iters')}），待跑 Gate 评估"
    elif phase == "failed" and n.get("last_notified_phase") != "failed":
        notify, reason = True, "failed"
        alert = f"训练 {adapter} 已退出但未正常完成（iter {last_iter}/{current.get('target_iters')}），待自修"
    elif phase == "training" and mem >= MEM_REDLINE_GB and n.get("last_notified_reason") != "mem_danger":
        notify, reason = True, "mem_danger"
        alert = f"训练 {adapter} 峰值内存 {mem}GB 超 {MEM_REDLINE_GB}GB 红线"
    elif phase == "training" and n.get("stall_count", 0) >= STALL_THRESHOLD:
        notify, reason = True, "stall"
        alert = f"训练 {adapter} 连续 {n.get('stall_count')} 次采样 iter 无进展（{last_iter}/{current.get('target_iters')}），疑似卡死"
    elif phase == "training":
        reason = "normal"

    # mem 恢复后重置 mem_danger 标记，允许下次再超时重新通知
    if n.get("last_notified_reason") == "mem_danger" and mem < MEM_REDLINE_GB:
        n["last_notified_reason"] = None

    # 通知时更新状态
    if notify:
        n["last_notified_phase"] = phase
        n["last_notified_reason"] = reason
        n["notified_at"] = now_iso()

    save_notify(n)

    return {
        "notify": notify,
        "reason": reason,
        "alert": alert,
        "adapter": adapter,
        "phase": phase,
        "iter": f"{last_iter}/{current.get('target_iters', '?')}",
        "mem_gb": mem,
        "proc_alive": current.get("proc_alive", False),
        "stall_count": n.get("stall_count", 0),
        "decision_route": "gpt_auto_continue" if reason == "needs_decision" else "auto_continue",
        "expert_required": needs_expert,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=None)
    args = ap.parse_args()

    adapter = args.adapter or uls.discover_adapter()
    if not adapter:
        # 无活跃训练，回落到最近 state 或直接 idle
        if uls.STATE_PATH.is_file():
            try:
                adapter = json.loads(uls.STATE_PATH.read_text(encoding="utf-8")).get("adapter")
            except Exception:
                adapter = None
    if not adapter:
        out = {"notify": False, "reason": "idle", "alert": "",
               "adapter": None, "phase": "idle", "iter": "?/?",
               "mem_gb": 0, "proc_alive": False, "stall_count": 0}
        print(json.dumps(out, ensure_ascii=False))
        return 0

    cfg = uls.load_cfg(adapter)
    if cfg is None:
        print(json.dumps({"notify": False, "reason": "error",
                          "alert": f"no config for {adapter}",
                          "adapter": adapter}, ensure_ascii=False))
        return 0

    p = uls.read_progress(adapter)
    current = uls.build_state(adapter, cfg, p, target_override=uls.discover_iters())
    out = decide(current)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
