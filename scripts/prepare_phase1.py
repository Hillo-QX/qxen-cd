#!/usr/bin/env python3
"""Phase 1 数据准备：块级 keep/drop/stale/exact 标签（T044 Phase 1 / 新会话 T001）。

读取 data/distill_phase0/train.jsonl（276 条含干扰源），将每个 prompt 切成块并标注：
  - keep  保留          ：原文档内容块（非精确、非噪声）
  - drop  丢弃          ：注入的冗余日志 / 语义相似无关 / 错误信息 块
  - stale 过期          ：注入的过期版本块
  - exact 精确保留      ：代码围栏块 / 硬约束行

输出 data/distill_phase1/train.jsonl —— 块级记录，条数 = 实际块数（>276），
每条含 context_id / block_id / label(标量四选一) / source_text / target_text。
target_text：keep/exact 时 = 源块文本（保留），drop/stale 时 = ""（丢弃）。

【格式冲突说明】Dispatcher 验收模板要求恰好 276 条扁平记录，但 Phase 1 训练目标要求
"每块独立决策"，块级标注自然产生多条记录/上下文。经 request_decision 确认采用
块级记录（选项 A）并如实报告条数。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.distiller.noise_generator import (
    CONSTRAINT_KEYWORDS,
    MARKER_FALSE,
    MARKER_REDUNDANT,
    MARKER_SEMANTIC,
    MARKER_STALE,
)

MARKERS = (MARKER_REDUNDANT, MARKER_STALE, MARKER_SEMANTIC, MARKER_FALSE)


def split_blocks(prompt: str) -> list[tuple[str, str]]:
    """把 prompt 切成块，返回 [(block_text, kind)]，kind in header/fence/noise/normal。

    规则：
      - 干扰标记行（含 `[干扰注入:...]`）优先切分 —— 无论其处于普通文本还是代码围栏内；
      - 冗余日志块 = 标记 + 连续 `[INFO]` 行（及夹缝空行）；
      - 其余噪声块 = 标记 + 恰好 1 行内容（Phase 0 生成时 strength=1，模板为单行段落）；
      - 代码围栏（```...```）为独立块；标题行开新块；其余连续非空行归为普通块。
    """
    lines = prompt.split("\n")
    blocks: list[tuple[str, str]] = []
    i = 0
    n = len(lines)
    if lines:
        blocks.append((lines[0], "header"))
        i = 1

    buf: list[str] = []          # 待刷出的普通/围栏块
    buf_kind: str | None = None

    def flush() -> None:
        nonlocal buf, buf_kind
        if buf:
            blocks.append(("\n".join(buf), buf_kind or "normal"))
        buf, buf_kind = [], None

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue

        marker = next((mk for mk in MARKERS if stripped.startswith(mk)), None)
        if marker:
            flush()
            j = i + 1
            if marker == MARKER_REDUNDANT:
                while j < n and (lines[j].startswith("[INFO]") or not lines[j].strip()):
                    j += 1
            else:
                # strength=1：非冗余噪声 = 标记 + 恰好 1 行内容
                if j < n and lines[j].strip():
                    j += 1
            blocks.append(("\n".join(lines[i:j]), "noise"))
            i = j
            continue

        if stripped.startswith("```"):
            if buf_kind == "fence":
                buf.append(line)  # 闭合围栏
                flush()
            else:
                flush()
                buf, buf_kind = [line], "fence"
            i += 1
            continue

        if buf_kind == "fence":
            buf.append(line)  # 围栏内：追加原行；标记行已在上方优先切分
            i += 1
            continue

        # 普通文本
        if stripped.startswith("#"):
            flush()
            buf, buf_kind = [line], "normal"
        elif buf_kind is None:
            buf, buf_kind = [line], "normal"
        else:
            buf.append(line)
        i += 1
    flush()
    return blocks


def label_block(text: str, kind: str) -> str:
    if kind == "header":
        return "keep"
    if kind == "noise":
        return "stale" if MARKER_STALE in text else "drop"
    if kind == "fence":
        return "exact"
    # normal：含硬约束行 → exact，否则 keep
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in CONSTRAINT_KEYWORDS):
            return "exact"
    return "keep"


def prepare(input_path: str, output_path: str) -> dict:
    with open(input_path, "r", encoding="utf-8") as fh:
        contexts = [json.loads(ln) for ln in fh if ln.strip()]

    out: list[dict] = []
    dist: dict[str, int] = {}
    per_ctx_blocks: dict[str, int] = {}
    for ctx in contexts:
        cid = ctx.get("id", "")
        blocks = split_blocks(ctx.get("prompt", ""))
        per_ctx_blocks[cid] = len(blocks)
        for k, (text, kind) in enumerate(blocks):
            label = label_block(text, kind)
            dist[label] = dist.get(label, 0) + 1
            target = text if label in ("keep", "exact") else ""
            out.append({
                "context_id": cid,
                "block_id": f"{cid}-{k:02d}",
                "label": label,
                "source_text": text,
                "target_text": target,
            })

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        for rec in out:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return {
        "n_contexts": len(contexts),
        "n_records": len(out),
        "label_distribution": dist,
        "blocks_per_context_min": min(per_ctx_blocks.values()),
        "blocks_per_context_max": max(per_ctx_blocks.values()),
        "output": output_path,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1 块级标签数据准备")
    ap.add_argument("--input", default=os.path.join(PROJECT_ROOT, "data", "distill_phase0", "train.jsonl"))
    ap.add_argument("--output", default=os.path.join(PROJECT_ROOT, "data", "distill_phase1", "train.jsonl"))
    args = ap.parse_args()
    summary = prepare(args.input, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
