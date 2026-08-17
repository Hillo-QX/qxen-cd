#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T046 — 将 T045 的 108 条 Context Decision 样本转换为 LoRA 训练 chat 格式。

输入: outputs/context_decision_dataset/context_decision_all.jsonl（T045 产物）
输出: outputs/context_decision_training/
  - train.jsonl            mlx_lm CompletionsDataset 兼容格式 {"prompt","completion"}
                           （mlx_lm 内部会 apply_chat_template 包裹 prompt，与推理侧一致）
  - train_messages.jsonl   显式 system/user/assistant 消息视图（验收用）
  - manifest.json          转换规则 / 版本 / provenance / 统计
  - README.md              说明

设计要点（对齐 T044/T045 计划 §2.3 + P1 bug 教训）：
  - completion 为单个标签词（7 类合法值），与推理侧 max_tokens=4 + 首词解析对齐；
  - prompt = 指令 + input_context；指令只在 prompt 中出现一次，绝无重复污染；
  - 用 tokenizer 实测 token 数，超预算样本如实丢弃并记录（防静默截断）；
  - 不修改任何原始数据集文件。
"""
from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

VALID_DECISIONS = ("PIN", "KEEP", "VERBATIM", "COMPRESS", "DROP", "REFRESH", "RETRIEVE")
INSTRUCTION = "决策：(PIN|KEEP|VERBATIM|COMPRESS|DROP|REFRESH|RETRIEVE) 只输出一个标签词。"
DATASET_VERSION = "ctxA-v1"

IN_DIR = os.path.join(PROJECT_ROOT, "outputs", "context_decision_dataset")
OUT_DIR = os.path.join(PROJECT_ROOT, "outputs", "context_decision_training")
MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "qwen3.5-9b-mlx-4bit")


def load_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def main() -> int:
    src = load_jsonl(os.path.join(IN_DIR, "context_decision_all.jsonl"))
    print(f"输入样本数: {len(src)}")

    # 加载 tokenizer（用真实模型 tokenizer 做长度预算，与训练侧一致）
    try:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(MODEL_DIR)
        have_tok = True
    except Exception as e:  # pragma: no cover
        print(f"[warn] tokenizer 加载失败，跳过长度过滤: {e}")
        tok, have_tok = None, False

    MAX_SEQ = 512          # 与训练 max_seq_length 对齐
    BUDGET = MAX_SEQ - 12  # 预留 chat template 包装 + completion 空间

    chat_rows: list[dict] = []
    msg_rows: list[dict] = []
    dropped: list[dict] = []
    label_dist: dict[str, int] = {}

    for r in src:
        rid = r["id"]
        decision = r["decision"]
        if decision not in VALID_DECISIONS:
            dropped.append({"id": rid, "reason": f"非法标签 {decision}"})
            continue
        prompt = f"{INSTRUCTION}\n\n{r['input_context']}"
        if have_tok:
            n_tok = len(tok.encode(prompt)) + len(tok.encode(decision))
            if n_tok > BUDGET:
                dropped.append({"id": rid, "reason": f"超长 {n_tok}>{BUDGET}"})
                continue
        chat_rows.append({"prompt": prompt, "completion": decision})
        msg_rows.append({
            "id": rid,
            "messages": [
                {"role": "system", "content": "你是 QXEN Context Decision 分类器，对给定任务与内容块输出唯一决策标签。"},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": decision},
            ],
        })
        label_dist[decision] = label_dist.get(decision, 0) + 1

    os.makedirs(OUT_DIR, exist_ok=True)

    with open(os.path.join(OUT_DIR, "train.jsonl"), "w", encoding="utf-8") as fh:
        for rec in chat_rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with open(os.path.join(OUT_DIR, "train_messages.jsonl"), "w", encoding="utf-8") as fh:
        for rec in msg_rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    manifest = {
        "dataset_version": DATASET_VERSION,
        "source": "outputs/context_decision_dataset/context_decision_all.jsonl",
        "converter": "scripts/prepare_ctxA_t045.py",
        "converted_at": "2026-08-13",
        "instruction": INSTRUCTION,
        "max_seq": MAX_SEQ,
        "token_budget": BUDGET,
        "input_count": len(src),
        "output_count": len(chat_rows),
        "dropped": dropped,
        "label_distribution": label_dist,
        "format": "mlx_lm CompletionsDataset: {prompt, completion}; 推理侧用同一 chat template + max_tokens=4 首词解析",
        "provenance": {
            "run_id": "T046",
            "teacher": "rule-based-anchor",
            "teacher_version": "v1",
            "verified": False,
            "training_allowed": True,
        },
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "input": len(src), "output": len(chat_rows), "dropped": dropped,
        "label_distribution": label_dist,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
