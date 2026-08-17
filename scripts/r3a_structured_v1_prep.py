#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 R3A' 两阶段结构化 completion 数据。

只读取冻结 data/r3/train；不读 fresh，不改 Gate。模型先输出证据字段，最后
输出效力状态，便于学习 reason/authority/conflict -> status 的映射。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/r3/train"
OUT = ROOT / "data/r3/staging/r3a_structured_v1"
SEED = 42
TAIL = (
    "\n请严格按四行输出，不添加解释：\n"
    "证据理由码：<reason_code>\n"
    "权威层级：<T0-T4>\n"
    "材料冲突：<true/false>\n"
    "效力状态：<CURRENT/STALE/SUPERSEDED>"
)
REASONS = {
    "ACTIVE_CONFIG", "ACTIVE_SCHEMA", "AGENT_REPORT", "AGENT_SUMMARY",
    "ARCHIVED_BACKUP", "CONFLICT_T0_T1", "CURRENT_SOURCE",
    "DEPRECATED_SCHEMA", "EXECUTED_CODE", "EXECUTED_SCHEMA",
    "HISTORICAL_LOG", "LOW_AUTHORITY_NOTE", "NOT_APPLICABLE_TO_TASK",
    "ONLY_SURVIVING_RECORD", "PROJECT_SPEC", "README_STATEMENT",
    "RUNTIME_TRUTH", "SUPERSEDED_SIMILAR", "VERIFIER_TRUTH",
}
AUTHORITIES = {f"T{i}" for i in range(5)}


def load():
    rows = []
    for path in sorted(SRC.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def holdout(row):
    key = f"{SEED}:{row['task_group']}:{row['query_id']}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big") % 10 == 0


def make_prompt(row):
    return row["text"].rstrip() + TAIL


def make_completion(row):
    conflict = "true" if row["material_conflict"] else "false"
    return (
        f"证据理由码：{row['reason_code']}\n"
        f"权威层级：{row['authority_type']}\n"
        f"材料冲突：{conflict}\n"
        f"效力状态：{row['label']}"
    )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    rows = load()
    train, valid, seen = [], [], set()
    for row in rows:
        if row["reason_code"] not in REASONS or row["authority_type"] not in AUTHORITIES:
            raise ValueError(f"vocabulary violation: {row['query_id']}")
        prompt = make_prompt(row)
        if prompt in seen:
            raise ValueError(f"duplicate prompt: {row['query_id']}")
        seen.add(prompt)
        item = {"prompt": prompt, "completion": make_completion(row)}
        (valid if holdout(row) else train).append(item)
    train_text = {r["prompt"] for r in train}
    valid_text = {r["prompt"] for r in valid}
    if train_text & valid_text:
        raise ValueError("train/valid prompt leakage")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("train.jsonl", train), ("valid.jsonl", valid)):
        (OUT / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data), encoding="utf-8"
        )
    manifest = {
        "stage": "R3A-structured-v1",
        "source": "data/r3/train only",
        "fresh_excluded": True,
        "seed": SEED,
        "holdout": "10% deterministic per task_group/query_id",
        "completion_fields": ["reason_code", "authority_type", "material_conflict", "operative_status"],
        "status_last_line": True,
        "train_rows": len(train),
        "valid_rows": len(valid),
        "train_labels": dict(sorted(Counter(r["completion"].splitlines()[-1].split("：", 1)[1] for r in train).items())),
        "valid_labels": dict(sorted(Counter(r["completion"].splitlines()[-1].split("：", 1)[1] for r in valid).items())),
        "reason_vocab": sorted(REASONS),
        "authority_vocab": sorted(AUTHORITIES),
        "duplicate_prompts": 0,
        "train_valid_prompt_overlap": 0,
        "files": {
            "train.jsonl": {"rows": len(train), "sha256": sha(OUT / "train.jsonl")},
            "valid.jsonl": {"rows": len(valid), "sha256": sha(OUT / "valid.jsonl")},
        },
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
