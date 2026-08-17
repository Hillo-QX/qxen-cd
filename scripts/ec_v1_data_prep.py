#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QXEN-CD Evidence Capsule v1 数据准备（步骤⑤，T005 产物）。

从真实锚点 + 人工稀缺样本构建证据胶囊训练数据。

数据策略（T004 确认，expert APPROVE 条件）：
  - 真实材料为主：data/r3/real_timeline/real_anchors.jsonl（12 条真实锚点，
    task/evidence_excerpt/candidate_path/source_sha256/observed_at 全真实）
  - 人工补稀缺：真实时间线标注（event_date/as_of）、冲突对（R3C）、
    superseded 链（R3A）、压缩样例（R4）——由人工标注文件提供，本脚本只做合并与校验

输出：
  - data/r3/ec_v1/real/real_capsules.jsonl   真实锚点→胶囊样本
  - data/r3/ec_v1/manual/manual_capsules.jsonl 人工稀缺样本（若标注文件存在）
  - data/r3/ec_v1/pool/ec_v1_pool.jsonl      合并池（去重）
  - data/r3/ec_v1/manifest.json

约束：
  - 不修改冻结资产（v5/v6、旧 eval 数据、real_anchors 原始文件）
  - 仅数据准备，不训练不评估
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC_REAL = ROOT / "data/r3/real_timeline/real_anchors.jsonl"
SRC_MANUAL = ROOT / "data/r3/ec_v1/manual/manual_annotations.jsonl"
# 人工稀缺标注模板目录（T006 产物，供标注者参考；正式标注写入 SRC_MANUAL）
MANUAL_TPL_DIR = ROOT / "data/r3/ec_v1/manual_annotations"
OUT_REAL = ROOT / "data/r3/ec_v1/real/real_capsules.jsonl"
OUT_MANUAL = ROOT / "data/r3/ec_v1/manual/manual_capsules.jsonl"
OUT_POOL = ROOT / "data/r3/ec_v1/pool/ec_v1_pool.jsonl"
OUT_MANIFEST = ROOT / "data/r3/ec_v1/manifest.json"

REQUIRED_CAPSULE = ("capsule_id", "source_type", "relevance", "key_evidence", "sufficiency")

