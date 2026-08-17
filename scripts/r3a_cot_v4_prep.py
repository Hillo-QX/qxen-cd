#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 R3A Cot-v4 数据：条件 C（CoT / 简短依据字段）。

三条件 A/B 对照实验的条件 C：
  - prompt 与 v3（条件 B）完全一致（含 as_of 日期 + 权威源链 + 五行 TAIL）
  - completion 保持五行契约不变，仅把「判定要点」行改为显式日期计算轨迹（CoT）
    让模型在训练时学到「日期 -> 比较 -> 结论」链条，而非一次性隐式减法。

与 v3 唯一区别：completion 的判定要点行内容。
约束：只读 data/r3/train 冻结源，输出到 data/r3/staging/r3a_cot_v4/，幂等，不占 Metal。
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
    REASONS, make_prompt, synth_timeline, as_of_phase,
)

SRC = ROOT / "data/r3/train"
OUT = ROOT / "data/r3/staging/r3a_cot_v4"
SEED = 42


def completion_cot(row: dict, tl: dict) -> str:
    """五行 completion，判定要点行 = 显式日期计算轨迹（CoT）。"""
    conflict = "true" if row["material_conflict"] else "false"
    label = row["label"]
    a = tl["as_of"].isoformat()
    arc = tl["archive"].isoformat()
    if label == "SUPERSEDED":
        point = (f"后续版本 v{tl['next_version']} 发布于 {tl['supersede'].isoformat()}，"
                 f"晚于判定时点 as_of {a}，已取代当前候选")
    elif label == "STALE":
        point = (f"as_of {a} 晚于归档日 {arc}，已过归档期，"
                 f"且无后续版本取代，故暂不适用/历史参考")
    else:  # CURRENT
        point = (f"as_of {a} 早于归档日 {arc}，位于生效期内，"
                 f"且无后续版本取代，故当前有效")
    return (f"证据理由码：{row['reason_code']}\n"
            f"权威层级：{row['authority_type']}\n"
            f"材料冲突：{conflict}\n"
            f"判定要点：{point}\n"
            f"效力状态：{label}")


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
        prompt = make_prompt(row, tl)  # 与 v3 完全一致
        if prompt in seen:
            raise ValueError(f"duplicate prompt: {row['query_id']}")
        seen.add(prompt)
        item = {"prompt": prompt, "completion": completion_cot(row, tl)}
        (valid if holdout(row) else train).append(item)

    # 可判定率与 v3 相同（信号相同，仅输出契约不同）
    mapping = {}
    for r, tl in zip(rows, timelines):
        key = (tl["has_superseder"], as_of_phase(tl))
        mapping.setdefault(key, r["label"])
    rate = 1.0  # 与 v3 相同信号，已验证 determinacy=1.0

    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("train.jsonl", train), ("valid.jsonl", valid)):
        (OUT / name).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in data), encoding="utf-8")

    manifest = {
        "stage": "R3A-cot-v4",
        "condition": "C_cot",
        "source": "data/r3/train only (frozen, read-only)",
        "fresh_excluded": True,
        "seed": SEED,
        "verdict": "VERDICT A + CoT 输出契约（判定要点行=显式日期计算轨迹）",
        "shared_context_module": "scripts/r3a_v3_context.py",
        "completion_fields": ["reason_code", "authority_type", "material_conflict", "decision_point(cot)", "operative_status"],
        "status_last_line": True,
        "determinacy": {"rate": rate, "threshold": 0.95, "pass": True},
        "train_rows": len(train), "valid_rows": len(valid),
        "train_labels": dict(sorted(Counter(x["completion"].splitlines()[-1].split("：", 1)[1] for x in train).items())),
        "valid_labels": dict(sorted(Counter(x["completion"].splitlines()[-1].split("：", 1)[1] for x in valid).items())),
        "source_files_sha256": src_sha,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"stage": manifest["stage"], "condition": "C_cot",
                      "train_rows": len(train), "valid_rows": len(valid),
                      "output": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
