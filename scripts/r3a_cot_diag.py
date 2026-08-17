#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定位 C(CoT) 的 invalid + conflict 错例根因。

对 fresh 540 全量推理一次（max_tokens=192 与 v192 报告同口径），
但额外 dump 每个样本的 raw output + 各字段解析结果，用于分类：
  截断(JSON未闭合) / 枚举外标签 / 格式污染(带尾巴) / 语义错。

只读，占 Metal。运行前确保无其他训练/评估进程。
"""
from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from r3a_v3_context import make_prompt, synth_timeline  # noqa: E402

MODEL = "models/qwen3.5-9b-mlx-4bit"

RE_REASON = re.compile(r"证据理由码\s*[:：]\s*(.+)")
RE_AUTH = re.compile(r"权威层级\s*[:：]\s*(T[0-4])", re.IGNORECASE)
RE_CONFLICT = re.compile(r"材料冲突\s*[:：]\s*(true|false)", re.IGNORECASE)
RE_STATUS = re.compile(r"效力状态\s*[:：]\s*(CURRENT|STALE|SUPERSEDED)", re.IGNORECASE)


def load_fresh():
    rows = []
    for p in sorted((ROOT / "data/r3/fresh").glob("*.jsonl")):
        rows.extend(json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip())
    return rows


def parse(out: str):
    return {
        "reason_raw": (RE_REASON.search(out).group(1).strip() if RE_REASON.search(out) else None),
        "authority": (RE_AUTH.search(out).group(1).upper() if RE_AUTH.search(out) else None),
        "conflict_raw": (RE_CONFLICT.search(out).group(1).lower() if RE_CONFLICT.search(out) else None),
        "status": (RE_STATUS.findall(out)[-1].upper() if RE_STATUS.findall(out) else None),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter-dir", default="models/r3a_cot_v4")
    ap.add_argument("--out", default="reports/r3/r3a_cot_v4_diag.jsonl")
    args = ap.parse_args()

    rows = load_fresh()
    from mlx_lm import generate, load
    model, tokenizer = load(MODEL, adapter_path=args.adapter_dir)

    t0 = time.time()
    out_fh = open(args.out, "w", encoding="utf-8")
    for i, r in enumerate(rows):
        tl = synth_timeline(r)
        prompt = make_prompt(r, tl)
        f = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        out = generate(model, tokenizer, prompt=f, max_tokens=192, verbose=False)
        p = parse(out)
        valid = all(p[k] is not None for k in ("reason_raw", "authority", "conflict_raw", "status"))
        conflict_gold = "true" if r["material_conflict"] else "false"
        rec = {
            "query_id": r["query_id"],
            "gold_label": r["label"],
            "gold_reason": r["reason_code"],
            "gold_authority": r["authority_type"],
            "gold_conflict": conflict_gold,
            "raw": out,
            "parsed": p,
            "valid": valid,
            "conflict_correct": (p["conflict_raw"] == conflict_gold),
            "reason_correct": (p["reason_raw"] == r["reason_code"]),
        }
        out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if (i + 1) % 50 == 0:
            print(f"[diag] {i+1}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    out_fh.close()
    del model

    # 汇总分类
    recs = [json.loads(l) for l in open(args.out, encoding="utf-8")]
    invalid = [r for r in recs if not r["valid"]]
    conf_bad = [r for r in recs if r["valid"] and not r["conflict_correct"]]
    reason_bad = [r for r in recs if r["valid"] and not r["reason_correct"]]
    print(f"\ninvalid={len(invalid)}, conflict_wrong={len(conf_bad)}, reason_wrong={len(reason_bad)}")
    print(f"诊断结果写到 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
