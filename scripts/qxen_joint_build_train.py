#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qxen_joint_v1 训练数据构建（capsule 1000 档 → mlx-lm prompt/completion 格式）。

输入：data/r3/ec_v1/data1000/{train,fresh}_capsule.jsonl（capsule 契约格式）
输出：data/r3/ec_v1/data1000/train_format/{train,valid}.jsonl（mlx-lm 训练格式）

格式（skill §4.1 + Kimi-Expert joint 上下文确认）：
  {"task_type": "capsule", "prompt": "<task_type 标签 + 证据材料>", "completion": "<结构化 capsule JSON>"}

prompt 设计：
  - 头部 task_type 标签（capsule 任务，joint 分流位预留）
  - 证据材料（来源 + 摘录 + 生命周期/时间线，若有）
completion 设计（Kimi ACTION 3：Gate 字段进 completion，防标签泄漏）：
  - 结构化 capsule 内容字段：relevance / key_evidence / timeline / relations / conflicts /
    operative_status / authority / provenance / sufficiency / next_step / uncertainty
  - 不含元数据（capsule_id / anchor_id / event_date / source_sha256 / metadata）

约束：
  - 不修改源数据（data1000 只读）
  - fresh 200 不参与训练，作为 valid 参考（valid 生成：eval 用 fresh 的子集打标，模型评估用）
  - 本次首轮 capsule-only：valid 从 fresh 抽样（见 §4.3）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC_TRAIN = ROOT / "data/r3/ec_v1/data1000/train_capsule.jsonl"
SRC_FRESH = ROOT / "data/r3/ec_v1/data1000/fresh_capsule.jsonl"
OUT_DIR = ROOT / "data/r3/ec_v1/data1000/train_format"

# completion 内容字段（去元数据，Kimi ACTION 3）
CONTENT_FIELDS = [
    "relevance", "key_evidence", "timeline", "relations", "conflicts",
    "operative_status", "authority", "provenance", "sufficiency",
    "next_step", "uncertainty",
]

TASK_HINT = (
    "你是证据胶囊生成器。根据证据材料，输出符合 evidence_capsule_v1 契约的结构化胶囊 JSON。"
    "包含：相关性(relevance)、关键证据(key_evidence 保真)、生命周期(timeline)、"
    "事件关系(relations)、冲突对(conflicts)、效力状态(operative_status: CURRENT/SUPERSEDED/STALE)、"
    "权威等级(authority)、溯源(provenance)、充分性(sufficiency)、下一步(next_step)、不确定性(uncertainty)。"
    "只输出 JSON，不要多余解释。"
)


def build_prompt(rec: dict, evidence: str, source: str) -> str:
    # evidence 截断，避免内嵌长 JSON 撑爆 max_seq_length 448
    if len(evidence) > 150:
        evidence = evidence[:150] + "...(截断)"
    tl = "; ".join(rec.get("timeline") or [])
    if len(tl) > 120:
        tl = tl[:120] + "..."
    lines = [
        "[TASK] capsule",
        "你是证据胶囊生成器。根据证据材料，输出符合 evidence_capsule_v1 契约的结构化胶囊 JSON。",
        "",
        "证据材料：",
        f"- 来源：{source}",
        f"- 证据摘录：{evidence}",
    ]
    if tl:
        lines.append(f"- 生命周期事件：{tl}")
    lines += ["", TASK_HINT, "只输出 JSON，不要多余解释。"]
    return "\n".join(lines)


def capsule_content(rec: dict) -> dict:
    """提取内容字段（去元数据）构成 completion。"""
    return {f: rec.get(f) for f in CONTENT_FIELDS if rec.get(f) not in (None, "", [])}


def to_training(rec: dict) -> dict:
    evidence = rec["key_evidence"][0]["text"] if rec.get("key_evidence") else ""
    source = rec["reference"][0] if rec.get("reference") else rec.get("anchor_id", "")
    return {
        "task_type": "capsule",
        "prompt": build_prompt(rec, evidence, source),
        "completion": json.dumps(capsule_content(rec), ensure_ascii=False),
    }


def main() -> int:
    if not SRC_TRAIN.is_file():
        print(f"ERROR: 源数据不存在: {SRC_TRAIN}")
        return 1

    train_rows = [json.loads(l) for l in SRC_TRAIN.read_text(encoding="utf-8").splitlines() if l.strip()]
    fresh_rows = [json.loads(l) for l in SRC_FRESH.read_text(encoding="utf-8").splitlines() if l.strip()]

    train_samples = [to_training(r) for r in train_rows]
    # valid: fresh 抽样（首轮 capsule-only，valid 用 fresh 全量作为参考，但训练只训 train）
    valid_samples = [to_training(r) for r in fresh_rows]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "train.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in train_samples), encoding="utf-8"
    )
    (OUT_DIR / "valid.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in valid_samples), encoding="utf-8"
    )

    stats = {
        "train_rows": len(train_samples),
        "valid_rows": len(valid_samples),
        "task_type": "capsule",
        "max_prompt_chars": max(len(s["prompt"]) for s in train_samples),
        "max_completion_chars": max(len(s["completion"]) for s in train_samples),
        "max_prompt_tokens_est": max(len(s["prompt"]) // 2 for s in train_samples),  # 粗估 2 chars/token
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print("train:", OUT_DIR / "train.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
