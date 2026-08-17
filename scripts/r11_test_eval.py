#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN-CD R1.1 fresh untouched test 评估 — r11 adapter vs base（REL/IRREL 二分类）。

对冻结的 test_fresh.jsonl（540 条, sha256 9989b7b3...）分别用
R1.1 固定 checkpoint（outputs/lora_adapters_r1_recall/adapters.safetensors,
sha256 a4b2eb36...）与 base（无 adapter）推理，gold 取自 completion 字段，
subtype/domain 取自 meta。计算用户要求的全部指标：

  REL Recall / Direct REL Recall / Indirect REL Recall
  Hard-negative / Weak-negative / Noise-negative Accuracy
  False IRREL / False REL（计数与占比）
  Macro F1 / confusion matrix / domain breakdown / invalid outputs

协议与 r1_gate_eval.py 一致（apply_chat_template + enable_thinking=False + max_tokens=4）。

用法：
  ./venv/bin/python scripts/r11_test_eval.py                 # r11 + base 全量
  ./venv/bin/python scripts/r11_test_eval.py --runs r11      # 只跑 r11
  ./venv/bin/python scripts/r11_test_eval.py --limit 20      # 冒烟
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

MODEL = os.path.join(PROJECT_ROOT, "models", "qwen3.5-9b-mlx-4bit")
ADAPTER = os.path.join(PROJECT_ROOT, "outputs", "lora_adapters_r1_recall")
TEST = os.path.join(PROJECT_ROOT, "data", "r1_recall_repair", "test_fresh.jsonl")
OUT = os.path.join(PROJECT_ROOT, "reports", "R1.1_TEST_REPORT.json")

_RE_LABEL = re.compile(r"REL|IRREL")


def parse_label(output: str) -> str:
    m = _RE_LABEL.search(output.upper())
    return m.group(0) if m else "INVALID"


def f1(p, r) -> float:
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def metrics(records: list[dict]) -> dict:
    n = len(records)
    def acc(grp):
        return round(sum(r["pred"] == r["gold"] for r in grp) / len(grp), 4) if grp else 0.0

    rel = [r for r in records if r["gold"] == "REL"]
    irrel = [r for r in records if r["gold"] == "IRREL"]
    pred_rel = [r for r in records if r["pred"] == "REL"]
    pred_irrel = [r for r in records if r["pred"] == "IRREL"]

    tp_rel = sum(1 for r in rel if r["pred"] == "REL")          # gold REL & pred REL
    fn_rel = sum(1 for r in rel if r["pred"] == "IRREL")        # gold REL & pred IRREL (= False IRREL)
    fp_rel = sum(1 for r in irrel if r["pred"] == "REL")        # gold IRREL & pred REL (= False REL)
    tn_rel = sum(1 for r in irrel if r["pred"] == "IRREL")

    rel_recall = tp_rel / len(rel) if rel else 0.0
    irrel_recall = tn_rel / len(irrel) if irrel else 0.0
    rel_precision = tp_rel / len(pred_rel) if pred_rel else 0.0
    irrel_precision = tn_rel / len(pred_irrel) if pred_irrel else 0.0
    macro_f1 = (f1(rel_precision, rel_recall) + f1(irrel_precision, irrel_recall)) / 2.0

    by_subtype = {st: {"n": len([r for r in records if r["meta_subtype"] == st]),
                       "accuracy": acc([r for r in records if r["meta_subtype"] == st])}
                  for st in ("direct_rel", "indirect_rel", "hard_negative", "weak_negative", "noise_negative")}

    by_domain = {}
    for dom in sorted({r["meta_domain"] for r in records}):
        grp = [r for r in records if r["meta_domain"] == dom]
        by_domain[dom] = {"n": len(grp), "accuracy": acc(grp),
                          "rel_recall": round(sum(1 for r in grp if r["gold"] == "REL" and r["pred"] == "REL") / max(1, sum(1 for r in grp if r["gold"] == "REL")), 4)}

    return {
        "n": n,
        "accuracy": round((tp_rel + tn_rel) / n, 4),
        "rel_recall": round(rel_recall, 4),
        "irrel_recall": round(irrel_recall, 4),
        "rel_precision": round(rel_precision, 4),
        "direct_rel_recall": by_subtype["direct_rel"]["accuracy"],
        "indirect_rel_recall": by_subtype["indirect_rel"]["accuracy"],
        "hard_negative_accuracy": by_subtype["hard_negative"]["accuracy"],
        "weak_negative_accuracy": by_subtype["weak_negative"]["accuracy"],
        "noise_negative_accuracy": by_subtype["noise_negative"]["accuracy"],
        "false_irrel_count": fn_rel,                 # REL 被判为 IRREL（漏召回）
        "false_irrel_rate": round(fn_rel / len(rel), 4) if rel else 0.0,
        "false_rel_count": fp_rel,                   # IRREL 被判为 REL（误报）
        "false_rel_rate": round(fp_rel / len(irrel), 4) if irrel else 0.0,
        "macro_f1": round(macro_f1, 4),
        "confusion_matrix": {"pred_vs_gold": {"REL_REL": tp_rel, "REL_IRREL": fn_rel,
                                              "IRREL_REL": fp_rel, "IRREL_IRREL": tn_rel}},
        "subtype_breakdown": by_subtype,
        "domain_breakdown": by_domain,
        "n_invalid": sum(r["pred"] == "INVALID" for r in records),
        "n_rel": len(rel),
        "n_irrel": len(irrel),
    }


