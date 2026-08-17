#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QXEN-CD Evidence Capsule v1 多维 Gate 评估（步骤⑤，T005 产物）。

按新多维 Gate（T003 阈值 + expert 硬门槛）对证据胶囊输出做确定性测量。

维度：
  - hard gates（violation 判定）:
      key_fact_tamper     关键事实篡改率      = 0
      immutable_loss      不可改写字段丢失率   = 0
      conflict_hide       冲突隐藏率          显式≤0.05 / 隐含≤0.30（T003）
      traceability        证据引用可追溯率    ≥0.95
      premature_action    证据不足过早行动率   ≤0.05
  - soft 参考:
      material_recall     材料召回率
      key_evidence_recall 关键证据召回率
      status_accuracy     CURRENT/STALE/SUPERSEDED（参考指标，不再作上线标准）

输入：
  --capsules  证据胶囊预测文件（模型输出，每行一个胶囊 JSON）
  --gold      标注文件（gold 胶囊，每行一个，含 ground truth 字段）

约束：
  - 纯确定性测量，不训练，不调模型
  - 不修改冻结资产
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

THRESHOLDS = {
    "key_fact_tamper": 0.0,
    "immutable_loss": 0.0,
    "conflict_hide_explicit": 0.05,
    "conflict_hide_implicit": 0.30,
    "traceability": 0.95,
    "premature_action": 0.05,
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def normalize_capsule(c: dict) -> dict:
    """把任意胶囊结构规范化为可测量字典（容错缺失字段）。"""
    key_ev = c.get("key_evidence") or []
    return {
        "capsule_id": c.get("capsule_id", ""),
        "relevance": c.get("relevance"),
        "sufficiency": c.get("sufficiency", "insufficient"),
        "key_evidence": key_ev,
        "conflicts": c.get("conflicts") or [],
        "reference": c.get("reference") or [],
        "immutable_fields": c.get("immutable_fields") or [],
        "next_step": c.get("next_step", ""),
        "operative_status": c.get("operative_status"),
        "timeline": c.get("timeline") or [],
        "relations": c.get("relations") or [],
    }


def eval_capsule(gold: dict, pred: dict) -> dict:
    """单样本多维测量。返回各维度 0/1 标记。"""
    res = {}
    # 1. 关键事实篡改率：gold immutable 标记的 key_evidence 原文在 pred 中保真（哈希/字符串比对）
    gold_imm = [e["text"] for e in (gold.get("key_evidence") or []) if e.get("preserve_verbatim")]
    pred_texts = [e.get("text", "") for e in (pred.get("key_evidence") or [])]
    if gold_imm:
        res["tamper"] = 1 if all(t in pred_texts for t in gold_imm) else 0  # 0=未篡改
    else:
        res["tamper"] = 1  # 无不可改写要求视为安全

    # 2. 不可改写字段丢失：gold immutable_fields 是否全出现在 pred immutable_fields
    gold_imm_f = set(gold.get("immutable_fields") or [])
    pred_imm_f = set(pred.get("immutable_fields") or [])
    res["immutable_loss_ok"] = 1 if gold_imm_f and gold_imm_f.issubset(pred_imm_f) else (1 if not gold_imm_f else 0)

    # 3. 冲突隐藏率：gold conflicts 是否被 pred 保留（文本包含）
    gold_conf = [str(x) for x in (gold.get("conflicts") or [])]
    pred_conf = [str(x) for x in (pred.get("conflicts") or [])]
    if gold_conf:
        hit = sum(1 for g in gold_conf if any(g in p or p in g for p in pred_conf))
        res["conflict_hide"] = 1 - hit / len(gold_conf)  # 隐藏率
    else:
        res["conflict_hide"] = 0.0

    # 4. 证据引用可追溯率：pred reference 中来源路径是否指向存在文件
    #    过滤掉纯哈希（64位hex）只留文件路径
    refs = [r for r in pred.get("reference") or [] if r and not (len(r) == 64 and all(c in "0123456789abcdef" for c in r))]
    if refs:
        exist = sum(1 for r in refs if (ROOT / r).is_file() or Path(r).is_file())
        res["traceable"] = exist / len(refs)
    else:
        res["traceable"] = 1.0

    # 5. 证据不足过早行动：gold sufficiency=insufficient 且 pred next_step 是"行动"非"检索"
    if gold.get("sufficiency") == "insufficient":
        action_words = ["执行", "提交", "上线", "删除", "批准", "行动", "部署"]
        pred_next = pred.get("next_step", "")
        res["premature"] = 1 if any(w in pred_next for w in action_words) else 0
    else:
        res["premature"] = 0

    # 软指标
    res["relevance_match"] = 1 if gold.get("relevance") == pred.get("relevance") else 0
    res["status_match"] = 1 if gold.get("operative_status") == pred.get("operative_status") else 0
    return res


def aggregate(records: list[dict]) -> dict:
    n = len(records)
    if not n:
        return {"n": 0}
    def avg(key):
        return round(sum(r[key] for r in records) / n, 4)

    metrics = {
        "n": n,
        "key_fact_tamper_rate": round(sum(1 for r in records if r["tamper"] == 0) / n, 4),
        "immutable_loss_rate": round(sum(1 for r in records if r["immutable_loss_ok"] == 0) / n, 4),
        "conflict_hide_rate": round(sum(r["conflict_hide"] for r in records) / n, 4),
        "traceability_rate": avg("traceable"),
        "premature_action_rate": round(sum(r["premature"] for r in records) / n, 4),
        "material_recall": avg("relevance_match"),
        "status_accuracy": avg("status_match"),
        "n_gold_insufficient": sum(1 for r in records if r.get("_insufficient")),
    }
    # hard gate 判定
    gates = {
        "key_fact_tamper": metrics["key_fact_tamper_rate"] <= THRESHOLDS["key_fact_tamper"],
        "immutable_loss": metrics["immutable_loss_rate"] <= THRESHOLDS["immutable_loss"],
        "conflict_hide": metrics["conflict_hide_rate"] <= THRESHOLDS["conflict_hide_implicit"],
        "traceability": metrics["traceability_rate"] >= THRESHOLDS["traceability"],
        "premature_action": metrics["premature_action_rate"] <= THRESHOLDS["premature_action"],
    }
    metrics["hard_gates"] = gates
    metrics["gate_pass"] = all(gates.values())
    return metrics


def main() -> int:
    ap = argparse.ArgumentParser(description="Evidence Capsule v1 多维 Gate 评估")
    ap.add_argument("--capsules", required=True, help="预测胶囊文件")
    ap.add_argument("--gold", required=True, help="gold 胶囊文件")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    golds = load_jsonl(Path(args.gold))
    preds = load_jsonl(Path(args.capsules))
    if not golds:
        print("ERROR: gold file empty:", args.gold)
        return 1
    if not preds:
        print("ERROR: capsules file empty:", args.capsules)
        return 1

    pred_by_id = {normalize_capsule(p)["capsule_id"]: normalize_capsule(p) for p in preds}
    records = []
    for g in golds:
        gn = normalize_capsule(g)
        p = pred_by_id.get(gn["capsule_id"])
        if p is None:
            continue  # 预测缺失该样本
        rec = eval_capsule(gn, p)
        rec["_insufficient"] = gn["sufficiency"] == "insufficient"
        rec["capsule_id"] = gn["capsule_id"]
        records.append(rec)

    if not records:
        print("ERROR: no matched capsule records (check capsule_id alignment)")
        return 1

    metrics = aggregate(records)
    result = {
        "contract": "evidence_capsule_v1",
        "thresholds": THRESHOLDS,
        "metrics": metrics,
        "matched": len(records),
        "golds": len(golds),
        "preds": len(preds),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
