#!/usr/bin/env python3
"""Measure QXEN-CD context burden ratio across source lengths.

Metric: final GPT payload chars / chars GPT would have read directly.
If the gate bypasses QXEN, final GPT payload is counted as the original chars
and ratio is 1.0 by design.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qxen_cd_mcp as qxen  # noqa: E402


def make_text(chars: int) -> str:
    facts = [
        "2026年7月社会融资规模同比多增，政府债券和企业债券融资是主要贡献。",
        "居民中长期贷款仍偏弱，反映房地产和居民加杠杆意愿尚未明显修复。",
        "企业中长期贷款边际改善，但票据融资占比仍提示实体信用需求恢复不均衡。",
        "M1同比回升与财政支出、存款活化有关，需要结合M2和社融结构确认趋势。",
        "利率下行环境中，信用脉冲是否持续取决于贷款需求、财政节奏和企业投资意愿。",
    ]
    text = "\n".join(facts)
    while len(text) < chars:
        text += "\n" + "\n".join(facts)
    return text[:chars]


async def run_size(chars: int, use_fake: bool) -> dict:
    text = make_text(chars)
    path: Path | None = None
    original = qxen._qxen_generate
    if use_fake:
        async def fake_generate(**kwargs):
            return {
                "runtime": "QXEN-CD",
                "task": "qxen_longtext_distill",
                "guard_status": "ADVISORY",
                "requires_gpt_review": False,
                "review_policy": "conditional",
                "gpt_context": {"context_mode": "ADVISORY_ONLY", "capsule": {
                    "summary": ["社融多增，债券融资贡献；居民贷款偏弱；企业贷款边际改善；M1回升需结合M2与社融结构验证。"],
                    "timeline": ["2026年7月"],
                    "source": kwargs.get("source"),
                    "advisory_only": True,
                }},
            }
        qxen._qxen_generate = fake_generate
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            handle.write(text)
            path = Path(handle.name)
        result = await qxen.qxen_cd_longtext_distill(
            source=path.name,
            source_path=str(path),
            max_tokens=700,
            compact_max_chars=2000,
        )
        burden = result.get("context_burden") or {}
        return {
            "source_chars": len(text),
            "decision": burden.get("decision"),
            "ratio": burden.get("ratio"),
            "final_gpt_chars": burden.get("final_gpt_chars"),
            "saved_chars": burden.get("saved_chars"),
            "accepted_capsules": result.get("accepted_capsule_count"),
            "chunking": result.get("chunking", {}),
            "bypass_reason": result.get("bypass_reason", ""),
        }
    finally:
        if path:
            path.unlink(missing_ok=True)
        qxen._qxen_generate = original


async def main_async(args: argparse.Namespace) -> int:
    rows = []
    for size in args.sizes:
        rows.append(await run_size(size, args.fake))
    injected = [row for row in rows if row.get("decision") == "INJECT_QXEN"]
    avg_ratio = sum(float(row["ratio"]) for row in rows) / len(rows) if rows else None
    avg_injected_ratio = (sum(float(row["ratio"]) for row in injected) / len(injected)
                          if injected else None)
    print(json.dumps({
        "metric": "final_gpt_payload_chars / direct_source_chars",
        "mode": "fake_deterministic" if args.fake else "real_qxen",
        "rows": rows,
        "summary": {
            "calls": len(rows),
            "inject_calls": len(injected),
            "bypass_calls": len(rows) - len(injected),
            "avg_context_burden_ratio": round(avg_ratio, 6) if avg_ratio is not None else None,
            "avg_injected_ratio": round(avg_injected_ratio, 6) if avg_injected_ratio is not None else None,
        },
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[2000, 3000, 4000, 5000, 6000, 6500, 8000, 10000])
    ap.add_argument("--fake", action="store_true", help="mock QXEN for deterministic CI gate test")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
