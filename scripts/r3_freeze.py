#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T341 — 冻结清单生成脚本。

对 R3 adapter/config 计算 sha256 并写入 frozen/r3_freeze_manifest.json。

用法:
  python3 scripts/r3_freeze.py --adapter adapters/r3/adapters.safetensors \
      --config adapters/r3/adapter_config.json \
      --version QXEN-CD-R3-v0.1 --gate reports/r3/r3_fresh_test_report.json

约束:
  - 冻结前必须通过 Fresh Test + Shadow 门禁（由调用方保证，脚本仅记录 gate 引用）
  - 冻结后任何 sha256 不匹配即视为基线被破坏
  - 禁止修改 outputs/lora_adapters_r1_selected/（R1 冻结区）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(PROJECT_ROOT, "frozen", "r3_freeze_manifest.json")
DEFAULT_BASELINE_REF = "outputs/lora_adapters_r1_selected/FROZEN_BASELINE.md"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="R3 冻结清单生成")
    ap.add_argument("--adapter", required=True, help="adapters.safetensors 路径（相对项目根）")
    ap.add_argument("--config", required=True, help="adapter_config.json 路径（相对项目根）")
    ap.add_argument("--version", required=True, help="冻结版本号，如 QXEN-CD-R3-v0.1")
    ap.add_argument("--gate", default="reports/r3/r3_fresh_test_report.json", help="前置门禁报告路径")
    ap.add_argument("--baseline_ref", default=DEFAULT_BASELINE_REF, help="R1 基线引用")
    ap.add_argument("--status", default="FROZEN")
    args = ap.parse_args()

    adapter_path = os.path.join(PROJECT_ROOT, args.adapter)
    config_path = os.path.join(PROJECT_ROOT, args.config)
    missing = [p for p in (adapter_path, config_path) if not os.path.exists(p)]
    if missing:
        print(f"[r3_freeze] FAIL — 文件不存在: {missing}")
        return 1

    freeze = {
        "version": args.version,
        "adapter_path": args.adapter,
        "config_path": args.config,
        "sha256": {
            "adapters.safetensors": sha256_of(adapter_path),
            "adapter_config.json": sha256_of(config_path),
        },
        "freeze_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseline_ref": args.baseline_ref,
        "gate_ref": args.gate,
        "status": args.status,
        "note": "冻结后任何 sha256 不匹配即视为基线被破坏",
    }

    manifest = {"stage": "R3", "schema_version": "1.0", "freezes": []}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            manifest = json.load(f)
    manifest["freezes"].append(freeze)
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[r3_freeze] 冻结完成: {args.version}")
    print(f"  adapters.safetensors: {freeze['sha256']['adapters.safetensors']}")
    print(f"  adapter_config.json:  {freeze['sha256']['adapter_config.json']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
