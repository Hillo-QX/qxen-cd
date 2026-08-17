#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 R3A''' structured v3 数据：注入合成 as_of 时间锚点 + 权威源链。

VERDICT A 落地（用户已授权）：
  在冻结源 data/r3/train/*.jsonl 之上派生 v3 数据资产，注入合成具体时间锚点
  （as_of）+ 该锚点时刻的权威源链，使 operative_status 从 prompt 唯一可推断。

合成逻辑（as_of 时间线 + 权威源链 + prompt 构造）统一放在 scripts/r3a_v3_context.py，
训练数据准备与 Gate eval 共用，保证两侧 prompt 一致。

约束（严格遵守）：
  - 只读 data/r3/train/*.jsonl，绝不写入或覆盖冻结源。
  - 输出到 data/r3/staging/r3a_structured_v3/，可重复运行、幂等。
  - 不加载模型、不占用 Metal、不启动训练/评估。
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from r3a_v3_context import (  # noqa: E402
    REASONS, completion, make_prompt, synth_timeline, as_of_phase,
)

SRC = ROOT / "data/r3/train"
OUT = ROOT / "data/r3/staging/r3a_structured_v3"
SEED = 42


def load():
    rows = []
    for path in sorted(SRC.glob("*.jsonl")):
        rows.extend(json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
    return rows


def holdout(row):
    key = f"{SEED}:{row['task_group']}:{row['query_id']}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big") % 10 == 0


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_determinacy(rows, timelines):
    """程序化校验可判定率：判定信号 (has_superseder, as_of_phase) -> label 唯一。"""
    mapping = {}
    conflicts = []
    for r, tl in zip(rows, timelines):
        key = (tl["has_superseder"], as_of_phase(tl))
        if key not in mapping:
            mapping[key] = r["label"]
        elif mapping[key] != r["label"]:
            conflicts.append((key, mapping[key], r["label"], r["query_id"]))
    n = len(rows)
    rate = round(1 - len(conflicts) / n, 4) if n else 0.0
    return rate, conflicts, mapping


def main():
    rows = load()
    if not rows:
        raise SystemExit("冻结源无数据")
    src_sha = {p.name: sha(p) for p in sorted(SRC.glob("*.jsonl"))}

    train, valid, seen = [], [], set()
    timelines = []
    for row in rows:
        if row["reason_code"] not in REASONS:
            raise ValueError(f"reason vocabulary violation: {row['query_id']}")
        tl = synth_timeline(row)
        timelines.append(tl)
        prompt = make_prompt(row, tl)
        if prompt in seen:
            raise ValueError(f"duplicate prompt: {row['query_id']}")
        seen.add(prompt)
        item = {"prompt": prompt, "completion": completion(row)}
        (valid if holdout(row) else train).append(item)

    rate, conflicts, mapping = check_determinacy(rows, timelines)

    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("train.jsonl", train), ("valid.jsonl", valid)):
        (OUT / name).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in data), encoding="utf-8")

    manifest = {
        "stage": "R3A-structured-v3-asof",
        "source": "data/r3/train only (frozen, read-only)",
        "fresh_excluded": True,
        "seed": SEED,
        "verdict": "VERDICT A - 合成 as_of 时间锚点 + 权威源链",
        "authorization": "用户已授权 A：派生 v3 数据资产，注入合成 as_of，不改冻结源",
        "shared_context_module": "scripts/r3a_v3_context.py",
        "completion_fields": ["reason_code", "authority_type", "material_conflict", "decision_point", "operative_status"],
        "status_last_line": True,
        "determinacy": {
            "rate": rate,
            "threshold": 0.95,
            "pass": rate >= 0.95,
            "conflicts": len(conflicts),
            "signal_mapping": {f"{k[0]}/{k[1]}": v for k, v in mapping.items()},
        },
        "train_rows": len(train), "valid_rows": len(valid),
        "train_labels": dict(sorted(Counter(x["completion"].splitlines()[-1].split("：", 1)[1] for x in train).items())),
        "valid_labels": dict(sorted(Counter(x["completion"].splitlines()[-1].split("：", 1)[1] for x in valid).items())),
        "source_files_sha256": src_sha,
        "files": {name: {"rows": len(data), "sha256": sha(OUT / name)} for name, data in (("train.jsonl", train), ("valid.jsonl", valid))},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "stage": manifest["stage"],
        "determinacy_rate": rate,
        "determinacy_pass": rate >= 0.95,
        "conflicts": len(conflicts),
        "train_rows": len(train), "valid_rows": len(valid),
        "frozen_source_files": len(src_sha),
        "output": str(OUT),
    }, ensure_ascii=False, indent=2))

    if rate < 0.95:
        raise SystemExit(f"可判定率 {rate} < 0.95，未达标")


if __name__ == "__main__":
    main()
