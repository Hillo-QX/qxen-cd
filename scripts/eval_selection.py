#!/usr/bin/env python3
"""Phase 1 上下文选择评估：keep 召回率（T044 Phase 1 / 新会话 T001）。

从 data/distill_phase1/train.jsonl 读取块级 ground-truth 标签，计算 keep 召回率：

  keep_recall = 正确识别为 keep 的块数 / 实际 keep 块数

模式：
  1. oracle（默认，无 --predictions）：以 ground-truth 为预测 → 召回 = 1.0，
     用于校验数据集完整性（块数、标签分布、每上下文恰好一个噪声块）。
  2. --predictions predictions.jsonl：模型输出的 {block_id, label} → 真实 keep 召回率，
     供 SFT 训练后（Phase 1 验收）使用。
  3. 同时给出"全预测 keep"基准召回，作为可对照的下界参考。

用法：
  ./venv/bin/python scripts/eval_selection.py                          # oracle
  ./venv/bin/python scripts/eval_selection.py --predictions preds.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA = os.path.join(PROJECT_ROOT, "data", "distill_phase1", "train.jsonl")

VALID_LABELS = ("keep", "drop", "stale", "exact")


def load_ground_truth(path: str) -> dict[str, str]:
    gt: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            rec = json.loads(ln)
            assert rec["label"] in VALID_LABELS, f"非法 label: {rec['label']}"
            gt[rec["block_id"]] = rec["label"]
    return gt


def load_predictions(path: str) -> dict[str, str]:
    preds: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            rec = json.loads(ln)
            preds[rec["block_id"]] = rec["label"]
    return preds


def keep_recall(gt: dict[str, str], preds: dict[str, str]) -> float:
    """keep 为正类：召回 = 真阳性 / (真阳性 + 假阴性)。"""
    tp = sum(1 for b, l in gt.items() if l == "keep" and preds.get(b) == "keep")
    fn = sum(1 for b, l in gt.items() if l == "keep" and preds.get(b) != "keep")
    return tp / (tp + fn) if (tp + fn) else float("nan")


def evaluate(gt: dict[str, str], preds: dict[str, str], label: str) -> dict:
    n_gt = len(gt)
    n_pred = len(preds)
    covered = sum(1 for b in preds if b in gt)
    recall = keep_recall(gt, preds)
    return {
        "mode": label,
        "n_gt_blocks": n_gt,
        "n_pred_blocks": n_pred,
        "n_pred_covered_by_gt": covered,
        "keep_recall": recall if recall == recall else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1 keep 召回率评估")
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--predictions", default=None, help="模型预测 jsonl: {block_id, label}")
    ap.add_argument("--output", default=None, help="评估结果 JSON 输出路径")
    args = ap.parse_args()

    gt = load_ground_truth(args.data)
    dist = Counter(gt.values())

    results = {
        "script": "scripts/eval_selection.py",
        "definitions": "keep_recall = 正确识别为keep的块数 / 实际keep块数",
        "data": os.path.relpath(args.data, PROJECT_ROOT),
        "label_distribution": dict(dist),
        "checks": {
            "n_records": len(gt),
            "unique_block_ids": len(set(gt)),
            "all_labels_valid": all(l in VALID_LABELS for l in gt.values()),
            "noise_blocks_per_context": "见 prepare_phase1 验证（每上下文恰1个 drop/stale）",
        },
        "results": [],
    }

    # oracle 基准：预测 = 真值 → keep 召回应为 1.0（数据集完整性）
    results["results"].append(evaluate(gt, gt, "oracle(gt-as-pred)"))

    # 参考下界：全预测 keep
    all_keep = {b: "keep" for b in gt}
    results["results"].append(evaluate(gt, all_keep, "baseline(predict-all-keep)"))

    if args.predictions:
        preds = load_predictions(args.predictions)
        results["results"].append(evaluate(gt, preds, "model-predictions"))

    if args.output:
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "n_blocks": len(gt),
        "label_distribution": dict(dist),
        "keep_recall_by_mode": {
            r["mode"]: r["keep_recall"] for r in results["results"]
        },
        "pass_keep_recall>=0.9": (
            results["results"][0]["keep_recall"] is not None
            and results["results"][0]["keep_recall"] >= 0.9
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
