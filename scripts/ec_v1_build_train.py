#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence Capsule v1 训练数据构建（T001 前置）。

把 pool 20 条证据胶囊转成 mlx-lm lora 训练格式 prompt/completion。

任务映射（Dispatcher 决策 选项A）：
  - real 12 条（provenance=real_anchor）→ R1/R2 任务：
    输入证据摘录 → 输出 relevance/key_evidence/reference/sufficiency/next_step
  - manual 8 条（provenance=manual_annotated）→ R3/R4 任务：
    输入证据摘录 + 生命周期事件 → 输出 timeline/relations/conflicts/operative_status/
    immutable_fields/compressible/sufficiency

输出：
  - data/r3/staging/ec_v1/train.jsonl（20 条 prompt/completion）
  - data/r3/staging/ec_v1/valid.jsonl（空文件，mlx-lm val_batches=0）
  - data/r3/staging/ec_v1/manifest.json

约束：
  - 不修改冻结资产（pool 只读）
  - completion 只含内容字段，不含元数据(capsule_id/anchor_id/provenance/event_date/as_of)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC_POOL = ROOT / "data/r3/ec_v1/pool/ec_v1_pool.jsonl"
OUT_DIR = ROOT / "data/r3/staging/ec_v1"
OUT_TRAIN = OUT_DIR / "train.jsonl"
OUT_VALID = OUT_DIR / "valid.jsonl"
OUT_MANIFEST = OUT_DIR / "manifest.json"

REAL_CONTENT_FIELDS = ["relevance", "key_evidence", "reference", "sufficiency", "next_step", "uncertainty"]
MANUAL_CONTENT_FIELDS = [
    "relevance", "key_evidence", "timeline", "relations", "conflicts",
    "operative_status", "immutable_fields", "compressible", "sufficiency",
]


def capsule_content(c: dict, fields: list[str]) -> dict:
    """提取内容字段（去元数据）构成模型应生成的 completion 目标。"""
    return {f: c.get(f) for f in fields if c.get(f) not in (None, "", [])}


def build_prompt(c: dict, evidence: str, source: str, task_hint: str) -> str:
    return (
        "你是证据胶囊生成器（Evidence Capsule Generator）。"
        "根据提供的证据材料，输出符合 evidence_capsule_v1 契约的证据胶囊 JSON。\n\n"
        f"证据材料：\n- 来源：{source}\n- 证据摘录：{evidence}\n\n"
        f"任务：{task_hint}\n"
        "只输出 JSON，不要多余解释。"
    )


def build_real_sample(c: dict) -> dict:
    evidence = c["key_evidence"][0]["text"] if c.get("key_evidence") else ""
    source = c["reference"][0] if c.get("reference") else c.get("anchor_id", "")
    task_hint = (
        "提取证据相关性（relevance）、关键证据（key_evidence，保真不可改写）、"
        "证据引用（reference）、证据充分性（sufficiency）与下一步（next_step）。"
    )
    content = capsule_content(c, REAL_CONTENT_FIELDS)
    return {
        "prompt": build_prompt(c, evidence, source, task_hint),
        "completion": json.dumps(content, ensure_ascii=False),
    }


def build_manual_sample(c: dict) -> dict:
    evidence = c["key_evidence"][0]["text"] if c.get("key_evidence") else ""
    source = c["reference"][0] if c.get("reference") else c.get("anchor_id", "")
    tl = "; ".join(c.get("timeline") or [])
    task_hint = (
        "提取证据相关性（relevance）、关键证据（key_evidence，保真不可改写）、"
        "生命周期时间线（timeline）、事件关系（relations）、冲突对（conflicts）、"
        "效力状态（operative_status：CURRENT/SUPERSEDED/STALE）、不可改写字段"
        "（immutable_fields）、可压缩内容（compressible）与充分性（sufficiency）。"
    )
    if tl:
        task_hint += f"\n证据生命周期事件：{tl}"
    content = capsule_content(c, MANUAL_CONTENT_FIELDS)
    return {
        "prompt": build_prompt(c, evidence, source, task_hint),
        "completion": json.dumps(content, ensure_ascii=False),
    }


def main() -> int:
    pool = [json.loads(l) for l in SRC_POOL.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not pool:
        print("ERROR: pool empty")
        return 1

    samples = []
    for c in pool:
        if c.get("provenance") == "manual_annotated":
            samples.append(build_manual_sample(c))
        else:
            samples.append(build_real_sample(c))

    if len(samples) != len(pool):
        print(f"ERROR: built {len(samples)} != pool {len(pool)}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_TRAIN.write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples), encoding="utf-8"
    )
    # 注意: mlx_lm load_subset 对空文件会崩, 故 valid 不生成(load_subset 对不存在返回 [])

    stats = {
        "train_rows": len(samples),
        "real_samples": sum(1 for s in samples if "operative_status" not in s["completion"]),
        "manual_samples": sum(1 for s in samples if "operative_status" in s["completion"]),
        "valid_rows": 0,
        "max_prompt_chars": max(len(s["prompt"]) for s in samples),
        "max_completion_chars": max(len(s["completion"]) for s in samples),
    }
    OUT_MANIFEST.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print("train:", OUT_TRAIN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
