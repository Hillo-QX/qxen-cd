#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从冻结 R3A train 派生 R3A-hard v2。

v2 只使用冻结 train：不读 fresh/valid，不过采样，不重复 prompt；按任务族
确定性留出内部 valid，Gate 仍使用独立 fresh 集。输出为纯状态 completion。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/r3/train"
OUT = ROOT / "data/r3/staging/r3a_hard_v2"
SEED = 42
HOLDOUT_DEN = 10
TAIL = "只输出一行：\n效力状态：CURRENT/STALE/SUPERSEDED"


def load_rows():
    rows = []
    for path in sorted(SRC.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def prompt(text: str) -> str:
    text = text.rstrip()
    if not text.endswith(TAIL):
        text = f"{text}\n{TAIL}"
    return text


def is_holdout(row: dict) -> bool:
    key = f"{SEED}:{row['task_group']}:{row['query_id']}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big") % HOLDOUT_DEN == 0


def convert(rows):
    train, valid = [], []
    seen = set()
    for row in rows:
        p = prompt(row["text"])
        if p in seen:
            raise RuntimeError(f"duplicate prompt in frozen train: {row['query_id']}")
        seen.add(p)
        item = {"prompt": p, "completion": row["label"]}
        (valid if is_holdout(row) else train).append(item)
    return train, valid


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(rows):
    return {
        "rows": len(rows),
        "labels": dict(sorted(Counter(r["completion"] for r in rows).items())),
    }


def main():
    rows = load_rows()
    train, valid = convert(rows)
    if not train or not valid:
        raise RuntimeError("deterministic holdout produced an empty split")
    train_prompts = {r["prompt"] for r in train}
    valid_prompts = {r["prompt"] for r in valid}
    if train_prompts & valid_prompts:
        raise RuntimeError("train/valid prompt leakage")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("train.jsonl", train), ("valid.jsonl", valid)):
        (OUT / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data), encoding="utf-8"
        )
    manifest = {
        "stage": "R3A-hard-v2",
        "source": "data/r3/train only",
        "seed": SEED,
        "holdout": "10% deterministic per task_group/query_id",
        "fresh_excluded": True,
        "oversampling": False,
        "duplicate_prompts": 0,
        "train": stats(train),
        "valid": stats(valid),
        "train_valid_prompt_overlap": 0,
        "source_rows": len(rows),
        "files": {
            "train.jsonl": {"rows": len(train), "sha256": sha(OUT / "train.jsonl")},
            "valid.jsonl": {"rows": len(valid), "sha256": sha(OUT / "valid.jsonl")},
        },
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
