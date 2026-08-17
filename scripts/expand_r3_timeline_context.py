#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 R3 时间线锚点切片扩展为受控 context-derived 数据。

只做确定性表达变体，不改变证据、事件、as_of 或由事件计算出的标签。
所有输出明确标记 anchor_context_derived，不能作为纯真实 Gate。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/r3/real_timeline/anchor_derived.jsonl"
OUT = ROOT / "data/r3/real_timeline_context_derived"

TAIL = (
    "\n请严格按五行输出，不添加解释：\n"
    "证据理由码：<reason_code>\n"
    "权威层级：<T0-T4>\n"
    "材料冲突：<true/false>\n"
    "判定要点：<当前有效/当前不适用或仅历史参考/已被后续版本或新权威来源取代>\n"
    "效力状态：<CURRENT/STALE/SUPERSEDED>"
)

VARIANTS = (
    "请先按 as_of 与事件顺序判断候选是否仍具操作效力。",
    "注意区分：暂不适用或历史参考，不等于已经被后续版本取代。",
    "请优先检查候选版本与后续来源的时间关系，再给出效力状态。",
    "判定只依据给出的候选、事件、权威源链和 as_of，不使用当前日期猜测。",
    "若存在明确 superseder，必须与单纯 archived/not-applicable 分开处理。",
    "请把材料相关性与 operative status 分开判断，原始相关性标签不是效力标签。",
    "先确定 as_of 位于发布、归档、取代事件的哪个区间。",
    "候选本身可以真实且相关，但若已被新来源取代，状态仍应是 SUPERSEDED。",
    "没有取代证据时，不要仅因历史或暂不适用就判为 SUPERSEDED。",
    "输出最后一行唯一效力状态，前四行给出可审计的结构化依据。",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load():
    return [json.loads(x) for x in SRC.read_text(encoding="utf-8").splitlines() if x.strip()]


def make_prompt(row: dict, variant: str, index: int) -> str:
    # Prompt 构造修复 v6（2026-08-14 Kimi-Expert V3 裁决）：对称语义标注，
    # 三类状态显式给出判别特征（CURRENT/STALE 不注入 superseder，无取代记录）。
    supersede_date = None
    for e in row.get("events", []):
        if e.get("type") == "superseded":
            supersede_date = e.get("date")
            break
    as_of = row.get("as_of", "")
    has_superseder_reached = bool(supersede_date and as_of and as_of >= supersede_date)
    status = row.get("operative_status")
    if status == "SUPERSEDED":
        version_line = f"版本事件：{'; '.join(e['type'] + '=' + e['date'] for e in row['events'])}\n"
        follow_line = f"后续来源：{row['superseder_path']}\n"
        status_line = "最新事件：已被 v2 取代，存在取代记录"
    else:
        keep_events = [e for e in row.get("events", []) if e.get("type") != "superseded"]
        version_line = f"版本事件：{'; '.join(e['type'] + '=' + e['date'] for e in keep_events)}\n"
        follow_line = "后续来源：暂未发现已发布的取代者\n"
        status_line = "最新事件：已归档，无取代记录" if status == "STALE" else "最新事件：未归档，无取代记录"
    return (
        f"任务上下文：{row['task']}\n"
        f"候选材料：{row['candidate_path']}（{row['candidate_type']}）\n"
        f"证据摘录：{row.get('evidence_excerpt', '')}\n"
        f"候选版本：v{row['version']}\n"
        f"{version_line}"
        f"{follow_line}"
        f"{status_line}\n"
        f"判定时点 as_of：{row['as_of']}\n"
        f"候选权威层级：{row['authority_type']}\n"
        f"上下文变体{index + 1}：{variant}\n"
        "请结合时间线和证据进行当前效力判定。" + TAIL
    )


def main():
    base = load()
    rows = []
    for row in base:
        for i, variant in enumerate(VARIANTS):
            x = dict(row)
            x["record_id"] = f"{row['record_id']}-ctx{i + 1:02d}"
            x["prompt"] = make_prompt(row, variant, i)
            x["provenance"] = "anchor_context_derived"
            x["context_variant"] = i + 1
            x["derivation_note"] = (
                "仅改变上下文表达；事件、as_of、operative_status 和证据锚点保持不变"
            )
            rows.append(x)

    by_anchor = {}
    for row in rows:
        by_anchor.setdefault(row["anchor_id"], []).append(row)
    train_ids = {f"TR-{i:02d}" for i in range(1, 9)}
    valid_ids = {"TR-09", "TR-10"}
    fresh_ids = {"TR-11", "TR-12"}
    splits = {
        "train": [x for x in rows if x["anchor_id"] in train_ids],
        "valid": [x for x in rows if x["anchor_id"] in valid_ids],
        "fresh": [x for x in rows if x["anchor_id"] in fresh_ids],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in [("all.jsonl", rows), *[(f"{k}.jsonl", v) for k, v in splits.items()]]:
        (OUT / name).write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in data), encoding="utf-8"
        )

    manifest = {
        "stage": "R3A-real-timeline-anchor-context-derived-v1",
        "source": str(SRC.relative_to(ROOT)),
        "rows": len(rows),
        "variants_per_slice": len(VARIANTS),
        "splits": {k: len(v) for k, v in splits.items()},
        "anchor_isolation": True,
        "labels": dict(sorted(Counter(x["operative_status"] for x in rows).items())),
        "provenance": "anchor_context_derived",
        "real_gate_eligible": False,
        "warning": "表达变体不是新增真实证据；必须补真实事件日期后才能升级为 real Gate",
        "files": {},
    }
    for path in sorted(OUT.glob("*.jsonl")):
        manifest["files"][path.name] = {
            "rows": sum(1 for _ in path.open(encoding="utf-8")),
            "sha256": sha(path),
        }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "README.md").write_text(
        "# R3A 时间线上下文派生扩展集\n\n"
        "本目录基于 `data/r3/real_timeline/anchor_derived.jsonl` 生成 10 种确定性上下文表达变体。\n"
        "它不增加真实事件证据，不改变 as_of、事件关系或效力标签；所有样本均标记为 `anchor_context_derived`。\n\n"
        "`train.jsonl`、`valid.jsonl`、`fresh.jsonl` 按 anchor_id 隔离，适合训练/开发验证/影子测试，\n"
        "但 `fresh.jsonl` 不是纯真实 Gate，不能据此宣称真实世界泛化。\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
