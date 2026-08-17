#!/usr/bin/env python3
"""ctxA 决策分类评估：base vs adapter 4 指标对照（T045 §2.4，取代 eval_selection.py）。

对 held-out valid（data/distill_ctxA_chat/valid.jsonl，已按 group 整组划分、
按 token 预算过滤）逐条推理，解析首个标签词，计算 4 指标：

  decision accuracy   4 类正确率
  critical recall     真值 PIN/VERBATIM 中预测非 DROP（且可解析）的比例
  constraint recall   真值 PIN 中预测非 DROP（且可解析）的比例
  stale rejection     真值 DROP 中预测为 DROP 的比例

P1 教训修复：
  - 推理与训练严格同格式：mlx_lm CompletionsDataset 内部 apply_chat_template
    包裹 prompt，本脚本用同一个 tokenizer.apply_chat_template(...,
    add_generation_prompt=True)（P1 传原始字符串导致首步 EOS 空输出）；
  - max_tokens=4，取输出中首个合法标签词（大小写不敏感），解析失败计 INVALID，
    INVALID 在 recall 类指标中一律视为"未保留/未拒绝"（不允许退化达标）；
  - 门控：4 指标全部大于等于 base 才 PASS；base 为满分（1.0）时持平视为通过（T045 §2.5 + T049 Dispatcher 规则）。

用法：
  venv/bin/python scripts/eval_decision.py                      # base vs ctxA 全量对照
  venv/bin/python scripts/eval_decision.py --limit 20           # 冒烟
  venv/bin/python scripts/eval_decision.py --runs base          # 只跑 base
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

LABELS = ("PIN", "DROP", "KEEP", "VERBATIM", "COMPRESS", "REFRESH", "RETRIEVE")
CRITICAL = ("PIN", "VERBATIM")
_RE_LABEL = re.compile(r"PIN|DROP|KEEP|VERBATIM|COMPRESS|REFRESH|RETRIEVE")


def parse_label(output: str) -> str:
    m = _RE_LABEL.search(output.upper())
    return m.group(0) if m else "INVALID"


def metrics(records: list[dict]) -> dict:
    n = len(records)
    acc = sum(r["pred"] == r["gold"] for r in records) / n if n else 0.0

    crit = [r for r in records if r["gold"] in CRITICAL]
    crit_recall = sum(r["pred"] not in ("DROP", "INVALID") for r in crit) / len(crit) if crit else 0.0

    pin = [r for r in records if r["gold"] == "PIN"]
    constraint_recall = sum(r["pred"] not in ("DROP", "INVALID") for r in pin) / len(pin) if pin else 0.0

    drop = [r for r in records if r["gold"] == "DROP"]
    stale_rej = sum(r["pred"] == "DROP" for r in drop) / len(drop) if drop else 0.0

    invalid = sum(r["pred"] == "INVALID" for r in records)
    return {
        "n": n,
        "decision_accuracy": round(acc, 4),
        "critical_recall": round(crit_recall, 4),
        "constraint_recall": round(constraint_recall, 4),
        "stale_rejection": round(stale_rej, 4),
        "n_invalid": invalid,
    }


def run_eval(name: str, model_path: str, adapter_path: str | None, records: list[dict]) -> tuple[dict, list[dict]]:
    from mlx_lm import generate, load

    print(f"[eval_decision] loading {name} (adapter={adapter_path}) ...", flush=True)
    model, tokenizer = load(model_path, adapter_path=adapter_path)
    out_records: list[dict] = []
    t0 = time.time()
    for i, rec in enumerate(records):
        formatted = tokenizer.apply_chat_template(
            [{"role": "user", "content": rec["prompt"]}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,  # qwen3.5 默认 open thinking：max_tokens=4 会被
            # "Thinking Process:..." 吃掉→全 INVALID。关闭 thinking 后模型直接输出标签词。
        )
        output = generate(model, tokenizer, prompt=formatted, max_tokens=4, verbose=False)
        out_records.append({
            "gold": rec["completion"],
            "pred": parse_label(output),
            "raw": output,
        })
        if (i + 1) % 20 == 0:
            print(f"[eval_decision] {name}: {i + 1}/{len(records)} ({time.time() - t0:.0f}s)", flush=True)
    del model  # 释放显存，base 与 adapter 不同时驻留
    return metrics(out_records), out_records


def main() -> int:
    ap = argparse.ArgumentParser(description="ctxA 决策分类 4 指标评估（base 对照）")
    ap.add_argument("--model", default=os.path.join(PROJECT_ROOT, "models", "qwen3.5-9b-mlx-4bit"))
    ap.add_argument("--adapter", default=os.path.join(PROJECT_ROOT, "outputs", "lora_adapters_ctxA"))
    ap.add_argument("--data", default=os.path.join(PROJECT_ROOT, "data", "distill_ctxA_chat", "valid.jsonl"))
    ap.add_argument("--runs", nargs="+", default=["base", "ctxA"], choices=["base", "ctxA"])
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟用）")
    ap.add_argument("--out", default=os.path.join(PROJECT_ROOT, "reports", "ctxA_eval.json"))
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as fh:
        records = [json.loads(ln) for ln in fh if ln.strip()]
    if args.limit:
        records = records[: args.limit]

    results: dict[str, dict] = {}
    details: dict[str, list[dict]] = {}
    for name in args.runs:
        adapter = None if name == "base" else args.adapter
        if adapter and not os.path.isdir(adapter):
            print(f"[eval_decision] skip {name}: adapter 不存在 {adapter}")
            continue
        m, det = run_eval(name, args.model, adapter, records)
        results[name] = m
        details[name] = det
        print(f"[eval_decision] {name}: {json.dumps(m, ensure_ascii=False)}", flush=True)

    gate = None
    if "base" in results and "ctxA" in results:
        keys = ("decision_accuracy", "critical_recall", "constraint_recall", "stale_rejection")
        # T049 Dispatcher 规则：base 指标为满分（1.0）时，持平视为通过（天花板效应，非退化）。
        gate = {
            k: results["ctxA"][k] > results["base"][k]
            or (results["base"][k] >= 1.0 and results["ctxA"][k] >= results["base"][k])
            for k in keys
        }
        verdict = "PASS（4 指标全超/持平满分 base，可进 Phase B）" if all(gate.values()) else "FAIL（未全超 base：调 1 轮或停）"
    else:
        verdict = "INCOMPLETE（需 base 与 ctxA 双侧结果才能门控判定）"

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"results": results, "gate": gate, "verdict": verdict, "details": details},
                  fh, ensure_ascii=False, indent=2)
    print(f"\n=== 门控判定: {verdict} ===")
    if gate:
        for k, ok in gate.items():
            print(f"  {k}: base={results['base'][k]} ctxA={results['ctxA'][k]} {'✓' if ok else '✗'}")
    print(f"报告: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
