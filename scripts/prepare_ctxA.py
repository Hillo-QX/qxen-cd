#!/usr/bin/env python3
"""Phase A (ctxA) 数据准备：块级 PIN/DROP/KEEP/VERBATIM 决策标签（T045）。

取代 T044 Phase 1 的 keep/drop/stale/exact 标签体系（已弃用）。
核心区别：模型只做"决策分类"，completion = 单个标签词，不再生成保留文本。

标签体系（T045 §2.1，4 类互斥）：
  PIN      决策锚点：用户指令/硬约束/权威规则（约束关键词行）
  VERBATIM 精确值：路径/hash/版本号/文件扩展名行，逐字保留
  DROP     过期/已解决/冗余日志/语义相似/错误信息（干扰标记块）
  KEEP     当前相关状态信息（其余）
冲突优先级：PIN > VERBATIM > DROP > KEEP（DROP 由干扰标记锚定，天然独立）。

修复 P1 四 bug（T045 §2.2）：
  1. source_text 不含原 header —— 首行指令块不输出为训练记录；
  2. 训练/推理统一 chat 格式 —— 指令文本写入 manifest，由训练脚本统一拼装，
     数据层不内嵌任何指令（杜绝"指令重复两次"污染）；
  3. train/valid 按 group（src-NNN）整组划分 —— 同一 base 样本的正样本块与
     其 3 个干扰变体块不跨边，杜绝 source_text 重复造成的评估退化；
  4. 评估对照 base 由 scripts/eval_decision.py 负责，本脚本只保证数据无泄漏。

输入：
  data/eval_set/train.jsonl        92 条正样本（行号 NNN ↔ phase0 sample_id src-NNN，已实测验证）
  data/distill_phase0/train.jsonl  276 条干扰源（92 redundant + 92 stale + 92 semantic）
输出：
  data/distill_ctxA/train.jsonl / valid.jsonl / manifest.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.prepare_phase1 import split_blocks  # 复用切块（含标记行优先切分修复）
from src.distiller.noise_generator import CONSTRAINT_KEYWORDS

LABELS = ("PIN", "DROP", "KEEP", "VERBATIM")

# 训练/推理统一指令（T045 §2.3）：训练脚本拼装 chat 时使用，数据层不内嵌。
INSTRUCTION = "决策：(PIN|DROP|KEEP|VERBATIM) 只输出一个标签词。"

# VERBATIM 判定锚点（T045 §2.1：路径式 token / 哈希 / 版本号 / 文件扩展名行）
_RE_PATH = re.compile(r"(?<![\w])(?:/[\w.\-]+){2,}/?|~/[\w.\-/]+")           # 绝对/家目录路径
_RE_FILE = re.compile(r"\b[\w.\-/]+\.(?:py|md|jsonl?|ya?ml|toml|sh|txt|log|pkl|bak)\b")
_RE_VERSION = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")                        # 版本号
_RE_HASH = re.compile(r"\b[0-9a-f]{7,40}\b")                                 # hash/短 sha


def label_block(text: str, kind: str) -> str:
    """4 标签判定。优先级 PIN > VERBATIM > DROP > KEEP。

    noise 块（含干扰标记）直接 DROP —— 标记由 noise_generator 注入，客观可验。
    fence/normal 块按行扫描：任一行含硬约束关键词 → PIN；
    否则任一行命中路径/文件/版本/hash → VERBATIM；否则 KEEP。
    """
    if kind == "noise":
        return "DROP"
    has_verbatim = False
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in CONSTRAINT_KEYWORDS):
            return "PIN"
        if (
            _RE_PATH.search(line)
            or _RE_FILE.search(line)
            or _RE_VERSION.search(line)
            or _RE_HASH.search(line)
        ):
            has_verbatim = True
    return "VERBATIM" if has_verbatim else "KEEP"


def iter_records() -> list[dict]:
    """切分 92 正样本 + 276 干扰源，输出块级记录（跳过 header 块）。"""
    out: list[dict] = []
    with open(os.path.join(PROJECT_ROOT, "data", "eval_set", "train.jsonl"), encoding="utf-8") as fh:
        positives = [json.loads(ln) for ln in fh if ln.strip()]
    with open(os.path.join(PROJECT_ROOT, "data", "distill_phase0", "train.jsonl"), encoding="utf-8") as fh:
        noisy = [json.loads(ln) for ln in fh if ln.strip()]

    # 正样本：group = src-NNN（与 phase0 sample_id 对齐，映射已实测验证）
    contexts: list[tuple[str, str, str]] = []  # (context_id, group_id, prompt)
    for i, rec in enumerate(positives):
        gid = f"src-{i:03d}"
        contexts.append((f"pos-{i:03d}", gid, rec["prompt"]))
    for rec in noisy:
        gid = rec["sample_id"]
        contexts.append((rec["id"], gid, rec["prompt"]))

    for cid, gid, prompt in contexts:
        k = 0
        for text, kind in split_blocks(prompt):
            if kind == "header":
                continue  # bug#1 修复：指令行不作为训练块
            label = label_block(text, kind)
            out.append({
                "context_id": cid,
                "group_id": gid,
                "block_id": f"{cid}-{k:02d}",
                "label": label,
                "source_text": text,
            })
            k += 1
    return out


def split_train_valid_test(
    records: list[dict], valid_groups: int, test_groups: int, seed: int
) -> tuple[list[dict], list[dict], list[dict], list[str], list[str]]:
    """按 group 整组划分三份（bug#3 修复：杜绝同一 base 的块跨 train/valid/test）。

    test 与 valid 从全部 group 中互斥抽取（seed 固定，可复现）；train = 其余。
    """
    groups = sorted({r["group_id"] for r in records})
    assert valid_groups + test_groups <= len(groups), "valid+test 组数超过总组数"
    rng = random.Random(seed)
    picked = rng.sample(groups, valid_groups + test_groups)
    vg = set(picked[:valid_groups])
    tg = set(picked[valid_groups:])
    train = [r for r in records if r["group_id"] not in vg and r["group_id"] not in tg]
    valid = [r for r in records if r["group_id"] in vg]
    test = [r for r in records if r["group_id"] in tg]
    return train, valid, test, sorted(vg), sorted(tg)


def dist(records: list[dict]) -> dict[str, int]:
    d: dict[str, int] = {}
    for r in records:
        d[r["label"]] = d.get(r["label"], 0) + 1
    return d


def write_jsonl(path: str, records: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="ctxA 块级决策标签数据准备（T045 Phase A）")
    ap.add_argument("--outdir", default=os.path.join(PROJECT_ROOT, "data", "distill_ctxA"))
    ap.add_argument("--valid-groups", type=int, default=10, help="valid 组数（92 组中抽取，约 11%%）")
    ap.add_argument("--test-groups", type=int, default=10, help="test 组数（92 组中抽取，约 11%%）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    records = iter_records()
    train, valid, test, vgroups, tgroups = split_train_valid_test(
        records, args.valid_groups, args.test_groups, args.seed
    )

    write_jsonl(os.path.join(args.outdir, "train.jsonl"), train)
    write_jsonl(os.path.join(args.outdir, "valid.jsonl"), valid)
    write_jsonl(os.path.join(args.outdir, "test.jsonl"), test)
    manifest = {
        "plan": "T045 Phase A Context Decision Training",
        "labels": list(LABELS),
        "instruction": INSTRUCTION,
        "priority": "PIN > VERBATIM > DROP > KEEP",
        "seed": args.seed,
        "valid_groups": vgroups,
        "test_groups": tgroups,
        "n_records": len(records),
        "n_train": len(train),
        "n_valid": len(valid),
        "n_test": len(test),
        "train_label_distribution": dist(train),
        "valid_label_distribution": dist(valid),
        "test_label_distribution": dist(test),
        "p1_bugfixes": [
            "source_text 不含原 header（指令行不输出为训练块）",
            "指令文本仅在 manifest，训练脚本统一拼装 chat 格式",
            "train/valid/test 按 group 整组划分，同 base 块不跨边",
            "评估对照 base 见 scripts/eval_decision.py",
        ],
    }
    with open(os.path.join(args.outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
