#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence Capsule v1 推理评估（T001 步骤2）。

加载 models/ec_v1 adapter（LoRA rank8 训练于 pool 20 条），
对 pool 全量 20 条用与训练一致的 prompt 生成证据胶囊 JSON，
输出 pred_capsules.jsonl（含 capsule_id 与 gold 对齐），供 ec_v1_eval.py 做多维 Gate 评估。

约束：
  - 评估不并行训练（训练已结束）
  - 不修改冻结资产
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ec_v1_build_train import (  # noqa: E402
    build_manual_sample,
    build_real_sample,
)

POOL = ROOT / "data/r3/ec_v1/pool/ec_v1_pool.jsonl"
OUT = ROOT / "data/r3/ec_v1/pred/ec_v1_pred_capsules.jsonl"
BASE_MODEL = "models/qwen3.5-9b-mlx-4bit"
ADAPTER = "models/ec_v1"


def parse_json_output(text: str) -> dict | None:
    """从生成文本中提取 JSON（容忍 code fence 与前后缀）。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def main() -> int:
    from mlx_lm import generate, load

    pool = [json.loads(l) for l in POOL.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not pool:
        print("ERROR: pool empty")
        return 1

    print("loading model + adapter ...")
    model, tokenizer = load(BASE_MODEL, adapter_path=ADAPTER)

    preds = []
    for i, c in enumerate(pool):
        sample = (
            build_manual_sample(c)
            if c.get("provenance") == "manual_annotated"
            else build_real_sample(c)
        )
        prompt = sample["prompt"]
        text = generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=800,
            verbose=False,
        )
        parsed = parse_json_output(text)
        if parsed is None:
            print(f"[{i+1}/{len(pool)}] {c['capsule_id']}: PARSE_FAIL, raw_head={text[:80]!r}")
            preds.append({"capsule_id": c["capsule_id"], "raw": text[:2000]})
            continue
        parsed["capsule_id"] = c["capsule_id"]
        preds.append(parsed)
        print(f"[{i+1}/{len(pool)}] {c['capsule_id']}: OK")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in preds), encoding="utf-8"
    )
    ok = sum(1 for p in preds if "raw" not in p)
    print(f"done: {ok}/{len(pool)} parsed, saved -> {OUT}")
    return 0 if ok == len(pool) else 1


if __name__ == "__main__":
    sys.exit(main())
