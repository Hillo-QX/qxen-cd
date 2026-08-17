#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN-CD R1 v2 checkpoint 选择 — 用 valid.jsonl 早停选优（Dispatcher 方案 C）。

对 outputs/lora_adapters_r1_v2/ 下每个 checkpoint（0000100/0000200/.../0000500/adapters）
用 valid.jsonl（300 条，REL100/IRREL200）逐条推理，计算：
  accuracy / rel_recall / irrel_recall

选择策略（Dispatcher 指令）：
  rel_recall 优先，兼顾 accuracy → 目标 rel_recall 最高且 accuracy 不显著下降。
  评分 = rel_recall（主）+ accuracy（次），输出排序。

协议与 r1_gate_eval.py 一致。valid.jsonl 用于早停（符合约束，test 不参与）。

用法：
  venv/bin/python scripts/r1_select_checkpoint.py [--out reports/R1_checkpoint_selection.json]
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
ADAPTER_DIR = os.path.join(PROJECT_ROOT, "outputs", "lora_adapters_r1_v2")
VALID = os.path.join(PROJECT_ROOT, "data", "r1", "valid.jsonl")
GT = os.path.join(PROJECT_ROOT, "ground_truth.jsonl")

_RE_LABEL = re.compile(r"REL|IRREL")


def parse_label(output: str) -> str:
    m = _RE_LABEL.search(output.upper())
    return m.group(0) if m else "INVALID"


def eval_checkpoint(adapter: str, records: list[dict]) -> dict:
    from mlx_lm import generate, load
    model, tokenizer = load(MODEL, adapter_path=adapter)
    preds = []
    t0 = time.time()
    for rec in records:
        formatted = tokenizer.apply_chat_template(
            [{"role": "user", "content": rec["prompt"]}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        out = generate(model, tokenizer, prompt=formatted, max_tokens=4, verbose=False)
        preds.append(parse_label(out))
    del model
    n = len(records)
    acc = sum(p == r["gold"] for p, r in zip(preds, records)) / n
    rel = [i for i, r in enumerate(records) if r["gold"] == "REL"]
    irrel = [i for i, r in enumerate(records) if r["gold"] == "IRREL"]
    rel_recall = sum(preds[i] == "REL" for i in rel) / len(rel) if rel else 0.0
    irrel_recall = sum(preds[i] == "IRREL" for i in irrel) / len(irrel) if irrel else 0.0
    return {
        "adapter": os.path.basename(adapter),
        "accuracy": round(acc, 4),
        "rel_recall": round(rel_recall, 4),
        "irrel_recall": round(irrel_recall, 4),
        "runtime_s": round(time.time() - t0, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="R1 v2 checkpoint 选择（valid 早停）")
    ap.add_argument("--out", default=os.path.join(PROJECT_ROOT, "reports", "R1_checkpoint_selection.json"))
    args = ap.parse_args()

    valid = [json.loads(l) for l in open(VALID, encoding="utf-8") if l.strip()]
    gt = [json.loads(l) for l in open(GT, encoding="utf-8") if l.strip()]
    gt_by_sha = {r["prompt_sha256"]: r for r in gt}
    records = []
    for v in valid:
        sha = hashlib.sha256(v["prompt"].encode("utf-8")).hexdigest()
        g = gt_by_sha.get(sha, {})
        records.append({"prompt": v["prompt"], "gold": g.get("label", v.get("completion"))})
    print(f"[r1_select] valid={len(records)}")

    # 收集 checkpoints：0000NNN_adapters.safetensors 文件 + 根 adapters.safetensors
    ckpt_files = []
    for f in sorted(os.listdir(ADAPTER_DIR)):
        if re.fullmatch(r"\d{7}_adapters\.safetensors", f):
            ckpt_files.append(os.path.join(ADAPTER_DIR, f))
    root_weights = os.path.join(ADAPTER_DIR, "adapters.safetensors")
    if os.path.exists(root_weights):
        ckpt_files.append(root_weights)
    print(f"[r1_select] 待评估 checkpoint: {[os.path.basename(c) for c in ckpt_files]}")

    import shutil
    import tempfile

    results = []
    for ckpt in ckpt_files:
        # mlx_lm load 需要目录：adapter_config.json + adapters.safetensors
        tmp = tempfile.mkdtemp(prefix="r1_ckpt_")
        shutil.copy(os.path.join(ADAPTER_DIR, "adapter_config.json"), os.path.join(tmp, "adapter_config.json"))
        shutil.copy(ckpt, os.path.join(tmp, "adapters.safetensors"))
        r = eval_checkpoint(tmp, records)
        r["adapter"] = os.path.basename(ckpt)
        results.append(r)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"[r1_select] {r}", flush=True)

    # 排序：rel_recall 主、accuracy 次
    results.sort(key=lambda r: (r["rel_recall"], r["accuracy"]), reverse=True)
    print("\n=== 排序（rel_recall 优先）===")
    for r in results:
        print(f"  {r['adapter']}: rel_recall={r['rel_recall']} acc={r['accuracy']} irrel_recall={r['irrel_recall']}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"valid_n": len(records), "results": results}, f, ensure_ascii=False, indent=2)
    print(f"报告: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
