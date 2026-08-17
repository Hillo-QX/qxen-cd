#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN-CD R1 Base benchmark — REL/IRREL 二分类，base 模型（无 adapter）对照。

对 test.jsonl 540 条逐条推理，输出 REL|IRREL 标签，与 ground_truth.jsonl
（按 prompt_sha256 关联）核对，计算指标：

  accuracy            总体正确率
  rel_recall          REL 召回
  irrel_recall        IRREL 召回
  rel_precision       REL 精确率
  per_subtype_recall  direct_rel / indirect_rel / hard_negative / weak_negative / noise_negative 召回

与 ctxA 评估同款协议：
  - mlx_lm apply_chat_template(add_generation_prompt=True, enable_thinking=False)
  - max_tokens=4，解析首个合法标签（REL|IRREL），失败计 INVALID
  - 报告写入 reports/R1_base_benchmark.json + .md

用法：
  venv/bin/python scripts/r1_base_benchmark.py                # base 全量 540 条
  venv/bin/python scripts/r1_base_benchmark.py --limit 20     # 冒烟
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
TEST = os.path.join(PROJECT_ROOT, "test.jsonl")
GT = os.path.join(PROJECT_ROOT, "ground_truth.jsonl")
OUT_JSON = os.path.join(PROJECT_ROOT, "reports", "R1_base_benchmark.json")
OUT_MD = os.path.join(PROJECT_ROOT, "reports", "R1_base_benchmark.md")

_RE_LABEL = re.compile(r"REL|IRREL")
SUBTYPES = ("direct_rel", "indirect_rel", "hard_negative", "weak_negative", "noise_negative")


def parse_label(output: str) -> str:
    m = _RE_LABEL.search(output.upper())
    return m.group(0) if m else "INVALID"


def load_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="QXEN-CD R1 Base benchmark（REL/IRREL 二分类）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟用）")
    ap.add_argument("--out", default=OUT_JSON)
    args = ap.parse_args()

    test = load_jsonl(TEST)
    if args.limit:
        test = test[: args.limit]
    gt = load_jsonl(GT)
    gt_by_sha = {r["prompt_sha256"]: r for r in gt}

    # 关联 ground truth
    records = []
    missing = 0
    for t in test:
        sha = hashlib.sha256(t["prompt"].encode("utf-8")).hexdigest()
        g = gt_by_sha.get(sha)
        if g is None:
            missing += 1
            g = {"label": t.get("completion"), "subtype": "unknown"}
        records.append({"prompt": t["prompt"], "gold": g["label"], "subtype": g.get("subtype", "unknown")})

    print(f"[r1_base] test={len(test)} gt_missing={missing}")

    from mlx_lm import generate, load
    model, tokenizer = load(MODEL, adapter_path=None)
    t0 = time.time()
    for i, rec in enumerate(records):
        formatted = tokenizer.apply_chat_template(
            [{"role": "user", "content": rec["prompt"]}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        out = generate(model, tokenizer, prompt=formatted, max_tokens=4, verbose=False)
        rec["pred"] = parse_label(out)
        rec["raw"] = out
        if (i + 1) % 50 == 0:
            print(f"[r1_base] {i + 1}/{len(records)} ({time.time() - t0:.0f}s)", flush=True)
    del model

    n = len(records)
    acc = sum(r["pred"] == r["gold"] for r in records) / n if n else 0.0
    rel = [r for r in records if r["gold"] == "REL"]
    irrel = [r for r in records if r["gold"] == "IRREL"]
    rel_recall = sum(r["pred"] == "REL" for r in rel) / len(rel) if rel else 0.0
    irrel_recall = sum(r["pred"] == "IRREL" for r in irrel) / len(irrel) if irrel else 0.0
    pred_rel = [r for r in records if r["pred"] == "REL"]
    rel_precision = sum(r["gold"] == "REL" for r in pred_rel) / len(pred_rel) if pred_rel else 0.0
    invalid = sum(r["pred"] == "INVALID" for r in records)

    per_subtype = {}
    for st in SUBTYPES:
        grp = [r for r in records if r["subtype"] == st]
        if grp:
            per_subtype[st] = {
                "n": len(grp),
                "recall": round(sum(r["pred"] == r["gold"] for r in grp) / len(grp), 4),
            }

    metrics = {
        "model": "qwen3.5-9b-mlx-4bit (base, no adapter)",
        "n": n,
        "accuracy": round(acc, 4),
        "rel_recall": round(rel_recall, 4),
        "irrel_recall": round(irrel_recall, 4),
        "rel_precision": round(rel_precision, 4),
        "n_invalid": invalid,
        "per_subtype_recall": per_subtype,
        "runtime_s": round(time.time() - t0, 1),
    }
    print(f"[r1_base] {json.dumps(metrics, ensure_ascii=False, indent=1)}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    report = {"task": "QXEN-CD R1 Base benchmark", "metrics": metrics, "records": records}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # markdown 摘要
    md = []
    md.append("# QXEN-CD R1 Base Benchmark（REL/IRREL 二分类）\n")
    md.append(f"- 模型: qwen3.5-9b-mlx-4bit（base，无 adapter）")
    md.append(f"- 评估集: test.jsonl {n} 条（ground_truth 关联缺失 {missing}）")
    md.append(f"- 准确率: **{metrics['accuracy']}**")
    md.append(f"- REL 召回: {metrics['rel_recall']} | IRREL 召回: {metrics['irrel_recall']} | REL 精确率: {metrics['rel_precision']}")
    md.append(f"- INVALID 数: {metrics['n_invalid']} | 耗时: {metrics['runtime_s']}s\n")
    md.append("## 按子类型召回")
    for st, v in per_subtype.items():
        md.append(f"- {st}: {v['n']} 条, recall={v['recall']}")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"报告: {args.out} / {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
