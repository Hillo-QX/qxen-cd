#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从已校验的 R3A-hard v1 派生纯状态 completion 数据，不读取 fresh。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/r3/staging/r3a_hard_v1"
OUT = ROOT / "data/r3/staging/r3a_hard_pure_v1"

def convert(src, dst):
    rows = []
    for line in (SRC / src).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        label = r["completion"].splitlines()[0].removeprefix("状态：")
        rows.append({"prompt": r["prompt"], "completion": label})
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows

def main():
    train = convert("train.jsonl", OUT / "train.jsonl")
    valid = convert("valid.jsonl", OUT / "valid.jsonl")
    manifest = {
        "stage": "R3A-hard-pure-v1",
        "source": "data/r3/staging/r3a_hard_v1",
        "fresh_excluded": True,
        "completion": "single status token",
        "train_rows": len(train), "valid_rows": len(valid),
        "unique_train_prompts": len({r["prompt"] for r in train}),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