def run_eval(name: str, adapter: str | None, records: list[dict]) -> tuple[dict, list[dict]]:
    from mlx_lm import generate, load
    model, tokenizer = load(MODEL, adapter_path=adapter)
    out_records: list[dict] = []
    t0 = time.time()
    for i, rec in enumerate(records):
        formatted = tokenizer.apply_chat_template(
            [{"role": "user", "content": rec["prompt"]}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        out = generate(model, tokenizer, prompt=formatted, max_tokens=4, verbose=False)
        out_records.append({"gold": rec["gold"], "meta_subtype": rec["meta_subtype"],
                            "meta_domain": rec["meta_domain"], "pred": parse_label(out), "raw": out})
        if (i + 1) % 50 == 0:
            print(f"[r11_test] {name}: {i + 1}/{len(records)} ({time.time() - t0:.0f}s)", flush=True)
    del model
    return metrics(out_records), out_records


def main() -> int:
    ap = argparse.ArgumentParser(description="QXEN-CD R1.1 fresh untouched test 评估（r11 vs base）")
    ap.add_argument("--runs", nargs="+", default=["r11", "base"], choices=["r11", "base"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    with open(TEST, encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    if args.limit:
        rows = rows[: args.limit]

    records = []
    missing = 0
    for r in rows:
        gold = r.get("completion") or (r.get("meta") or {}).get("label")
        if not gold:
            missing += 1
            gold = "UNKNOWN"
        meta = r.get("meta") or {}
        records.append({"prompt": r["prompt"], "gold": gold,
                        "meta_subtype": meta.get("subtype", "unknown"),
                        "meta_domain": meta.get("domain", "unknown")})
    print(f"[r11_test] test={len(records)} gold_missing={missing}")

    results: dict[str, dict] = {}
    details: dict[str, list[dict]] = {}
    for name in args.runs:
        adapter = None if name == "base" else ADAPTER
        if adapter and not os.path.isdir(adapter):
            print(f"[r11_test] skip {name}: adapter 不存在 {adapter}")
            continue
        m, det = run_eval(name, adapter, records)
        results[name] = m
        details[name] = det
        print(f"[r11_test] {name}: {json.dumps(m, ensure_ascii=False)}", flush=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"test": TEST, "model": MODEL, "adapter_r11": ADAPTER,
                   "checkpoint_sha256": "a4b2eb361c4b4676ac357fbb7af8395fbd80e60392a32591c9d2d3b57c5bd84f",
                   "test_fresh_sha256": "9989b7b3f95a2195514d04d19666b1f87b9eab59d6fd2360081c0d186bb76607",
                   "results": results, "details": details},
                  fh, ensure_ascii=False, indent=2)
    print(f"\n报告: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
