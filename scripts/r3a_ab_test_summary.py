#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3A 三条件 A/B 对照 + 真实时间线预检汇总。

用途（Metal 空闲时运行）：
  1. 汇总三条件 fresh(540) 五指标对照表。
  2. 用指定 adapter 对 anchor_derived 独立切片做方向性预检（不构成 Gate 验收）。

只读，不占 Metal 直到调用 evaluate 加载模型。
"""
from __future__ import annotations
import json, argparse, sys, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

RE_STATUS_LINE = re.compile(r"效力状态\s*[:：]\s*(CURRENT|STALE|SUPERSEDED)", re.IGNORECASE)


def load_jsonl(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


def summarize(condition_files):
    """汇总三条件五指标。"""
    rows = []
    for name, path in condition_files.items():
        d = json.load(open(path))
        m = d["metrics"]
        rows.append((name, m))
    return rows


def eval_anchor_derived(adapter_dir: str, split: str = "fresh", limit: int = 0):
    """对 anchor_derived 独立切片做方向性预检。"""
    from mlx_lm import generate, load
    from r3a_v3_context import make_prompt, synth_timeline  # 不适用，anchor 已有 prompt
    MODEL = "models/qwen3.5-9b-mlx-4bit"
    src = f"data/r3/real_timeline_context_derived/{split}.jsonl"
    rows = load_jsonl(src)
    if limit:
        rows = rows[:limit]
    model, tokenizer = load(MODEL, adapter_path=adapter_dir)
    correct, total = 0, 0
    conf = {}
    for r in rows:
        f = tokenizer.apply_chat_template(
            [{"role": "user", "content": r["prompt"]}], tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        out = generate(model, tokenizer, prompt=f, max_tokens=96, verbose=False)
        pred = RE_STATUS_LINE.findall(out)
        pred = pred[-1].upper() if pred else "INVALID"
        gold = r["operative_status"]
        conf[(gold, pred)] = conf.get((gold, pred), 0) + 1
        total += 1
        if pred == gold:
            correct += 1
    del model
    acc = correct / total if total else 0.0
    return {"adapter": adapter_dir, "split": split, "n": total,
            "accuracy": round(acc, 4), "confusion": {f"{k[0]}->{k[1]}": v for k, v in sorted(conf.items())}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summarize", action="store_true", help="汇总三条件对照表")
    ap.add_argument("--eval-anchor", action="store_true", help="对 anchor_derived 预检")
    ap.add_argument("--adapter-dir", default="models/r3a_structured_v3")
    ap.add_argument("--split", default="fresh", choices=["train", "valid", "fresh"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.summarize:
        files = {
            "A_pure_label": "reports/r3/ab_test_A_pure_label.json",
            "B_structured_date": "reports/r3/ab_test_B_structured_date.json",
            "C_cot": "reports/r3/ab_test_C_cot.json",
        }
        # 只打印已存在的
        have = {k: v for k, v in files.items() if Path(v).exists()}
        if not have:
            print("无已保存条件结果"); return 1
        print("=== R3A 三条件 A/B 对照（fresh 540）===")
        header = ["condition", "acc", "sup_rej", "wap", "t0t1_miss", "invalid"]
        print(f"{header[0]:22} {header[1]:>7} {header[2]:>8} {header[3]:>8} {header[4]:>10} {header[5]:>8}")
        for name, path in have.items():
            m = json.load(open(path))["metrics"]
            print(f"{name:22} {m.get('operative_status_accuracy',0):>7.4f} "
                  f"{m.get('superseded_rejection',0):>8.4f} "
                  f"{m.get('wrong_authority_preference_rate',0):>8.4f} "
                  f"{m.get('critical_t0_t1_miss',0):>10} {m.get('invalid_output',0):>8}")
        return 0

    if args.eval_anchor:
        r = eval_anchor_derived(args.adapter_dir, args.split, args.limit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
