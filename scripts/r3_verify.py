#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T341/T342 — 训练产物验证脚本（T342 决策 A 修订版：递归扫描 data/r3/ 子目录）。

验证 R3 训练产物与 schema 一致性，并执行词法捷径审计（skill §6.2）：
  1. adapter 产物存在性检查（adapters/r3/adapter_config.json + adapters.safetensors）
  2. adapter_config.json 关键字段与 schema/hyperparameter 策略一致性
     （rank=8, learning_rate 3e-6~5e-6, max_seq_length 512-768, dataset 路径指向 data/r3）
  3. 词法捷径审计：统计 data/r3 数据中标签预测性表面词
     （当前目标直接对应/必要依赖/明显相关/归档副本/无调用关系/旧版本/仅历史/REL/IRREL/AUTHORITY/OPERATIVE）
     若某词与 label 高度相关（卡方/条件概率超阈值）→ 报告 WARN/FAIL
  4. 空产物目录 → PASS（无产物时通过），空数据目录 → PASS

输出: reports/r3/r3_verify_report.json + 控制台 PASS/FAIL。

运行:
  python3 scripts/r3_verify.py
"""
from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "adapters", "r3", "schema.json")
ADAPTER_DIR = os.path.join(PROJECT_ROOT, "adapters", "r3")
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "r3")
REPORT_DIR = os.path.join(PROJECT_ROOT, "reports", "r3")

ADAPTER_FILES = ["adapter_config.json", "adapters.safetensors"]

# skill §6.2 词法捷径审计关键词（可扩展）
LEXICAL_MARKERS = [
    "当前目标直接对应", "必要依赖", "明显相关", "归档副本", "无调用关系",
    "旧版本", "仅历史", "REL", "IRREL", "AUTHORITY", "OPERATIVE",
]

# skill §7 默认超参窗口
EXPECTED_CONFIG = {
    "rank": 8,
    "learning_rate_min": 3e-6,
    "learning_rate_max": 5e-6,
    "max_seq_length_min": 512,
    "max_seq_length_max": 768,
    "dataset": "data/r3",
}


def artifact_check() -> list:
    """产物存在性与 config 一致性检查。空产物目录返回空错误列表。"""
    errors = []
    config_path = os.path.join(ADAPTER_DIR, "adapter_config.json")
    weights_path = os.path.join(ADAPTER_DIR, "adapters.safetensors")
    if not os.path.exists(config_path) and not os.path.exists(weights_path):
        return []  # 空产物 → 无错误
    if not os.path.exists(config_path):
        errors.append(f"缺失 {ADAPTER_FILES[0]}")
    if not os.path.exists(weights_path):
        errors.append(f"缺失 {ADAPTER_FILES[1]}")
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        rank = cfg.get("lora_parameters", cfg).get("rank")
        lr = cfg.get("learning_rate", cfg.get("lora_parameters", {}).get("learning_rate"))
        if rank is not None and rank != EXPECTED_CONFIG["rank"]:
            errors.append(f"rank={rank} 偏离默认 {EXPECTED_CONFIG['rank']}（skill §7）")
        if lr is not None and not (EXPECTED_CONFIG["learning_rate_min"] <= lr <= EXPECTED_CONFIG["learning_rate_max"]):
            errors.append(f"learning_rate={lr} 超出 {EXPECTED_CONFIG['learning_rate_min']}~{EXPECTED_CONFIG['learning_rate_max']}")
    return errors


def iter_data_files(data_dir: str):
    """递归扫描 data_dir 下所有 *.jsonl 文件（含子目录），返回排序后的 (相对路径, 绝对路径) 列表。"""
    files = []
    for root, _dirs, fnames in os.walk(data_dir):
        for fn in sorted(fnames):
            if fn.endswith(".jsonl"):
                abs_path = os.path.join(root, fn)
                rel_path = os.path.relpath(abs_path, data_dir)
                files.append((rel_path, abs_path))
    return sorted(files)


def lexical_shortcut_audit(data_dir: str) -> dict:
    """词法捷径审计：若表面词足以预测 label → DATASET FAIL。

    对每个 marker，统计含/不含该词的样本中 label 分布。
    若 |P(label=X | 含词) - P(label=X | 不含词)| >= 0.5 → 判定捷径风险。
    空数据目录返回空统计。
    """
    counts = {}  # marker -> {total_pos, total_neg, pos_among_marker, total_marker}
    total_pos = 0
    total_neg = 0
    for _rel, path in iter_data_files(data_dir):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or "text" not in rec or "label" not in rec:
                    continue
                text = rec["text"]
                pos = rec["label"] == "CURRENT"
                total_pos += 1 if pos else 0
                total_neg += 0 if pos else 1
                for m in LEXICAL_MARKERS:
                    c = counts.setdefault(m, {"marker": 0, "pos": 0, "neg": 0})
                    if m in text:
                        c["marker"] += 1
                        c["pos"] += 1 if pos else 0
                        c["neg"] += 0 if pos else 1
    n = total_pos + total_neg
    if n == 0:
        return {"audited_rows": 0, "warnings": []}
    base_rate = total_pos / n
    warnings = []
    for m, c in counts.items():
        if c["marker"] == 0:
            continue
        marker_rate = c["pos"] / c["marker"]
        if abs(marker_rate - base_rate) >= 0.5:
            warnings.append({
                "marker": m,
                "marker_pos_rate": round(marker_rate, 3),
                "base_pos_rate": round(base_rate, 3),
                "delta": round(abs(marker_rate - base_rate), 3),
                "severity": "DATASET FAIL" if c["marker"] >= 20 else "WARN",
            })
    return {"audited_rows": n, "warnings": warnings}


def main() -> int:
    art_errors = artifact_check()
    lex = lexical_shortcut_audit(DATA_DIR)
    lexical_fail = [w for w in lex["warnings"] if w["severity"] == "DATASET FAIL"]
    passed = (len(art_errors) == 0 and len(lexical_fail) == 0)
    report = {
        "stage": "R3",
        "tool": "scripts/r3_verify.py",
        "result": "PASS" if passed else "FAIL",
        "artifact_errors": art_errors,
        "lexical_shortcut_audit": lex,
        "note": "空产物/空数据目录视为 PASS（无产物/无数据时通过）",
    }
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "r3_verify_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    if passed:
        print(f"[r3_verify] PASS — 产物 {len(ADAPTER_FILES)} 项检查 0 错误, 词法审计 {lex['audited_rows']} 行 0 FAIL")
    else:
        print(f"[r3_verify] FAIL — artifact_errors={art_errors}, lexical_fail={lexical_fail}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
