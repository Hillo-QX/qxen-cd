#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a complete, controlled-date R3A Gate test dataset.

The task/path/evidence fields come from the existing real anchors. Missing
lifecycle dates are filled with deterministic test dates so the full Gate
pipeline can run. This dataset is intentionally not a claim of real-world
temporal evidence; its manifest records ``gate_mode=controlled_synthetic``.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/r3/real_timeline/real_anchors.jsonl"
OUT = ROOT / "data/r3/r3a_gate_test"

VARIANTS = (
    "按 as_of 与事件顺序判断候选的当前效力。",
    "区分暂不适用/历史参考与已被后续版本取代。",
    "先检查候选版本、后续来源和 as_of 的时间关系。",
    "只依据给出的时间线和证据，不使用系统当前日期猜测。",
    "存在明确 superseder 时，不能仅判为 STALE。",
    "材料相关性与 operative status 必须分开判断。",
    "确定 as_of 位于发布、归档、取代事件的哪个区间。",
    "真实且相关的候选也可能因后续版本取代而失效。",
    "没有取代证据时，不要把历史或暂不适用判为 SUPERSEDED。",
    "输出结构化依据，并在最后一行给出唯一效力状态。",
)

STATUS_REASON = {
    "CURRENT": ("ACTIVE_SOURCE", "当前有效"),
    "STALE": ("ARCHIVED_OR_NOT_APPLICABLE", "当前不适用或仅历史参考"),
    "SUPERSEDED": ("EXPLICIT_SUPERSEDER", "已被后续版本或新权威来源取代"),
}

