#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 R1.4 真实轨迹构建 R3A 时间线锚点与 anchor-derived 扩展集。

重要：真实轨迹没有完整生命周期事件，因此本脚本不伪造真实历史。
real_anchors.jsonl 只记录可核验事实；anchor_derived.jsonl 明确标注
受控派生的版本/事件/as_of，用于训练与研究，不作为纯真实 Gate。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/r1.4/C/eval_trajectories_raw.jsonl"
OUT = ROOT / "data/r3/real_timeline"
BASE_DATE = date(2026, 1, 1)

TAIL = (
    "\n请严格按五行输出，不添加解释：\n"
    "证据理由码：<reason_code>\n"
    "权威层级：<T0-T4>\n"
    "材料冲突：<true/false>\n"
    "判定要点：<当前有效/当前不适用或仅历史参考/已被后续版本或新权威来源取代>\n"
    "效力状态：<CURRENT/STALE/SUPERSEDED>"
)

AUTHORITY = {
    "数据文件": "T0",
    "训练日志": "T0",
    "评估报告": "T0",
    "模型权重（二进制）": "T0",
    "配置文件": "T1",
    "训练数据": "T1",
    "冻结测试数据": "T2",
    "周期 checkpoint 文件": "T4",
    "规划文档": "T4",
    "环境检查文档": "T4",
}


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def observed_at(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()


def load_rows():
    return [json.loads(x) for x in SRC.read_text(encoding="utf-8").splitlines() if x.strip()]


def anchor(row: dict, index: int) -> dict:
    path = ROOT / row["candidate_path"]
    return {
        "anchor_id": row["trajectory_id"],
        "task_kind": row.get("task_kind"),
        "task": row["task"],
        "candidate_path": row["candidate_path"],
        "candidate_type": row.get("candidate_type", ""),
        "evidence_excerpt": row.get("content", ""),
        "materiality_label_original": row.get("expected"),
        "materiality_note": row.get("note", ""),
        "source_exists": path.is_file(),
        "source_sha256": sha256(path),
        "evidence_observed_at": observed_at(path),
        "event_date": None,
        "as_of": None,
        "operative_status": None,
        "provenance": "real_anchor",
        "status_basis": "未发现完整生命周期事件；不能从 REL/IRREL 推导效力状态",
    }


def status_for(as_of: date, published: date, archived: date, superseded: date) -> str:
    if as_of < archived:
        return "CURRENT"
    if as_of < superseded:
        return "STALE"
    return "SUPERSEDED"


def reason_for(status: str) -> str:
    return {
        "CURRENT": "ACTIVE_SOURCE",
        "STALE": "ARCHIVED_OR_NOT_APPLICABLE",
        "SUPERSEDED": "EXPLICIT_SUPERSEDER",
    }[status]


def make_derived(a: dict, anchor_index: int) -> list[dict]:
    """为每个真实锚点构建 9 个时间切片；事件先定，状态由关系计算。"""
    observed = date.fromisoformat(a["evidence_observed_at"]) if a["evidence_observed_at"] else BASE_DATE + timedelta(days=anchor_index * 17)
    published = observed - timedelta(days=180)
    archived = observed - timedelta(days=90)
    superseded = observed - timedelta(days=30)
    candidate_v1 = a["candidate_path"] + "#v1"
    superseder = a["candidate_path"] + "#v2_superseder"
    points = [
        ("before_archive", published + timedelta(days=30)),
        ("near_archive", archived - timedelta(days=1)),
        ("after_archive", archived + timedelta(days=15)),
        ("before_supersede", superseded - timedelta(days=1)),
        ("after_supersede", superseded + timedelta(days=15)),
        ("late_after_supersede", observed + timedelta(days=30)),
    ]
    out = []
    for phase, as_of in points:
        status = status_for(as_of, published, archived, superseded)
        record_id = f"{a['anchor_id']}-{phase}"
        # Prompt 构造修复 v6（2026-08-14 Kimi-Expert 裁决）：对称语义标注，
        # 显式给出三类状态的判别特征（CURRENT/STALE 无 superseder 提示）。
        if status == "SUPERSEDED":
            version_line = (
                f"版本事件：v1 发布 {published.isoformat()}；归档 {archived.isoformat()}；"
                f"v2 取代者发布 {superseded.isoformat()}\n"
                f"后续来源：{superseder}\n"
                "最新事件：已被 v2 取代，存在取代记录"
            )
        elif status == "STALE":
            version_line = (
                f"版本事件：v1 发布 {published.isoformat()}；归档 {archived.isoformat()}\n"
                "后续来源：暂未发现已发布的取代者\n"
                "最新事件：已归档，无取代记录"
            )
        else:  # CURRENT
            version_line = (
                f"版本事件：v1 发布 {published.isoformat()}\n"
                "后续来源：暂未发现已发布的取代者\n"
                "最新事件：未归档，无取代记录"
            )
        context = (
            f"任务：{a['task']}\n"
            f"候选来源：{candidate_v1}\n"
            f"候选类型：{a['candidate_type']}\n"
            f"证据摘录：{a['evidence_excerpt']}\n"
            f"判定时点 as_of：{as_of.isoformat()}\n"
            f"{version_line}\n"
            f"原始材料相关性标签（不用于效力标签）：{a['materiality_label_original']}"
        )
        out.append({
            "record_id": record_id,
            "anchor_id": a["anchor_id"],
            "task_kind": a["task_kind"],
            "task": a["task"],
            "candidate_path": candidate_v1,
            "superseder_path": superseder,
            "candidate_type": a["candidate_type"],
            "version": 1,
            "events": [
                {"type": "published", "date": published.isoformat(), "provenance": "anchor_derived"},
                {"type": "archived", "date": archived.isoformat(), "provenance": "anchor_derived"},
                {"type": "superseded", "date": superseded.isoformat(), "by": superseder, "provenance": "anchor_derived"},
            ],
            "as_of": as_of.isoformat(),
            "operative_status": status,
            "reason_code": reason_for(status),
            "authority_type": AUTHORITY.get(a["candidate_type"], "T3"),
            "material_conflict": False,
            "prompt": context + TAIL,
            "completion": (
                f"证据理由码：{reason_for(status)}\n"
                f"权威层级：{AUTHORITY.get(a['candidate_type'], 'T3')}\n"
                "材料冲突：false\n"
                f"判定要点：{ {'CURRENT':'当前有效','STALE':'当前不适用或仅历史参考','SUPERSEDED':'已被后续版本或新权威来源取代'}[status] }\n"
                f"效力状态：{status}"
            ),
            "provenance": "anchor_derived",
            "anchor_evidence_ref": a["anchor_id"],
            "derivation_note": "日期与版本事件为受控派生；状态由 as_of 与事件关系计算，不由标签反推 as_of",
        })
    return out


def write_jsonl(path: Path, rows: list[dict]):
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    anchors = [anchor(row, i) for i, row in enumerate(load_rows())]
    derived = [x for i, a in enumerate(anchors) for x in make_derived(a, i)]
    # 按 anchor_id 隔离，避免同一真实轨迹的切片泄漏到不同集合。
    train_ids = {f"TR-{i:02d}" for i in range(1, 9)}
    valid_ids = {"TR-09", "TR-10"}
    fresh_ids = {"TR-11", "TR-12"}
    train = [x for x in derived if x["anchor_id"] in train_ids]
    valid = [x for x in derived if x["anchor_id"] in valid_ids]
    fresh = [x for x in derived if x["anchor_id"] in fresh_ids]
    write_jsonl(OUT / "real_anchors.jsonl", anchors)
    write_jsonl(OUT / "anchor_derived.jsonl", derived)
    write_jsonl(OUT / "train.jsonl", train)
    write_jsonl(OUT / "valid.jsonl", valid)
    write_jsonl(OUT / "fresh.jsonl", fresh)
    manifest = {
        "stage": "R3A-real-timeline-anchor-derived-v1",
        "source": str(SRC.relative_to(ROOT)),
        "real_anchor_rows": len(anchors),
        "derived_rows": len(derived),
        "splits": {"train": len(train), "valid": len(valid), "fresh": len(fresh)},
        "anchor_split": {"train": sorted(train_ids), "valid": sorted(valid_ids), "fresh": sorted(fresh_ids)},
        "real_anchor_policy": "只保留真实路径/摘录/文件哈希/观测日期；缺少生命周期日期则为 null",
        "derived_policy": "版本事件和 as_of 为受控派生；operative_status 由事件关系计算",
        "label_policy": "不把原始 REL/IRREL 映射为 CURRENT/STALE/SUPERSEDED",
        "fresh_gate_warning": "fresh 为 anchor-derived，不是纯真实时间线 Gate；需补充真实事件日期后才可升级为 real_gate",
        "files": {},
    }
    for p in sorted(OUT.glob("*.jsonl")):
        manifest["files"][p.name] = {"rows": sum(1 for _ in p.open(encoding="utf-8")), "sha256": sha256(p)}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(
        "# R3A 时间线锚点与派生集\n\n"
        "`real_anchors.jsonl` 是真实锚点，但现有轨迹缺少完整生命周期日期，因此不填充虚假的 event_date/as_of。\n\n"
        "`anchor_derived.jsonl`、`train.jsonl`、`valid.jsonl`、`fresh.jsonl` 基于真实轨迹的任务、候选和证据摘录，\n"
        "派生出 v1 发布、归档、v2 取代和独立 as_of；状态由时间关系计算。它们是 anchor-derived，不能宣称为纯真实 Gate。\n\n"
        "按 anchor_id 隔离 train/valid/fresh，避免同一轨迹的时间切片泄漏。要建立真正 real Gate，仍需人工/系统补齐真实事件日期和版本替代证据。\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
