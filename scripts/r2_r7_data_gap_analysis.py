#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R2-R7 数据缺口分析（专家 Q5 前置，T001 后续）。

量化统一路线三类数据来源的可用量，输出配比表：
  1. 现有 pool（real 12 + manual 8）—— capsule 任务
  2. 真实轨迹挖掘（dispatcher.log / local_qwen.log / 任务账本）—— capsule + state_patch 候选
  3. real_timeline 衍生（anchor_derived 72）—— capsule 候选

用途：决定 100 档/200 档数据配比，为 skill 改写提供实证依据。
不修改任何数据源（只读分析）。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

POOL = ROOT / "data/r3/ec_v1/pool/ec_v1_pool.jsonl"
REAL_ANCHORS = ROOT / "data/r3/real_timeline/real_anchors.jsonl"
ANCHOR_DERIVED = ROOT / "data/r3/real_timeline/anchor_derived.jsonl"
REAL_TRAIN = ROOT / "data/r3/real_timeline/train.jsonl"
DISPATCHER_LOG = ROOT / "日志/dispatcher.log"
LOCAL_QWEN_LOG = ROOT / "日志/local_qwen.log"
TASK_LEDGER = ROOT / "调度状态/任务账本.json"


def count_jsonl(p: Path) -> int:
    if not p.is_file():
        return 0
    return sum(1 for l in p.read_text(encoding="utf-8").splitlines() if l.strip())


def main() -> int:
    out = {}

    # 1. 现有 pool
    pool = [
        json.loads(l)
        for l in POOL.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ] if POOL.is_file() else []
    real = [r for r in pool if r.get("provenance") == "real_anchor"]
    manual = [r for r in pool if r.get("provenance") == "manual_annotated"]
    out["1_pool"] = {
        "total": len(pool),
        "real": len(real),
        "manual": len(manual),
        "task_types": dict(Counter(r.get("provenance") for r in pool)),
        "state_patch_samples": 0,
        "note": "全部为 capsule 任务(R1-R4), 无 state_patch(R6)",
    }

    # 2. 真实轨迹挖掘源
    disp_log = DISPATCHER_LOG.read_text(encoding="utf-8") if DISPATCHER_LOG.is_file() else ""
    lq_log = LOCAL_QWEN_LOG.read_text(encoding="utf-8") if LOCAL_QWEN_LOG.is_file() else ""
    # 粗略估计可挖掘事件量（按日志行/结构化条目）
    disp_lines = disp_log.count("\n")
    lq_lines = lq_log.count("\n")
    # 任务账本
    ledger = json.loads(TASK_LEDGER.read_text(encoding="utf-8")) if TASK_LEDGER.is_file() else {}
    ledger_tasks = ledger.get("completed_tasks", [])
    out["2_trajectory"] = {
        "dispatcher_log_chars": len(disp_log),
        "dispatcher_log_lines": disp_lines,
        "local_qwen_log_chars": len(lq_log),
        "local_qwen_log_lines": lq_lines,
        "ledger_completed_tasks": len(ledger_tasks),
        "state_patch_candidates": len(ledger_tasks),
        "note": "任务账本 396 条状态变迁记录为 state_patch 主要候选源; 日志为 capsule 轨迹挖掘源",
        "est_capsule_mineable": min(60, max(0, disp_lines // 40 + lq_lines // 30)),
        "est_state_patch_mineable": min(80, len(ledger_tasks) // 5),
    }

    # 3. real_timeline 衍生
    derived = count_jsonl(ANCHOR_DERIVED)
    real_train = count_jsonl(REAL_TRAIN)
    out["3_real_timeline"] = {
        "anchor_derived": derived,
        "real_train": real_train,
        "real_anchors": count_jsonl(REAL_ANCHORS),
        "note": "anchor_derived 72 条与 train 48 条为 capsule 任务扩充候选(需人工核验可逆推性)",
    }

    # 汇总 100 档可行性
    capsule_now = len(pool)
    capsule_mineable = out["2_trajectory"]["est_capsule_mineable"]
    capsule_derived = min(20, derived)
    capsule_manual_target = max(0, 80 - capsule_mineable - capsule_derived)
    out["4_100_gap"] = {
        "capsule_current": capsule_now,
        "capsule_trajectory_mine": capsule_mineable,
        "capsule_derived": capsule_derived,
        "capsule_manual_needed_80": capsule_manual_target,
        "target_total_capsule_100": 100,
        "fresh_test_needed": 20,
        "note": "专家方案: 100 档需留 fresh 20 + train 80; capsule 需凑满 80 train, state_patch 另算 30-50 验证",
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
