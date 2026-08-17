#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T341 — 审计记录脚本。

向 audit/r3_audit_log.json 追加一条审计记录：
  {timestamp, action, file_hash, operator, result}

用法:
  python3 scripts/r3_audit.py --action train \
      --files adapters/r3/adapter_config.json adapters/r3/adapters.safetensors \
      --operator executor --result PASS

  file_hash: 对 --files 中每个存在的文件计算 sha256，合并进一条记录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_LOG = os.path.join(PROJECT_ROOT, "audit", "r3_audit_log.json")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="R3 审计记录追加")
    ap.add_argument("--action", required=True, help="操作类型: validate/verify/train/freeze/promote 等")
    ap.add_argument("--files", nargs="*", default=[], help="需记录哈希的文件路径（相对项目根）")
    ap.add_argument("--operator", default="executor", help="操作者")
    ap.add_argument("--result", default="PASS", help="结果: PASS/FAIL")
    ap.add_argument("--note", default="", help="备注")
    args = ap.parse_args()

    file_hash = {}
    for rel in args.files:
        p = os.path.join(PROJECT_ROOT, rel)
        if os.path.exists(p):
            file_hash[rel] = sha256_of(p)
        else:
            file_hash[rel] = "FILE_NOT_FOUND"

    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": args.action,
        "file_hash": file_hash,
        "operator": args.operator,
        "result": args.result,
        "note": args.note,
    }

    log = {"stage": "R3", "schema_version": "1.0", "entries": []}
    if os.path.exists(AUDIT_LOG):
        with open(AUDIT_LOG, encoding="utf-8") as f:
            log = json.load(f)
    log["entries"].append(entry)
    with open(AUDIT_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"[r3_audit] 已追加记录: {entry['timestamp']} action={entry['action']} result={entry['result']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
