#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Base/LoRA 同口径 Evidence Capsule 对照评估。

评估只使用 clean valid 入口，并显式 enable_thinking=False；解析失败计入
invalid，不让评估器崩溃。脚本不修改模型、数据或 adapter。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "models/qwen3.5-9b-mlx-4bit"
DEFAULT_VALID = ROOT / "data/r3/ec_v1/data1000/clean_train_format/valid.jsonl"


def parse_first_json(text: str):
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def load_rows(valid_path: Path):
    return [json.loads(x) for x in valid_path.read_text(encoding="utf-8").splitlines() if x.strip()]


def run(adapter: str | None, limit: int | None, out_path: Path, max_tokens: int,
        valid_path: Path, schema: str) -> dict:
    from mlx_lm import generate, load

    rows = load_rows(valid_path)
    rows = rows[:limit] if limit else rows
    model, tokenizer = load(BASE, adapter_path=adapter)
    results = []
    parse_count = 0
    field_hits = {k: 0 for k in ("relevance", "key_evidence", "sufficiency", "next_step")}
    schema_required = ("schema_version", "task_type", "relevance", "key_evidence",
                       "sufficiency", "uncertainty", "next_step", "assessed_fields", "profiles")
    schema_count = 0
    type_count = 0
    status_hits = 0
    status_total = 0
    t0 = time.time()
    for idx, row in enumerate(rows):
        gold = json.loads(row["completion"])
        prompt = tokenizer.apply_chat_template(
            [{"role": "user", "content": row["prompt"] +
             "\n输出约束：只输出一个紧凑 JSON 对象；key_evidence 最多 2 条，"
             "timeline、relations、conflicts、uncertainty 各最多 3 条；"
             "不要重复数组元素，不要输出 Markdown、解释或推理过程；"
             "材料未提供的字段使用空数组或 null。"}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        raw = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)
        pred = parse_first_json(raw)
        truncated = pred is None and len(raw) > 0
        if pred is not None:
            parse_count += 1
            if schema == "ec_v2":
                profiles = pred.get("profiles")
                schema_count += int(all(k in pred for k in schema_required))
                type_count += int(
                    isinstance(pred.get("key_evidence"), list)
                    and isinstance(pred.get("assessed_fields"), list)
                    and isinstance(profiles, dict)
                    and all(k in profiles for k in ("timeline", "relations", "conflicts",
                                                     "operative_status", "authority", "provenance"))
                )
            for key in field_hits:
                field_hits[key] += int(pred.get(key) == gold.get(key))
            pred_status = pred.get("operative_status")
            gold_status = gold.get("operative_status")
            if schema == "ec_v2":
                pred_status = (pred.get("profiles") or {}).get("operative_status")
                gold_status = (gold.get("profiles") or {}).get("operative_status")
            if gold_status in {"CURRENT", "STALE", "SUPERSEDED"}:
                status_total += 1
                status_hits += int(pred_status == gold_status)
        results.append({"id": idx, "parsed": pred is not None, "truncated": truncated,
                        "raw": raw, "pred": pred})
        if (idx + 1) % 20 == 0:
            print(f"generated {idx + 1}/{len(rows)} parse={parse_count}", flush=True)
    report = {
        "adapter": adapter or "BASE",
        "valid": str(valid_path),
        "n": len(rows),
        "parse_rate": parse_count / len(rows) if rows else 0,
        "invalid": len(rows) - parse_count,
        "schema": schema,
        "schema_valid_on_parsed": schema_count / parse_count if parse_count and schema == "ec_v2" else None,
        "type_valid_on_parsed": type_count / parse_count if parse_count and schema == "ec_v2" else None,
        "field_accuracy_on_parsed": {k: v / parse_count if parse_count else 0 for k, v in field_hits.items()},
        "operative_status_accuracy_on_status_rows": status_hits / status_total if status_total else None,
        "elapsed_s": round(time.time() - t0, 1),
        "max_tokens": max_tokens,
        "template": "apply_chat_template(add_generation_prompt=True, enable_thinking=False)",
    }
    out_path.write_text(json.dumps({"report": report, "predictions": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=1000)
    ap.add_argument("--valid", default=str(DEFAULT_VALID))
    ap.add_argument("--schema", choices=("ec_v1", "ec_v2"), default="ec_v1")
    args = ap.parse_args()
    run(args.adapter, args.limit, ROOT / args.out, args.max_tokens,
        Path(args.valid), args.schema)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
