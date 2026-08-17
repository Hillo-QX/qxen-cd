#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3A 0-shot STALE 判别验证（Kimi-Expert V4 裁决 ACTION：排除量化混淆变量）。

4bit Base（无 LoRA）直接对 eval_pool 中的 STALE 样本推理，
用 isolated 契约（<think>+JSON），统计 STALE 判别正确率。
目的：确认 STALE 84% 错误是模型能力上限，还是量化 Base 本身在
细粒度时序判别上的固有问题。

用法：
  venv/bin/python scripts/r3a_zeroshot_stale_check.py [--limit 20]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MODEL = "models/qwen3.5-9b-mlx-4bit"
POOL = "data/r3/r3a_gate_test_ext/eval_pool.jsonl"
OUT = "reports/r3/r3a_zeroshot_stale_check.json"

RE_STATUS = re.compile(r"效力状态\s*[:：]\s*(CURRENT|STALE|SUPERSEDED)", re.IGNORECASE)

TAIL_ISOLATED = (
    "\n请先给出推理过程（放在 <think> 标签内），随后只输出一个 JSON 对象，"
    "不要输出任何其它文字或标记：\n"
    "<think>推理过程</think>\n"
    '{"reason_code": "<19类枚举之一>", "authority": "<T0-T4>", '
    '"conflict": <true/false>, "status": "<CURRENT/STALE/SUPERSEDED>"}'
)


def parse_status_isolated(out: str) -> str:
    out = out.strip()
    start = out.rfind("{")
    end = out.rfind("}")
    if start < 0 or end <= start:
        return "INVALID"
    try:
        d = json.loads(out[start:end + 1])
    except Exception:
        m = RE_STATUS.findall(out)
        return m[-1].upper() if m else "INVALID"
    if not isinstance(d, dict):
        return "INVALID"
    st = str(d.get("status", "")).strip().upper()
    return st if st in ("CURRENT", "STALE", "SUPERSEDED") else "INVALID"


def load_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="R3A 0-shot STALE 判别验证")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    rows = load_jsonl(POOL)
    stale = [r for r in rows if r["operative_status"] == "STALE"]
    # 按 anchor_id+as_of 去重取独立切片优先
    seen, chosen = set(), []
    for r in stale:
        key = (r["anchor_id"], r["as_of"])
        if key not in seen:
            seen.add(key)
            chosen.append(r)
    # 补足到 limit（若独立切片不足则用剩余变体）
    extra = [r for r in stale if r not in chosen]
    chosen += extra[: max(0, args.limit - len(chosen))]
    chosen = chosen[: args.limit]
    print(f"[zeroshot] STALE total={len(stale)} chosen={len(chosen)} (limit={args.limit})")

    from mlx_lm import generate, load
    model, tokenizer = load(MODEL, adapter_path=None)
    records, t0 = [], time.time()
    for i, r in enumerate(chosen):
        prompt = r["prompt"] + TAIL_ISOLATED
        f = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        out = generate(model, tokenizer, prompt=f, max_tokens=128, verbose=False)
        pred = parse_status_isolated(out)
        records.append({
            "record_id": r["record_id"], "anchor_id": r["anchor_id"],
            "gold": "STALE", "pred": pred, "as_of": r.get("as_of"),
        })
        if (i + 1) % 10 == 0:
            print(f"[zeroshot] {i+1}/{len(chosen)} ({time.time()-t0:.0f}s)", flush=True)
    del model

    n = len(records)
    correct = sum(r["pred"] == "STALE" for r in records)
    cm = Counter(r["pred"] for r in records)
    result = {
        "model": MODEL,
        "adapter": "NONE (0-shot base)",
        "data": POOL,
        "n": n,
        "stale_accuracy": round(correct / n, 4) if n else 0.0,
        "pred_distribution": dict(cm),
        "note": "4bit 量化 Base 0-shot；排除 LoRA 训练因素，定位 STALE 84% 错误是否量化/模型固有问题",
        "records": records,
    }
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, ensure_ascii=False, indent=2))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"报告: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
