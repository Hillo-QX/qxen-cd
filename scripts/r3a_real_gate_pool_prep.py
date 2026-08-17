#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 R3A Realistic Gate 扩展 eval 池（Kimi-Expert 2026-08-14 裁决落地）。

数据源（全部只读）:
  - data/r3/real_timeline/anchor_derived.jsonl   (72 条, 12 锚点全覆盖, status 均衡)
  - data/r3/r3a_gate_test/fresh.jsonl            (120 条, TR-11/12 表达变体)

输出（写到 data/r3/r3a_gate_test_ext/）:
  - eval_pool.jsonl     合并池（derived 72 + gate 120 = 192 条）
  - conflict_variants.jsonl  冲突变体 20 条（Kimi-Expert 裁决: 补 conflict 覆盖率）

conflict 变体构造（从 derived 中 as_of>superseded 且 gold=SUPERSEDED 的 24 条基底选 20）:
  在 prompt 证据摘录后注入一行竞争来源摘要（T0/T1 冲突语义），material_conflict=true，
  gold 保持 SUPERSEDED（冲突中的候选被后续来源取代）。

约束: 幂等, 不修改源, 不占 Metal, seed=42 确定性。
"""
from __future__ import annotations
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DERIVED = ROOT / "data/r3/real_timeline/anchor_derived.jsonl"
SRC_GATE = ROOT / "data/r3/r3a_gate_test/fresh.jsonl"
OUT_DIR = ROOT / "data/r3/r3a_gate_test_ext"
SEED = 42
CONFLICT_TARGET = 20

# 竞争来源摘要模板（T0/T1 冲突）
CONFLICT_LINE = (
    "\n竞争来源摘要：{superseder} 中的 {field} 与候选版本 v{ver} 给出实质冲突的结论，"
    "权威层级 T0（运行时验证输出）"
)


def load_jsonl(p: Path) -> list:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def det_ok(r: dict) -> bool:
    """确定性抽样选择（seed 派生，保证幂等）。"""
    h = hashlib.sha256(f"{SEED}:conflict_variant:{r['record_id']}".encode()).digest()
    return int.from_bytes(h[:2], "big") % 100 < 85  # 24 条基底中选 ~20


def make_conflict_variant(r: dict) -> dict:
    """构造冲突变体：注入竞争来源摘要，gold 保持 SUPERSEDED。"""
    import copy
    v = copy.deepcopy(r)
    superseder = v.get("superseder_path") or "后续版本 v2"
    field = "效力判定结论"
    ver = v.get("version", 1)
    conflict_note = CONFLICT_LINE.format(superseder=superseder, field=field, ver=ver)
    # 注入到 prompt 证据摘录之后
    prompt = v["prompt"]
    marker = "证据摘录："
    idx = prompt.find(marker)
    if idx >= 0:
        line_end = prompt.find("\n", idx)
        if line_end < 0:
            line_end = len(prompt)
        prompt = prompt[:line_end] + conflict_note + prompt[line_end:]
    v["prompt"] = prompt
    v["material_conflict"] = True
    v["conflict_variant"] = True
    v["derivation_note"] = "conflict_variant(注入T0竞争来源, Kimi-Expert裁决补conflict覆盖率)"
    v["record_id"] = v["record_id"] + "-conflict"
    return v


def main() -> int:
    derived = load_jsonl(SRC_DERIVED)
    gate = load_jsonl(SRC_GATE)
    print(f"derived: {len(derived)} | gate: {len(gate)}")

    # 1) 合并池: derived 72 + gate 120（去重 record_id）
    seen = set()
    pool = []
    for r in derived + gate:
        rid = r.get("record_id")
        if rid and rid in seen:
            continue
        seen.add(rid)
        pool.append(r)
    print(f"合并池(去重): {len(pool)}")

    # 2) conflict 变体基底: as_of>superseded 且 gold=SUPERSEDED
    base = []
    for r in derived:
        evs = {e["type"]: e["date"] for e in r.get("events", [])}
        if (evs.get("superseded") and r.get("as_of")
                and r["as_of"] > evs["superseded"] and r["operative_status"] == "SUPERSEDED"):
            base.append(r)
    print(f"conflict 基底: {len(base)}")

    variants = []
    for r in base:
        if len(variants) >= CONFLICT_TARGET:
            break
        if det_ok(r):
            variants.append(make_conflict_variant(r))
    print(f"conflict 变体: {len(variants)} (目标 {CONFLICT_TARGET})")
    if len(variants) < CONFLICT_TARGET:
        print(f"WARN: 变体不足目标（{len(variants)}<{CONFLICT_TARGET}）")

    # 3) 写输出
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pool_path = OUT_DIR / "eval_pool.jsonl"
    var_path = OUT_DIR / "conflict_variants.jsonl"
    with open(pool_path, "w", encoding="utf-8") as f:
        for r in pool:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(var_path, "w", encoding="utf-8") as f:
        for r in variants:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 4) manifest 摘要
    pool_status = Counter(r.get("operative_status") for r in pool)
    pool_anchor = Counter(r.get("anchor_id") for r in pool)
    var_anchor = Counter(r.get("anchor_id") for r in variants)
    manifest = {
        "stage": "R3A-realistic-gate-eval-pool-v1",
        "source": [str(SRC_DERIVED), str(SRC_GATE)],
        "kimi_expert_verdict": "新样本仅作 eval/fresh 不进 train; 补 conflict 变体; 按 anchor_id 分组",
        "pool": {
            "total": len(pool),
            "derived": 72,
            "gate_fresh": 120,
            "status": dict(pool_status),
            "anchor_coverage": len(pool_anchor),
            "anchors": dict(pool_anchor),
        },
        "conflict_variants": {
            "total": len(variants),
            "target": CONFLICT_TARGET,
            "anchor_coverage": len(var_anchor),
            "anchors": dict(var_anchor),
            "gold_status": "SUPERSEDED",
        },
        "note": "real_anchors 12 无 gold 标签(status_basis=未发现完整生命周期), 不作为 eval 池; 仅作证据源",
        "built_at": "2026-08-14T18:00:00Z",
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"输出: {pool_path} ({len(pool)}条), {var_path} ({len(variants)}条)")
    print(f"manifest: {OUT_DIR / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
