#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 R3A-hard v1 派生数据。

原则：只读取冻结的 data/r3/train 与 data/r3/valid，不读取 fresh；不覆盖既有
staging 数据。训练集使用全部弱类样本，并用不同的通用指令变体补齐到三类各
720 条，避免 v2 的完全相同 prompt 重复。completion 增加一行通用判定依据，
Gate 仍只解析状态词，因此不改变评估协议。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/r3/staging/r3a_hard_v1"
SEED = 42
TAIL = "只输出一行：\n效力状态：CURRENT/STALE/SUPERSEDED"
INSTRUCTION = re.compile(
    r"^(输出|请评估|请判定|判断|评估|给出|请输出).*(状态|有效|效力|权威|候选).*[。？?]?$"
)
VARIANTS = (
    "判定目标：先识别候选在本任务下的效力状态。",
    "判定要求：依据任务、规则、来源和竞争材料判断效力。",
    "分析顺序：先区分当前有效、暂不适用与已被取代。",
)
RATIONALE = {
    "CURRENT": "候选在本任务下仍具操作效力，且未被后续材料取代。",
    "STALE": "候选当前不适用于本任务或仅作历史参考，未确认被后续材料直接取代。",
    "SUPERSEDED": "候选已被后续版本、更新结果或生效来源取代，不应继续作为当前依据。",
}


def load(split: str):
    rows = []
    for p in sorted((ROOT / f"data/r3/{split}").glob("*.jsonl")):
        rows.extend(json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def body(text: str) -> str:
    lines = text.rstrip().splitlines()
    if lines and INSTRUCTION.match(lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).rstrip()


def variant_index(query_id: str, salt: int = 0) -> int:
    h = hashlib.sha256(f"{query_id}:{salt}".encode()).digest()
    return int.from_bytes(h[:2], "big") % len(VARIANTS)


def prompt(row, salt: int = 0) -> str:
    return f"{VARIANTS[variant_index(row['query_id'], salt)]}\n{body(row['text'])}\n{TAIL}"


def stratified_pick(rows, n: int):
    """按 task_group 近似等比例抽样，确保每类保留全部任务族。"""
    rng = random.Random(SEED)
    groups = defaultdict(list)
    for row in rows:
        groups[row["task_group"]].append(row)
    names = sorted(groups)
    base = n // len(names)
    extra = n % len(names)
    chosen = []
    for i, name in enumerate(names):
        k = base + (1 if i < extra else 0)
        pool = list(groups[name])
        rng.shuffle(pool)
        chosen.extend(pool[:k])
    return chosen


def make_train(rows):
    by = defaultdict(list)
    for row in rows:
        by[row["label"]].append(row)
    target = 720
    selected = {"CURRENT": stratified_pick(by["CURRENT"], target),
                "STALE": list(by["STALE"]),
                "SUPERSEDED": list(by["SUPERSEDED"])}
    # 弱类只增加通用指令变体；不复制完全相同的 prompt。
    rng = random.Random(SEED)
    for label in ("STALE", "SUPERSEDED"):
        need = target - len(selected[label])
        pool = by[label]
        for i in range(need):
            row = dict(pool[(i * 37 + 11) % len(pool)])
            row["_salt"] = i + 1
            selected[label].append(row)
    out = []
    for label in ("CURRENT", "STALE", "SUPERSEDED"):
        for row in selected[label]:
            salt = row.get("_salt", 0)
            out.append({
                "prompt": prompt(row, salt),
                "completion": f"状态：{label}\n判定依据：{RATIONALE[label]}",
                "meta": {"query_id": row["query_id"], "label": label,
                         "reason_code": row["reason_code"], "augmented": bool(salt)},
            })
    rng.shuffle(out)
    return out


def make_valid(rows):
    return [{"prompt": prompt(row),
             "completion": f"状态：{row['label']}\n判定依据：{RATIONALE[row['label']]}",
             "meta": {"query_id": row["query_id"], "label": row["label"],
                      "reason_code": row["reason_code"], "augmented": False}}
            for row in rows]


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({"prompt": row["prompt"], "completion": row["completion"]},
                               ensure_ascii=False) + "\n")


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    train, valid = load("train"), load("valid")
    train_out, valid_out = make_train(train), make_valid(valid)
    write_jsonl(OUT / "train.jsonl", train_out)
    write_jsonl(OUT / "valid.jsonl", valid_out)
    manifest = {
        "stage": "R3A-hard-v1", "seed": SEED,
        "source": "data/r3/train + data/r3/valid (fresh excluded)",
        "transform": "canonical gate-aligned prompt + structured status/reason completion",
        "train_rows": len(train_out), "valid_rows": len(valid_out),
        "train_labels": dict(Counter(r["completion"].split("\n", 1)[0].removeprefix("状态：") for r in train_out)),
        "train_unique_prompts": len({r["prompt"] for r in train_out}),
        "train_augmented_rows": sum(r["meta"]["augmented"] for r in train_out),
        "files": {name: {"rows": len(rows), "sha256": sha(OUT / name)}
                  for name, rows in (("train.jsonl", train_out), ("valid.jsonl", valid_out))},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
