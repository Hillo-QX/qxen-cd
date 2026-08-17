#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN-CD R1 门控评估 — base vs r1 adapter 对照（REL/IRREL 二分类）。

对 test.jsonl 540 条分别用 base（无 adapter）与 r1 adapter 推理，
与 ground_truth.jsonl（prompt_sha256 关联）核对，计算指标：

  accuracy / rel_recall / irrel_recall / rel_precision / per_subtype_recall / n_invalid

门控：4 指标全部大于等于 base 才 PASS；base 为满分（1.0）时持平视为通过
（沿用 T045/T049 规则）。子类型维度单独报告 hard/noise/weak 召回用于诊断。

协议与 r1_base_benchmark.py 一致（apply_chat_template + enable_thinking=False + max_tokens=4）。

用法：
  venv/bin/python scripts/r1_gate_eval.py                       # base vs r1 全量
  venv/bin/python scripts/r1_gate_eval.py --runs r1            # 只跑 r1
  venv/bin/python scripts/r1_gate_eval.py --limit 20           # 冒烟
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

MODEL = os.path.join(PROJECT_ROOT, "models", "qwen3.5-9b-mlx-4bit")
ADAPTER = os.path.join(PROJECT_ROOT, "outputs", "lora_adapters_r1_selected")
TEST = os.path.join(PROJECT_ROOT, "data", "r1", "test.jsonl")
GT = os.path.join(PROJECT_ROOT, "ground_truth.jsonl")
OUT = os.path.join(PROJECT_ROOT, "reports", "R1_gate_eval_selected.json")

_RE_LABEL = re.compile(r"REL|IRREL")
SUBTYPES = ("direct_rel", "indirect_rel", "hard_negative", "weak_negative", "noise_negative")


def parse_label(output: str) -> str:
    m = _RE_LABEL.search(output.upper())
    return m.group(0) if m else "INVALID"


def metrics(records: list[dict]) -> dict:
    n = len(records)
    acc = sum(r["pred"] == r["gold"] for r in records) / n if n else 0.0
    rel = [r for r in records if r["gold"] == "REL"]
    irrel = [r for r in records if r["gold"] == "IRREL"]
    rel_recall = sum(r["pred"] == "REL" for r in rel) / len(rel) if rel else 0.0
    irrel_recall = sum(r["pred"] == "IRREL" for r in irrel) / len(irrel) if irrel else 0.0
    pred_rel = [r for r in records if r["pred"] == "REL"]
    rel_precision = sum(r["gold"] == "REL" for r in pred_rel) / len(pred_rel) if pred_rel else 0.0
    per_subtype = {}
    for st in SUBTYPES:
        grp = [r for r in records if r["subtype"] == st]
        if grp:
            per_subtype[st] = {
                "n": len(grp),
                "recall": round(sum(r["pred"] == r["gold"] for r in grp) / len(grp), 4),
            }
    return {
        "n": n,
        "accuracy": round(acc, 4),
        "rel_recall": round(rel_recall, 4),
        "irrel_recall": round(irrel_recall, 4),
        "rel_precision": round(rel_precision, 4),
        "n_invalid": sum(r["pred"] == "INVALID" for r in records),
        "per_subtype_recall": per_subtype,
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
        out_records.append({"gold": rec["gold"], "subtype": rec["subtype"], "pred": parse_label(out), "raw": out})
        if (i + 1) % 50 == 0:
            print(f"[r1_gate] {name}: {i + 1}/{len(records)} ({time.time() - t0:.0f}s)", flush=True)
    del model
    return metrics(out_records), out_records


def main() -> int:
    ap = argparse.ArgumentParser(description="QXEN-CD R1 门控评估（base vs r1 adapter）")
    ap.add_argument("--runs", nargs="+", default=["base", "r1"], choices=["base", "r1"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    with open(TEST, encoding="utf-8") as fh:
        test = [json.loads(l) for l in fh if l.strip()]
    if args.limit:
        test = test[: args.limit]
    gt = [json.loads(l) for l in open(GT, encoding="utf-8") if l.strip()]
    gt_by_sha = {r["prompt_sha256"]: r for r in gt}

    records = []
    missing = 0
    for t in test:
        sha = hashlib.sha256(t["prompt"].encode("utf-8")).hexdigest()
        g = gt_by_sha.get(sha)
        if g is None:
            missing += 1
            g = {"label": t.get("completion"), "subtype": "unknown"}
        records.append({"prompt": t["prompt"], "gold": g["label"], "subtype": g.get("subtype", "unknown")})
    print(f"[r1_gate] test={len(test)} gt_missing={missing}")

    results: dict[str, dict] = {}
    details: dict[str, list[dict]] = {}
    for name in args.runs:
        adapter = None if name == "base" else ADAPTER
        if adapter and not os.path.isdir(adapter):
            print(f"[r1_gate] skip {name}: adapter 不存在 {adapter}")
            continue
        m, det = run_eval(name, adapter, records)
        results[name] = m
        details[name] = det
        print(f"[r1_gate] {name}: {json.dumps(m, ensure_ascii=False)}", flush=True)

    gate = None
    verdict = "INCOMPLETE"
    if "base" in results and "r1" in results:
        keys = ("accuracy", "rel_recall", "irrel_recall")
        gate = {
            k: results["r1"][k] > results["base"][k]
            or (results["base"][k] >= 1.0 and results["r1"][k] >= results["base"][k])
            for k in keys
        }
        verdict = "PASS（3 指标全超/持平满分 base）" if all(gate.values()) else "FAIL（未全超 base）"

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"results": results, "gate": gate, "verdict": verdict, "details": details},
                  fh, ensure_ascii=False, indent=2)
    print(f"\n=== R1 门控判定: {verdict} ===")
    if gate:
        for k, ok in gate.items():
            print(f"  {k}: base={results['base'][k]} r1={results['r1'][k]} {'✓' if ok else '✗'}")
    print(f"报告: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
