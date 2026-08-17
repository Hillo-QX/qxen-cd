#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 R3A fresh-like 校准训练集。

只读取冻结 train 与 fresh 的标签计数；不读取 fresh 文本、不修改 fresh，
按 task_group 内 fresh 标签比例从 train 无放回确定性抽样，使用新版本目录。
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "data/r3/train"
FRESH = ROOT / "data/r3/fresh"
OUT = ROOT / "data/r3/staging/r3a_calib_v1"
SEED = 42
SCALE = 3
TAIL = "只输出一行：\n效力状态：CURRENT/STALE/SUPERSEDED"


def load(root):
    rows = []
    for path in sorted(root.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    train = load(TRAIN)
    fresh = load(FRESH)
    source = defaultdict(list)
    target = defaultdict(Counter)
    for row in train:
        source[row["task_group"]].append(row)
    for row in fresh:
        target[row["task_group"]][row["label"]] += 1

    rng = random.Random(SEED)
    out = []
    seen = set()
    chosen_counts = defaultdict(Counter)
    for group in sorted(target):
        pools = defaultdict(list)
        for row in source[group]:
            pools[row["label"]].append(row)
        for label, fresh_n in sorted(target[group].items()):
            want = fresh_n * SCALE
            # 不复制 prompt：极少数族的目标略高于冻结 train 时封顶，
            # manifest 保留实际值，避免引入重复样本或改动冻结源。
            want = min(want, len(pools[label]))
            candidates = list(pools[label])
            rng.shuffle(candidates)
            for row in candidates[:want]:
                prompt = row["text"].rstrip()
                if not prompt.endswith(TAIL):
                    prompt += "\n" + TAIL
                if prompt in seen:
                    raise RuntimeError(f"duplicate prompt: {row['query_id']}")
                seen.add(prompt)
                out.append({"prompt": prompt, "completion": label})
                chosen_counts[group][label] += 1
    rng.shuffle(out)
    OUT.mkdir(parents=True, exist_ok=True)
    train_path = OUT / "train.jsonl"
    train_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    manifest = {
        "stage": "R3A-calib-v1",
        "source": "data/r3/train only",
        "fresh_counts_only": True,
        "fresh_text_used": False,
        "gate_protocol_unchanged": True,
        "seed": SEED,
        "scale_vs_fresh": SCALE,
        "rows": len(out),
        "duplicate_prompts": 0,
        "fresh_targets": {g: dict(sorted(c.items())) for g, c in sorted(target.items())},
        "chosen_counts": {g: dict(sorted(c.items())) for g, c in sorted(chosen_counts.items())},
        "source_counts": {g: dict(sorted(Counter(r["label"] for r in source[g]).items())) for g in sorted(source)},
        "file": {"rows": len(out), "sha256": sha(train_path)},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
