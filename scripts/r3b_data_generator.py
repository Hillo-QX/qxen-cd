#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R3B 能力覆盖样本生成器 — 骨架(SKELETON ONLY)

依据: docs/r3b_context_capability_data_design.md (DRAFT, 2026-08-14)
状态: 骨架代码, 不包含实际数据生成逻辑, 不落地任何数据。
落地前置: 用户授权 + v5 Gate 评估后。

用法(当前仅占位, 不可运行):
    ./venv/bin/python scripts/r3b_data_generator.py --dry-run
"""
from __future__ import annotations
import argparse
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


# ---------------------------------------------------------------------------
# 5 类能力样本 schema 定义 (对齐 docs/r3b_context_capability_data_design.md §3)
# ---------------------------------------------------------------------------

@dataclass
class R3BSample:
    """单条训练样本的公共结构。"""
    type: str                 # evidence_selection / conflict_explanation / uncertainty_retrieve / state_update / fidelity_compress
    prompt: str
    completion: str
    provenance: Dict[str, Any] = field(default_factory=dict)  # {source_query_ids, source, generated_at}


# ---------------------------------------------------------------------------
# 5 个生成函数签名 (每个函数仅占位, docstring 说明输入输出)
# ---------------------------------------------------------------------------

def gen_evidence_selection(group_rows: List[Dict[str, Any]]) -> Iterator[R3BSample]:
    """
    类型 A — 证据筛选 (evidence_selection)
    输入: 冻结数据同一 query 组的全部候选行 (含 text/authority_type/operativeness/material_conflict/reason_code/source)
    输出: R3BSample(type='evidence_selection')
    逻辑(待实现):
      - 从组内候选重组多证据块 E1..En (含 1 个正确 + 若干 hard-negative: 备份/失效/间接提及)
      - prompt: 原始证据块 + 判定任务
      - completion: <think>筛选+推理</think> + JSON(selected, status, authority, reason)
    覆盖能力: 相关性判断 / 证据筛选 / hard-negative 区分
    """
    raise NotImplementedError("SKELETON: 待用户授权后实现")


def gen_conflict_explanation(group_rows: List[Dict[str, Any]]) -> Iterator[R3BSample]:
    """
    类型 B — 冲突解释 (conflict_explanation)
    输入: 冻结数据中 material_conflict=true 的候选组
    输出: R3BSample(type='conflict_explanation')
    逻辑(待实现):
      - 构造两份来源的冲突判定 (A: 当前生效源 vs B: 间接/历史源)
      - prompt: 冲突场景 + 判定时点
      - completion: <think>权威层级+时间线取舍</think> + JSON(material_conflict, resolution, explanation)
    覆盖能力: 冲突解释 / 权威取舍 / 时间线推理
    """
    raise NotImplementedError("SKELETON: 待用户授权后实现")


def gen_uncertainty_retrieve(group_rows: List[Dict[str, Any]]) -> Iterator[R3BSample]:
    """
    类型 C — 不确定性/继续检索 (uncertainty_retrieve)
    输入: 冻结数据中证据不足/缺时间戳的候选行
    输出: R3BSample(type='uncertainty_retrieve')
    逻辑(待实现):
      - 构造"证据不足"场景 (仅间接提及, 无版本/时间戳)
      - prompt: 当前证据 + 判定任务
      - completion: JSON(sufficient=false, action=RETRIEVE, target=需检索内容)
    覆盖能力: 证据充分性判断 / RETRIEVE 决策
    """
    raise NotImplementedError("SKELETON: 待用户授权后实现")


def gen_state_update(group_rows: List[Dict[str, Any]]) -> Iterator[R3BSample]:
    """
    类型 D — 滚动上下文更新 (state_update)
    输入: 同 query 不同候选的成对反事实样本 (O(n^2) 状态转移对)
    输出: R3BSample(type='state_update')
    逻辑(待实现):
      - OLD_STATE: 候选 A 生效时的状态
      - NEW_EVENTS: 候选 B 出现/取代的事件
      - completion: NEW_STATE (含 superseded 标记)
    覆盖能力: 滚动上下文管理 / 状态更新
    """
    raise NotImplementedError("SKELETON: 待用户授权后实现")


def gen_fidelity_compress(group_rows: List[Dict[str, Any]]) -> Iterator[R3BSample]:
    """
    类型 E — 摘要保真 (fidelity_compress)
    输入: 冻结数据中 text 较长的候选行
    输出: R3BSample(type='fidelity_compress')
    逻辑(待实现):
      - 取长 text 构造压缩任务
      - completion: JSON(current, archived[], critical_paths[]) 关键路径 VERBATIM 保留
    覆盖能力: 摘要保真 / COMPRESS vs VERBATIM 决策
    """
    raise NotImplementedError("SKELETON: 待用户授权后实现")


# ---------------------------------------------------------------------------
# 生成管线编排 (仅占位)
# ---------------------------------------------------------------------------

GENERATORS = {
    "evidence_selection": gen_evidence_selection,
    "conflict_explanation": gen_conflict_explanation,
    "uncertainty_retrieve": gen_uncertainty_retrieve,
    "state_update": gen_state_update,
    "fidelity_compress": gen_fidelity_compress,
}


def generate_all(rows_by_group: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[R3BSample]]:
    """
    编排: 按 query 组输入冻结数据, 输出 5 类样本字典。
    待实现: 每组内按类型调用对应 generator; 8:2 train/valid 按组隔离; manifest 统计。
    """
    raise NotImplementedError("SKELETON: 待用户授权后实现")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="R3B 能力样本生成器 (SKELETON)")
    parser.add_argument("--dry-run", action="store_true", help="仅打印生成计划, 不落地数据")
    args = parser.parse_args()
    if args.dry_run:
        print("SKELETON: 计划生成 5 类样本:",
              ", ".join(GENERATORS.keys()))
        print("落地前置: 用户授权 + v5 Gate 评估完成。")
    else:
        print("SKELETON: 未授权不落地数据。请先取得授权。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
