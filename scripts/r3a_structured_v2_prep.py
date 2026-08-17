#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 R3A'' hard structured 数据：不读取 fresh，不覆盖 v1。

仅使用 data/r3/train 与同一确定性 holdout；对 STALE/SUPERSEDED 增加
唯一的边界对照 prompt，强化“未被取代”与“已被取代”的区分。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/r3/train"
OUT = ROOT / "data/r3/staging/r3a_structured_v2"
SEED = 42
TAIL = (
    "\n请严格按五行输出，不添加解释：\n"
    "证据理由码：<reason_code>\n"
    "权威层级：<T0-T4>\n"
    "材料冲突：<true/false>\n"
    "判定要点：<一句话说明是当前有效、仅不适用/历史参考，还是已被后续版本或实现取代>\n"
    "效力状态：<CURRENT/STALE/SUPERSEDED>"
)
BOUNDARY = (
    "\n状态边界提示：CURRENT=当前任务下仍有效；STALE=当前任务暂不适用或仅作历史参考，"
    "但没有证据表明已被后续版本取代；SUPERSEDED=已明确被后续版本、当前实现或新权威来源取代。"
)
REASONS = {
    "ACTIVE_CONFIG", "ACTIVE_SCHEMA", "AGENT_REPORT", "AGENT_SUMMARY",
    "ARCHIVED_BACKUP", "CONFLICT_T0_T1", "CURRENT_SOURCE",
    "DEPRECATED_SCHEMA", "EXECUTED_CODE", "EXECUTED_SCHEMA",
    "HISTORICAL_LOG", "LOW_AUTHORITY_NOTE", "NOT_APPLICABLE_TO_TASK",
    "ONLY_SURVIVING_RECORD", "PROJECT_SPEC", "README_STATEMENT",
    "RUNTIME_TRUTH", "SUPERSEDED_SIMILAR", "VERIFIER_TRUTH",
}


def load():
    rows = []
    for path in sorted(SRC.glob("*.jsonl")):
        rows.extend(json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
    return rows


def holdout(row):
    key = f"{SEED}:{row['task_group']}:{row['query_id']}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big") % 10 == 0


def completion(row):
    conflict = "true" if row["material_conflict"] else "false"
    label = row["label"]
    if label == "SUPERSEDED":
        point = "已被后续版本、当前实现或新权威来源取代"
    elif label == "STALE":
        point = "当前任务暂不适用或仅作历史参考，但未显示被后续版本取代"
    else:
        point = "当前任务下仍有效，且没有更高权威来源取代它"
    return (f"证据理由码：{row['reason_code']}\n"
            f"权威层级：{row['authority_type']}\n"
            f"材料冲突：{conflict}\n"
            f"判定要点：{point}\n"
            f"效力状态：{label}")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    rows = load()
    train, valid, seen = [], [], set()
    for row in rows:
        if row["reason_code"] not in REASONS:
            raise ValueError(f"reason vocabulary violation: {row['query_id']}")
        base = row["text"].rstrip() + TAIL
        items = [(base, "base")]
        if row["label"] in {"STALE", "SUPERSEDED"}:
            items.append((row["text"].rstrip() + BOUNDARY + TAIL, "boundary"))
        bucket = valid if holdout(row) else train
        for prompt, variant in items:
            if prompt in seen:
                raise ValueError(f"duplicate prompt: {row['query_id']}:{variant}")
            seen.add(prompt)
            bucket.append({"prompt": prompt, "completion": completion(row)})
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("train.jsonl", train), ("valid.jsonl", valid)):
        (OUT / name).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in data), encoding="utf-8")
    manifest = {
        "stage": "R3A-structured-v2-hard",
        "source": "data/r3/train only",
        "fresh_excluded": True,
        "seed": SEED,
        "boundary_variants": "one extra unique prompt for STALE/SUPERSEDED",
        "completion_fields": ["reason_code", "authority_type", "material_conflict", "decision_point", "operative_status"],
        "status_last_line": True,
        "train_rows": len(train), "valid_rows": len(valid),
        "train_labels": dict(sorted(Counter(x["completion"].splitlines()[-1].split("：", 1)[1] for x in train).items())),
        "valid_labels": dict(sorted(Counter(x["completion"].splitlines()[-1].split("：", 1)[1] for x in valid).items())),
        "duplicate_prompts": 0, "train_valid_prompt_overlap": 0,
        "files": {name: {"rows": len(data), "sha256": sha(OUT / name)} for name, data in (("train.jsonl", train), ("valid.jsonl", valid))},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
