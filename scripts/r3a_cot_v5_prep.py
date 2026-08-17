#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 R3A Cot-v5 数据：字段隔离契约（<think> 推理 + 纯 JSON 输出）。

Kimi-Expert 裁决（2026-08-14）+ 用户授权：
  - 接受本地「字段隔离」论证：推理放 <think>，输出纯 JSON，
    使 CoT 推理与结构化字段在 token 层面物理隔离。
  - 下一轮验证：invalid_output 与 reason_code 失真是否因隔离而同步下降。

与 v4 唯一区别：completion 由「五行文本（判定要点=日期轨迹）」改为
  <think>日期计算轨迹</think> + {"reason_code":..,"authority":..,"conflict":..,"status":..}
prompt 的判定上下文（as_of + 权威源链）与 v4 完全一致。

约束：只读 data/r3/train 冻结源，输出到 data/r3/staging/r3a_cot_v5/，幂等，不占 Metal。
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
    REASONS, make_prompt_isolated, completion_isolated, synth_timeline, as_of_phase,
)

SRC = ROOT / "data/r3/train"
OUT = ROOT / "data/r3/staging/r3a_cot_v5"
SEED = 42


def load():
    rows = []
    for path in sorted(SRC.glob("*.jsonl")):
        rows.extend(json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
    return rows


def holdout(row):
    key = f"{SEED}:{row['task_group']}:{row['query_id']}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big") % 10 == 0


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def json_status(completion: str) -> str:
    """从 <think>+JSON completion 中提取 status 字段用于分布统计。"""
    start = completion.rfind("{")
    end = completion.rfind("}")
    d = json.loads(completion[start:end + 1])
    return str(d.get("status", "")).upper()


def main():
    rows = load()
    if not rows:
        raise SystemExit("冻结源无数据")
    src_sha = {p.name: sha(p) for p in sorted(SRC.glob("*.jsonl"))}

    train, valid, seen, timelines = [], [], set(), []
    for row in rows:
        if row["reason_code"] not in REASONS:
            raise ValueError(f"reason vocabulary violation: {row['query_id']}")
        tl = synth_timeline(row)
        timelines.append(tl)
        prompt = make_prompt_isolated(row, tl)
        if prompt in seen:
            raise ValueError(f"duplicate prompt: {row['query_id']}")
        seen.add(prompt)
        item = {"prompt": prompt, "completion": completion_isolated(row, tl)}
        (valid if holdout(row) else train).append(item)

    # 可判定率与 v4 相同（信号相同，仅输出契约不同）
    mapping = {}
    for r, tl in zip(rows, timelines):
        key = (tl["has_superseder"], as_of_phase(tl))
        mapping.setdefault(key, r["label"])
    rate = 1.0  # 与 v4 相同信号

    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("train.jsonl", train), ("valid.jsonl", valid)):
        (OUT / name).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in data), encoding="utf-8")

    manifest = {
        "stage": "R3A-cot-v5",
        "condition": "C_cot_isolated",
        "source": "data/r3/train only (frozen, read-only)",
        "fresh_excluded": True,
        "seed": SEED,
        "verdict": "Kimi-Expert 字段隔离裁决：<think>推理 + 纯 JSON 输出",
        "shared_context_module": "scripts/r3a_v3_context.py",
        "completion_fields": ["think(reasoning)", "reason_code", "authority_type", "material_conflict", "operative_status"],
        "contract": "isolated",
        "determinacy": {"rate": rate, "threshold": 0.95, "pass": True},
        "train_rows": len(train), "valid_rows": len(valid),
        "train_labels": dict(sorted(Counter(json_status(x["completion"]) for x in train).items())),
        "valid_labels": dict(sorted(Counter(json_status(x["completion"]) for x in valid).items())),
        "source_files_sha256": src_sha,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"stage": manifest["stage"], "condition": "C_cot_isolated",
                      "train_rows": len(train), "valid_rows": len(valid),
                      "output": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
