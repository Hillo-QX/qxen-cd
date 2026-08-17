#!/usr/bin/env python3
"""ctxA 数据 → mlx_lm chat 训练格式转换（T045 Phase A / P1 bug#2 修复）。

把 data/distill_ctxA/{train,valid}.jsonl（块级决策标签）转成 mlx_lm
CompletionsDataset 格式 {"prompt","completion"}，输出 data/distill_ctxA_chat/。

关键约束（P1 血泪教训）：
  - prompt = 新指令 + "\\n\\n" + source_text；source_text 已由 prepare_ctxA.py
    保证不含原 header（tests/test_ctxa_data.py 回归），因此 prompt 中指令
    恰好出现一次，绝无 P1 的"指令重复两次"污染；
  - completion = 单个标签词（PIN/DROP/KEEP/VERBATIM），与推理侧
    max_tokens=4 + 首词解析对齐（T045 §2.3）；
  - mlx_lm CompletionsDataset 内部会 apply_chat_template 包裹 prompt；
    推理侧必须用同一个 template（scripts/eval_decision.py 负责），两侧严格一致；
  - prompt+completion token 数 > max_seq_length-12 的样本会被训练静默截断
    （completion 截没 = 学不到标签），此处直接过滤并如实记录丢弃数。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

INSTRUCTION = "决策：(PIN|DROP|KEEP|VERBATIM) 只输出一个标签词。"
LABELS = ("PIN", "DROP", "KEEP", "VERBATIM")


def load_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def convert(records: list[dict], tok, max_prompt_tokens: int) -> tuple[list[dict], list[str]]:
    out: list[dict] = []
    dropped: list[str] = []
    for r in records:
        label = r["label"]
        assert label in LABELS, f"非法标签 {label}"
        prompt = f"{INSTRUCTION}\n\n{r['source_text']}"
        n_tok = len(tok.encode(prompt)) + len(tok.encode(label))
        if n_tok > max_prompt_tokens:
            dropped.append(r["block_id"])
            continue
        out.append({"prompt": prompt, "completion": label})
    return out, dropped


def main() -> int:
    ap = argparse.ArgumentParser(description="ctxA → mlx_lm chat 格式转换")
    ap.add_argument("--indir", default=os.path.join(PROJECT_ROOT, "data", "distill_ctxA"))
    ap.add_argument("--outdir", default=os.path.join(PROJECT_ROOT, "data", "distill_ctxA_chat"))
    ap.add_argument("--model", default=os.path.join(PROJECT_ROOT, "models", "qwen3.5-9b-mlx-4bit"))
    ap.add_argument("--max-seq", type=int, default=512, help="与训练 max_seq_length 对齐")
    args = ap.parse_args()

    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.model)
    budget = args.max_seq - 12  # 留 12 token 给 chat template 包装 + completion

    summary = {"instruction": INSTRUCTION, "max_seq": args.max_seq, "token_budget": budget}
    for split in ("train", "valid", "test"):
        records = load_jsonl(os.path.join(args.indir, f"{split}.jsonl"))
        out, dropped = convert(records, tok, budget)
        os.makedirs(args.outdir, exist_ok=True)
        with open(os.path.join(args.outdir, f"{split}.jsonl"), "w", encoding="utf-8") as fh:
            for rec in out:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        dist: dict[str, int] = {}
        for rec in out:
            dist[rec["completion"]] = dist.get(rec["completion"], 0) + 1
        summary[split] = {"in": len(records), "out": len(out), "dropped_too_long": dropped,
                          "label_distribution": dist}

    with open(os.path.join(args.outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
