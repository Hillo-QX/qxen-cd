"""verifier：复用 scripts/compute_metrics.py 的 CIR/CPR 提取逻辑（T044 Phase 0.2）。

不重复实现任何提取逻辑；仅做：
  1. 字段映射：prompt/completion -> instruction/response；
  2. 逐条 CIR/CPR / 压缩比 / 遗漏约束列表 计算（复用 extract_critical/extract_constraints）；
  3. 与 compute_metrics.py 的聚合结果交叉校验（浮点误差 < 1e-6）；
  4. 输出结构化 JSON 到 data/distill_phase0/。

用法：
  ./venv/bin/python scripts/verifier.py --input data/distill_phase0/train.jsonl \
      --output data/distill_phase0/verifier_cir_cpr.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# ---- 复用 compute_metrics.py（scripts 目录无 __init__.py，按文件路径加载） ----
_CM_PATH = os.path.join(PROJECT_ROOT, "scripts", "compute_metrics.py")
_spec = importlib.util.spec_from_file_location("compute_metrics_mod", _CM_PATH)
cm = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(cm)

extract_critical = cm.extract_critical
extract_constraints = cm.extract_constraints
compute_metrics = cm.compute_metrics

CIR_TARGET = 0.98
CPR_TARGET = 0.99


def _nan(v: float) -> float:
    """NaN 序列化为 null（JSON 兼容）。"""
    return float("nan") if v != v else v


def _eq_or_both_nan(a: float, b: float, tol: float = 1e-6) -> bool:
    if a != a and b != b:  # both NaN
        return True
    return abs(a - b) < tol


def map_record(rec: dict) -> dict:
    """字段映射：verifier 输入记录 -> compute_metrics 期望的 instruction/response。"""
    return {
        "instruction": rec.get("prompt", ""),
        "response": rec.get("completion", ""),
    }


def verify_record(rec: dict) -> dict:
    """逐条 CIR/CPR：复用 compute_metrics 的提取函数，不重复实现正则/关键词。"""
    inst = rec.get("prompt", "")
    resp = rec.get("completion", "")
    c_req = extract_critical(inst)
    c_pres = len(extract_critical(resp) & c_req)
    co_req = extract_constraints(inst)
    con_pres = len(extract_constraints(resp) & co_req)
    return {
        "id": rec.get("id", ""),
        "sample_id": rec.get("sample_id", ""),
        "noise_type": rec.get("noise_type", ""),
        "critical_required": len(c_req),
        "critical_preserved": c_pres,
        "CIR": _nan(c_pres / len(c_req)) if c_req else None,
        "constraints_required": len(co_req),
        "constraints_preserved": con_pres,
        "CPR": _nan(con_pres / len(co_req)) if co_req else None,
        "missing_constraints": sorted(co_req - extract_constraints(resp)),
        "compression_ratio": _nan(len(resp) / len(inst)) if inst else None,
    }


def verify_dataset(path: str) -> dict:
    """对数据集逐条验证并聚合。"""
    with open(path, "r", encoding="utf-8") as fh:
        records = [json.loads(ln) for ln in fh if ln.strip()]
    per = [verify_record(r) for r in records]

    c_req = sum(r["critical_required"] for r in per)
    c_pres = sum(r["critical_preserved"] for r in per)
    co_req = sum(r["constraints_required"] for r in per)
    co_pres = sum(r["constraints_preserved"] for r in per)
    agg = {
        "records": len(per),
        "critical_required": c_req,
        "critical_preserved": c_pres,
        "CIR": _nan(c_pres / c_req) if c_req else None,
        "constraints_required": co_req,
        "constraints_preserved": co_pres,
        "CPR": _nan(co_pres / co_req) if co_req else None,
    }
    return {"aggregate": agg, "per_record": per, "_raw_records": records}


def compare_with_compute_metrics(records: list[dict], agg: dict) -> dict:
    """与 compute_metrics.py 直接聚合结果交叉校验（浮点误差 < 1e-6）。"""
    ref = compute_metrics([map_record(r) for r in records])
    cir_ok = _eq_or_both_nan(
        agg["CIR"] if agg["CIR"] is not None else float("nan"),
        ref["CIR"] if ref["CIR"] == ref["CIR"] else float("nan"),
    )
    cpr_ok = _eq_or_both_nan(
        agg["CPR"] if agg["CPR"] is not None else float("nan"),
        ref["CPR"] if ref["CPR"] == ref["CPR"] else float("nan"),
    )
    return {
        "ok": cir_ok and cpr_ok,
        "diff": {
            "CIR": abs(agg["CIR"] - ref["CIR"]) if (agg["CIR"] and ref["CIR"] == ref["CIR"]) else None,
            "CPR": abs(agg["CPR"] - ref["CPR"]) if (agg["CPR"] and ref["CPR"] == ref["CPR"]) else None,
        },
        "reference_aggregate": ref,
    }


def summarize(per: list[dict]) -> dict:
    """通过/失败分布（对齐 Skill 目标 CIR>=0.98 / CPR>=0.99）。"""
    n = len(per)
    cir_ok = sum(1 for r in per if r["CIR"] is not None and r["CIR"] >= CIR_TARGET)
    cpr_ok = sum(1 for r in per if r["CPR"] is not None and r["CPR"] >= CPR_TARGET)
    both = sum(
        1 for r in per
        if (r["CIR"] is not None and r["CIR"] >= CIR_TARGET)
        and (r["CPR"] is not None and r["CPR"] >= CPR_TARGET)
    )
    no_critical = sum(1 for r in per if r["critical_required"] == 0)
    no_constraints = sum(1 for r in per if r["constraints_required"] == 0)
    return {
        "records": n,
        "n_CIR>=0.98": cir_ok,
        "n_CPR>=0.99": cpr_ok,
        "n_pass_both": both,
        "n_no_critical_tokens": no_critical,
        "n_no_constraints": no_constraints,
        "CIR_hist": _hist(per, "CIR"),
        "CPR_hist": _hist(per, "CPR"),
    }


def _hist(per: list[dict], key: str) -> dict:
    buckets = {"[0,0.2)": 0, "[0.2,0.4)": 0, "[0.4,0.6)": 0,
               "[0.6,0.8)": 0, "[0.8,1.0)": 0, "1.0": 0, "null": 0}
    for r in per:
        v = r[key]
        if v is None:
            buckets["null"] += 1
        elif v >= 1.0:
            buckets["1.0"] += 1
        elif v >= 0.8:
            buckets["[0.8,1.0)"] += 1
        elif v >= 0.6:
            buckets["[0.6,0.8)"] += 1
        elif v >= 0.4:
            buckets["[0.4,0.6)"] += 1
        elif v >= 0.2:
            buckets["[0.2,0.4)"] += 1
        else:
            buckets["[0,0.2)"] += 1
    return buckets


def main() -> int:
    ap = argparse.ArgumentParser(description="verifier: 复用 compute_metrics 的 CIR/CPR")
    ap.add_argument("--input", default=os.path.join(PROJECT_ROOT, "data", "distill_phase0", "train.jsonl"))
    ap.add_argument("--output", default=os.path.join(PROJECT_ROOT, "data", "distill_phase0", "verifier_cir_cpr.json"))
    args = ap.parse_args()

    result = verify_dataset(args.input)
    records = result.pop("_raw_records")
    comparison = compare_with_compute_metrics(records, result["aggregate"])
    summary = summarize(result["per_record"])

    out = {
        "script": "scripts/verifier.py",
        "generated_at": "2026-08-13",
        "input": os.path.relpath(args.input, PROJECT_ROOT),
        "definitions": "QXEN SKILL §14 (CIR=critical info recall, CPR=constraint preservation)",
        "aggregate": result["aggregate"],
        "comparison_with_compute_metrics": comparison,
        "summary": summary,
        "per_record": result["per_record"],
    }
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "records": summary["records"],
        "comparison_ok": comparison["ok"],
        "diff": comparison["diff"],
        "aggregate_CIR": result["aggregate"]["CIR"],
        "aggregate_CPR": result["aggregate"]["CPR"],
        "n_CIR>=0.98": summary["n_CIR>=0.98"],
        "n_CPR>=0.99": summary["n_CPR>=0.99"],
        "output": os.path.relpath(args.output, PROJECT_ROOT),
    }, ensure_ascii=False, indent=2))
    return 0 if comparison["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
