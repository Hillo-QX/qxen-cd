#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3A Realistic Gate 评估：直接用 anchor_context_derived 数据的 prompt 字段推理。

与 r3_gate_eval.py 的关键区别：
  - prompt 直接取数据行里的 `prompt` 字段（as_of 为独立正向切片，不由 gold label 反推）
  - 不调用 synth_timeline / make_prompt_v3（那些是 Synthetic 用的 label 反推逻辑）
  - 解析最后一行「效力状态：X」，统计 operative_status_accuracy / superseded_rejection /
    wrong_authority_preference_rate / critical_t0_t1_miss / invalid_output
  - 双口径：12 独立切片（anchor×as_of 去重）+ 全 120 条（含表达变体）

用法:
  ./venv/bin/python scripts/r3a_realistic_gate_eval.py --adapter models/r3a_cot_v4 \
      --data data/r3/real_timeline_context_derived/fresh.jsonl
"""
from __future__ import annotations
import argparse, json, os, re, sys, time
from collections import Counter, defaultdict
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MODEL = "models/qwen3.5-9b-mlx-4bit"
RE_STATUS_LINE = re.compile(r"效力状态\s*[:：]\s*(CURRENT|STALE|SUPERSEDED)", re.IGNORECASE)

# 字段隔离契约（v5）：追加 <think>+JSON 指令到 prompt
TAIL_ISOLATED = (
    "\n请先给出推理过程（放在 <think> 标签内），随后只输出一个 JSON 对象，"
    "不要输出任何其它文字或标记：\n"
    "<think>推理过程</think>\n"
    '{"reason_code": "<19类枚举之一>", "authority": "<T0-T4>", '
    '"conflict": <true/false>, "status": "<CURRENT/STALE/SUPERSEDED>"}'
)


def parse_status(out: str) -> str:
    m = RE_STATUS_LINE.findall(out.strip())
    return (m[-1].upper() if m else "INVALID")


def parse_status_isolated(out: str) -> str:
    """解析字段隔离契约输出：提取最后一个合法 JSON 的 status 字段。"""
    out = out.strip()
    start = out.rfind("{")
    end = out.rfind("}")
    if start < 0 or end <= start:
        return "INVALID"
    try:
        d = json.loads(out[start:end + 1])
    except Exception:
        # JSON 解析失败，回退到五行 status 行
        return parse_status(out)
    if not isinstance(d, dict):
        return "INVALID"
    st = d.get("status")
    if not st:
        return "INVALID"
    s = str(st).strip().upper()
    return s if s in ("CURRENT", "STALE", "SUPERSEDED") else "INVALID"


def gate_metrics(records):
    n = len(records)
    status_acc = sum(r["pred"] == r["gold"] for r in records) / n if n else 0.0
    sup = [r for r in records if r["gold"] == "SUPERSEDED"]
    sup_rej = (sum(r["pred"] == "SUPERSEDED" for r in sup) / len(sup)) if sup else 0.0
    wap = [r for r in records if r["pred"] != r["gold"]]
    wrong_auth_pref = (sum(1 for r in wap if r["pred"] == "CURRENT") / n) if n else 0.0
    crit = [r for r in records if r.get("authority_type") in ("T0", "T1")]
    crit_miss = sum(1 for r in crit if r["pred"] == "INVALID")
    invalid = sum(1 for r in records if r["pred"] == "INVALID")
    return {
        "n": n,
        "operative_status_accuracy": round(status_acc, 4),
        "superseded_rejection": round(sup_rej, 4),
        "wrong_authority_preference_rate": round(wrong_auth_pref, 4),
        "critical_t0_t1_miss": crit_miss,
        "invalid_output": invalid,
        "n_superseded": len(sup),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--contract", default="legacy", choices=("legacy", "isolated"),
                    help="输出契约：legacy=五行文本 / isolated=<think>+JSON")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.data, encoding="utf-8")]
    if args.limit:
        rows = rows[: args.limit]

    parser = parse_status if args.contract == "legacy" else parse_status_isolated
    isolated = args.contract == "isolated"

    from mlx_lm import generate, load
    model, tokenizer = load(MODEL, adapter_path=args.adapter)
    records, t0 = [], time.time()
    for i, r in enumerate(rows):
        prompt = r["prompt"] + (TAIL_ISOLATED if isolated else "")
        f = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        out = generate(model, tokenizer, prompt=f, max_tokens=128, verbose=False)
        pred = parser(out)
        records.append({
            "record_id": r["record_id"], "anchor_id": r["anchor_id"],
            "as_of": r.get("as_of"), "authority_type": r.get("authority_type"),
            "gold": r["operative_status"], "pred": pred, "raw": out,
        })
        if (i + 1) % 40 == 0:
            print(f"[real_gate] {i+1}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    del model

    # 全口径（含表达变体）
    full = gate_metrics(records)
    # 12 独立切片口径：anchor_id + as_of 去重，每切片取第一个变体的 pred
    by_slice = {}
    for r in records:
        key = (r["anchor_id"], r["as_of"])
        by_slice.setdefault(key, r)
    slices = list(by_slice.values())
    slice_metrics = gate_metrics(slices)

    # 混淆矩阵（全口径）
    cm = Counter((r["gold"], r["pred"]) for r in records)

    result = {
        "adapter": args.adapter,
        "data": args.data,
        "contract": args.contract,
        "full_variant_metrics": full,
        "independent_slice_metrics": slice_metrics,
        "independent_slices": len(slices),
        "confusion_matrix": {f"{g}->{p}": c for (g, p), c in cm.items()},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        json.dump(result, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