SOURCE_TYPE_MAP = {
    "数据文件": "data_file",
    "配置文件": "config",
    "评估报告": "report",
    "代码": "code",
    "模型权重（二进制）": "model_weights",
    "训练日志": "log",
    "规划文档": "doc",
    "环境检查文档": "doc",
    "训练数据": "data_file",
    "冻结测试数据": "data_file",
    "周期 checkpoint 文件": "model_weights",
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def sha256_hex(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_real_capsule(a: dict) -> dict:
    """真实锚点 → 证据胶囊样本（R1 相关性 + R2 证据筛选底座）。

    真实锚点缺 event_date/as_of/operative_status（event_date=0/12），
    因此只填充可核验字段；时间线/效力关系留给 R3A 模块或人工标注。
    """
    cand = a.get("candidate_path", "")
    return {
        "capsule_id": f"EC-R-{a['anchor_id']}",
        "source_type": SOURCE_TYPE_MAP.get(a.get("candidate_type", ""), "other"),
        "relevance": "high",  # 真实锚点 materiality_label_original=REL
        "key_evidence": [
            {
                "text": a.get("evidence_excerpt", ""),
                "source": cand,
                "preserve_verbatim": True,  # 真实证据摘录不可改写
            }
        ],
        "timeline": [],
        "relations": [],
        "conflicts": [],
        "uncertainty": [
            "真实锚点缺少生命周期事件(event_date/as_of)，时间线提取需人工标注或外部证据",
        ],
        "immutable_fields": ["来源路径", "哈希", "证据摘录"],
        "compressible": [],
        "sufficiency": "insufficient",
        "next_step": "人工补全时间线/效力关系标注或检索外部事件证据",
        "reference": [cand, a.get("source_sha256", "")],
        "metadata": {
            "model": "ec-v1-data-prep",
            "contract_version": "v1",
            "created_at": "",
            "as_of": a.get("evidence_observed_at"),
        },
        "anchor_id": a["anchor_id"],
        "provenance": "real_anchor",
        "source_sha256": a.get("source_sha256", ""),
        "evidence_observed_at": a.get("evidence_observed_at"),
        "materiality_label_original": a.get("materiality_label_original"),
    }


def build_manual_capsule(m: dict) -> dict:
    """人工稀缺标注 → 证据胶囊样本。

    人工标注文件格式（manual_annotations.jsonl 每行）：
    {
      "anchor_id": "TR-01",
      "event_date": "2026-05-03",
      "as_of": "2026-07-03",
      "operative_status": "SUPERSEDED",
      "timeline": ["v1 发布：2026-02-14", "v1 归档：2026-05-15", "v2 发布：2026-07-14"],
      "relations": ["as_of 晚于 v1 归档日期", "as_of 晚于 v2 发布日期"],
      "conflicts": [{"a": "...", "b": "..."}],
      "immutable_fields": ["日期", "版本号", "来源路径", "哈希"],
      "compressible": ["背景说明"]
    }
    缺失字段由 real 锚点补齐（task/evidence/source）。
    """
    real = _real_by_id.get(m.get("anchor_id"))
    if real is None:
        raise ValueError(f"manual annotation references unknown anchor_id: {m.get('anchor_id')}")
    cand = real.get("candidate_path", "")
    return {
        "capsule_id": f"EC-M-{real['anchor_id']}-{m.get('variant', 1)}",
        "source_type": SOURCE_TYPE_MAP.get(real.get("candidate_type", ""), "other"),
        "relevance": "high",
        "key_evidence": [
            {"text": real.get("evidence_excerpt", ""), "source": cand, "preserve_verbatim": True}
        ],
        "timeline": m.get("timeline", []),
        "relations": m.get("relations", []),
        "conflicts": m.get("conflicts", []),
        "uncertainty": m.get("uncertainty", []),
        "immutable_fields": m.get("immutable_fields", ["来源路径", "哈希"]),
        "compressible": m.get("compressible", []),
        "sufficiency": m.get("sufficiency", "insufficient"),
        "next_step": m.get("next_step", ""),
        "reference": [cand, real.get("source_sha256", "")],
        "metadata": {
            "model": "ec-v1-data-prep",
            "contract_version": "v1",
            "created_at": "",
            "as_of": m.get("as_of"),
        },
        "anchor_id": real["anchor_id"],
        "event_date": m.get("event_date"),
        "operative_status": m.get("operative_status"),
        "provenance": "manual_annotated",
    }


_real_by_id: dict = {}


def validate_capsule(c: dict) -> list[str]:
    """校验胶囊是否满足契约必填字段。返回缺失字段列表（空=合法）。"""
    missing = [f for f in REQUIRED_CAPSULE if c.get(f) in (None, "", [])]
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description="Evidence Capsule v1 数据准备")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = ap.parse_args()

    reals = load_jsonl(SRC_REAL)
    if not reals:
        print("ERROR: no real anchors found at", SRC_REAL)
        return 1
    global _real_by_id
    _real_by_id = {r["anchor_id"]: r for r in reals}

    real_caps = [build_real_capsule(a) for a in reals]
    manuals_raw = load_jsonl(SRC_MANUAL)
    manual_caps = [build_manual_capsule(m) for m in manuals_raw]

    # 契约校验
    bad = []
    for c in real_caps + manual_caps:
        m = validate_capsule(c)
        if m:
            bad.append((c["capsule_id"], m))
    if bad:
        print("契约校验失败:")
        for cid, miss in bad:
            print(f"  {cid}: missing {miss}")
        return 1

    # 合并池（按 capsule_id 去重，真实优先）
    merged = {}
    for c in manual_caps + real_caps:  # 真实后写覆盖人工（同 id 去重保护）
        merged[c["capsule_id"]] = c
    pool = list(merged.values())

    stats = {
        "real_anchors": len(reals),
        "manual_annotations": len(manuals_raw),
        "real_capsules": len(real_caps),
        "manual_capsules": len(manual_caps),
        "pool_capsules": len(pool),
        "labels": dict(sorted(Counter((x.get("operative_status") or "unknown") for x in pool).items())),
        "provenance": dict(sorted(Counter(x["provenance"] for x in pool).items())),
    }
    if args.dry_run:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    OUT_REAL.parent.mkdir(parents=True, exist_ok=True)
    OUT_MANUAL.parent.mkdir(parents=True, exist_ok=True)
    OUT_POOL.parent.mkdir(parents=True, exist_ok=True)

    def write(path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )

    write(OUT_REAL, real_caps)
    write(OUT_MANUAL, manual_caps)
    write(OUT_POOL, pool)

    manifest = {
        "stage": "R3A-ec-v1-data-prep",
        "contract": "evidence_capsule_v1",
        "policy": "真实材料为主(12锚点), 人工补稀缺(时间线/冲突对/superseded链)",
        "real_warning": "真实锚点event_date=0/12, 时间线依赖人工标注",
        "manual_templates": {
            "dir": "data/r3/ec_v1/manual_annotations/",
            "files": sorted(p.name for p in MANUAL_TPL_DIR.glob("*") if p.is_file()),
            "guide": "annotation_guide.md",
        },
        "stats": stats,
        "files": {
            p.name: {"rows": sum(1 for _ in p.open(encoding="utf-8") if _.strip())}
            for p in (OUT_REAL, OUT_MANUAL, OUT_POOL)
            if p.is_file()
        },
    }
    OUT_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print("manifest:", OUT_MANIFEST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