TAIL = (
    "\n请严格按五行输出，不添加解释：\n"
    "证据理由码：<reason_code>\n"
    "权威层级：<T0-T4>\n"
    "材料冲突：<true/false>\n"
    "判定要点：<当前有效/当前不适用或仅历史参考/已被后续版本或新权威来源取代>\n"
    "效力状态：<CURRENT/STALE/SUPERSEDED>"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_anchors() -> list[dict]:
    return [json.loads(line) for line in SRC.read_text(encoding="utf-8").splitlines() if line.strip()]


def authority(candidate_type: str) -> str:
    return {
        "数据文件": "T0",
        "训练日志": "T0",
        "评估报告": "T0",
        "模型权重（二进制）": "T0",
        "配置文件": "T1",
        "训练数据": "T1",
        "冻结测试数据": "T2",
        "周期 checkpoint 文件": "T4",
        "规划文档": "T4",
    }.get(candidate_type, "T3")


def status_for(as_of: date, archived: date, superseded: date) -> str:
    if as_of < archived:
        return "CURRENT"
    if as_of < superseded:
        return "STALE"
    return "SUPERSEDED"


def make_timeline(anchor: dict, index: int) -> list[dict]:
    # Prefer the observed date as the anchor for reproducibility. The dates
    # below are controlled test dates, not reconstructed historical facts.
    observed = date.fromisoformat(anchor["evidence_observed_at"])
    published = observed - timedelta(days=180)
    effective = published + timedelta(days=1)
    archived = observed - timedelta(days=90)
    superseded = observed - timedelta(days=30)
    candidate = f"{anchor['candidate_path']}#v1"
    superseder = f"{anchor['candidate_path']}#v2_superseder"
    points = (
        ("before_archive", published + timedelta(days=30)),
        ("near_archive", archived - timedelta(days=1)),
        ("after_archive", archived + timedelta(days=15)),
        ("before_supersede", superseded - timedelta(days=1)),
        ("after_supersede", superseded + timedelta(days=15)),
        ("late_after_supersede", observed + timedelta(days=30)),
    )
    rows = []
    for phase, as_of in points:
        status = status_for(as_of, archived, superseded)
        reason, point = STATUS_REASON[status]
        events = [
            {"type": "published", "date": published.isoformat(), "source": "controlled_test_timeline"},
            {"type": "effective", "date": effective.isoformat(), "source": "controlled_test_timeline"},
            {"type": "archived", "date": archived.isoformat(), "source": "controlled_test_timeline"},
            {"type": "superseded", "date": superseded.isoformat(), "by": superseder, "source": "controlled_test_timeline"},
        ]
        base = {
            "record_id": f"{anchor['anchor_id']}-{phase}",
            "anchor_id": anchor["anchor_id"],
            "task_kind": anchor.get("task_kind"),
            "task": anchor["task"],
            "candidate_path": candidate,
            "candidate_type": anchor.get("candidate_type", ""),
            "evidence_excerpt": anchor.get("evidence_excerpt", ""),
            "source_exists": anchor.get("source_exists"),
            "source_sha256": anchor.get("source_sha256"),
            "observed_at": anchor["evidence_observed_at"],
            "published_at": published.isoformat(),
            "effective_from": effective.isoformat(),
            "archived_at": archived.isoformat(),
            "superseded_at": superseded.isoformat(),
            "superseded_by": superseder,
            "as_of": as_of.isoformat(),
            "version": 1,
            "events": events,
            "source_refs": [anchor["anchor_id"], anchor["candidate_path"]],
            "date_provenance": "controlled_synthetic",
            "gate_mode": "controlled_synthetic",
            "operative_status": status,
            "reason_code": reason,
            "authority_type": authority(anchor.get("candidate_type", "")),
            "material_conflict": False,
            "materiality_label_original": anchor.get("materiality_label_original"),
            "timeline_phase": phase,
            "provenance": "real_anchor_with_controlled_dates",
            "derivation_note": "任务/路径/证据来自真实锚点；生命周期日期为固定测试日期；状态由 as_of 与事件关系正向计算",
        }
        base["completion"] = (
            f"证据理由码：{reason}\n"
            f"权威层级：{base['authority_type']}\n"
            "材料冲突：false\n"
            f"判定要点：{point}\n"
            f"效力状态：{status}"
        )
        rows.append(base)
    return rows


def prompt(row: dict, variant: str, n: int) -> str:
    # Prompt 构造修复 v6（2026-08-14 Kimi-Expert V3 裁决）：对称语义标注，
    # 三类状态显式给出判别特征（CURRENT/STALE 不注入 superseder，无取代记录）。
    as_of = row["as_of"]
    superseded_at = row.get("superseded_at", "")
    has_superseder_reached = bool(superseded_at and as_of and as_of >= superseded_at)
    status = row["operative_status"]
    if status == "SUPERSEDED":
        events = "; ".join(f"{e['type']}={e['date']}" for e in row["events"])
        follow = f"后续来源：{row['superseded_by']}\n"
        status_line = "最新事件：已被 v2 取代，存在取代记录"
    elif status == "STALE":
        events = "; ".join(
            f"{e['type']}={e['date']}" for e in row["events"] if e.get("type") != "superseded"
        )
        follow = "后续来源：暂未发现已发布的取代者\n"
        status_line = "最新事件：已归档，无取代记录"
    else:  # CURRENT
        events = "; ".join(
            f"{e['type']}={e['date']}" for e in row["events"] if e.get("type") != "superseded"
        )
        follow = "后续来源：暂未发现已发布的取代者\n"
        status_line = "最新事件：未归档，无取代记录"
    return (
        f"任务上下文：{row['task']}\n"
        f"候选材料：{row['candidate_path']}（{row['candidate_type']}）\n"
        f"证据摘录：{row['evidence_excerpt']}\n"
        f"候选版本：v{row['version']}\n"
        f"版本事件：{events}\n"
        f"{follow}"
        f"{status_line}\n"
        f"判定时点 as_of：{row['as_of']}\n"
        f"候选权威层级：{row['authority_type']}\n"
        f"上下文变体{n + 1}：{variant}\n"
        "请结合时间线和证据进行当前效力判定。" + TAIL
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    anchors = load_anchors()
    timeline = [row for i, anchor in enumerate(anchors) for row in make_timeline(anchor, i)]
    contexts = []
    for row in timeline:
        for i, variant in enumerate(VARIANTS):
            item = dict(row)
            item["record_id"] = f"{row['record_id']}-ctx{i + 1:02d}"
            item["prompt"] = prompt(row, variant, i)
            item["context_variant"] = i + 1
            item["provenance"] = "real_anchor_with_controlled_dates_context"
            contexts.append(item)

    train_ids = {f"TR-{i:02d}" for i in range(1, 9)}
    valid_ids = {"TR-09", "TR-10"}
    fresh_ids = {"TR-11", "TR-12"}
    splits = {
        "train": [x for x in contexts if x["anchor_id"] in train_ids],
        "valid": [x for x in contexts if x["anchor_id"] in valid_ids],
        "fresh": [x for x in contexts if x["anchor_id"] in fresh_ids],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    write_jsonl(OUT / "timeline.jsonl", timeline)
    write_jsonl(OUT / "all.jsonl", contexts)
    for name, rows in splits.items():
        write_jsonl(OUT / f"{name}.jsonl", rows)

    manifest = {
        "stage": "R3A-final-gate-test-v1",
        "gate_mode": "controlled_synthetic",
        "real_materials": True,
        "synthetic_dates": True,
        "real_world_gate_eligible": False,
        "purpose": "使用真实任务/路径/证据，补齐可计算的测试生命周期日期，验证模型 Gate 管线",
        "source": str(SRC.relative_to(ROOT)),
        "anchor_count": len(anchors),
        "timeline_rows": len(timeline),
        "context_rows": len(contexts),
        "variants_per_slice": len(VARIANTS),
        "splits": {k: len(v) for k, v in splits.items()},
        "anchor_split": {"train": sorted(train_ids), "valid": sorted(valid_ids), "fresh": sorted(fresh_ids)},
        "labels": dict(sorted(Counter(x["operative_status"] for x in timeline).items())),
        "date_policy": "published/effective/archived/superseded/as_of 为固定控制日期；不代表外部历史事实",
        "label_policy": "状态由 as_of 与 archived_at/superseded_at 的时间关系计算，不由标签反推 as_of",
        "files": {},
    }
    for path in sorted(OUT.glob("*.jsonl")):
        manifest["files"][path.name] = {"rows": sum(1 for _ in path.open(encoding="utf-8")), "sha256": sha256(path)}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(
        "# R3A Final Gate Test Dataset\n\n"
        "本目录使用现有真实锚点的任务、候选路径、证据摘录和哈希；为缺失生命周期字段填入固定控制日期，\n"
        "用于验证 R3A 模型是否能在完整时间线输入上通过 Gate。\n\n"
        "注意：`gate_mode=controlled_synthetic`，日期不是外部历史事实，因此该数据集可以作为\n"
        "模型/管线 Gate 测试，但不能作为真实世界能力证明。真正 Real Gate 仍需替换为可审计的外部事件日期。\n\n"
        "`timeline.jsonl` 有 72 个时间切片；`all.jsonl` 有 720 个表达变体；train/valid/fresh 按 anchor_id 隔离。\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
