#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T360 — R3A/R3B/R3C 组合测试脚本（静态准备）。

用户决策(2026-08-13): 每个 adapter 单独过 Gate 后再组合测试。

组合方式:
  三个独立 adapter（r3a status / r3b authority / r3c conflict）各自推理
  data/r3/fresh/ 540 条，按 query_id 合并三条预测，计算 skill §8 完整
  gate 指标（合并后整体判定）。

  - R3A 输出: operative_status (CURRENT/STALE/SUPERSEDED)
  - R3B 输出: authority (T0/T1/T2/T3/T4)
  - R3C 输出: material_conflict (true/false)

  单次只加载一个 adapter（推理完 del model 释放），避免内存叠加。

用法:
  ./venv/bin/python scripts/r3_combo_eval.py --dry-run     # 静态校验（不加载模型）
  ./venv/bin/python scripts/r3_combo_eval.py --limit 20    # 冒烟（3 个 adapter 已就绪后）
  ./venv/bin/python scripts/r3_combo_eval.py                # 完整 540 条
  ./venv/bin/python scripts/r3_combo_eval.py --base         # 含 base 对照 (Shadow)
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, glob

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
MODEL = "models/qwen3.5-9b-mlx-4bit"
ADAPTER_DIR = {
    "r3a": "models/r3a",
    "r3b": "models/r3b",
    "r3c": "models/r3c",
}
OUT = "reports/r3/r3_combo_eval.json"
RE_STATUS = re.compile(r"CURRENT|STALE|SUPERSEDED")
RE_AUTH = re.compile(r"T[0-4]")
RE_CONFLICT = re.compile(r"true|false", re.IGNORECASE)

# stage -> (目标字段, gold 键, prompt 后缀, 解析正则)
STAGE_SPEC = {
    "r3a": ("operative_status", "label",
            "\n只输出一行：\n效力状态：CURRENT/STALE/SUPERSEDED", RE_STATUS),
    "r3b": ("authority", "authority_type",
            "\n只输出一行：\n权威层级：T0-T4", RE_AUTH),
    "r3c": ("material_conflict", "material_conflict",
            "\n只输出一行：\n冲突：true/false", RE_CONFLICT),
}


def parse_one(out: str, regex):
    m = regex.search(out)
    return m.group(0) if m else "INVALID"


def prompt_for(r: dict, stage: str) -> str:
    _, _, suffix, _ = STAGE_SPEC[stage]
    return r["text"] + suffix


def infer_stage(r: dict, adapter: str, out: str) -> dict:
    """用指定 adapter 推理一条 fresh 记录，产出该 stage 的预测。"""
    regex = STAGE_SPEC[adapter][3]
    pred = parse_one(out, regex)
    gold_key = STAGE_SPEC[adapter][1]
    return {
        "query_id": r["query_id"],
        "stage": adapter,
        "pred": pred,
        "gold": r[gold_key],
    }


def run_stage(adapter: str, records: list, limit: int) -> list:
    from mlx_lm import generate, load
    model, tokenizer = load(MODEL, adapter_path=ADAPTER_DIR[adapter])
    out_records, t0 = [], time.time()
    for i, r in enumerate(records[:limit] if limit else records):
        f = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt_for(r, adapter)}], tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        out = generate(model, tokenizer, prompt=f, max_tokens=32, verbose=False)
        out_records.append(infer_stage(r, adapter, out))
        if (i + 1) % 50 == 0:
            print(f"[r3_combo] {adapter}: {i+1}/{len(records[:limit] if limit else records)} ({time.time()-t0:.0f}s)", flush=True)
    del model
    return out_records


def merge_and_metrics(by_query: dict) -> dict:
    """按 query_id 合并三条预测，计算 skill §8 完整 gate 指标。"""
    n = len(by_query)
    def acc(f):
        return sum(f(q) for q in by_query.values()) / n if n else 0.0
    status_acc = acc(lambda q: q["r3a"]["pred"] == q["gold"]["label"])
    auth_acc = acc(lambda q: q["r3b"]["pred"] == q["gold"]["authority_type"])
    sup = [q for q in by_query.values() if q["gold"]["label"] == "SUPERSEDED"]
    sup_rej = (sum(q["r3a"]["pred"] == "SUPERSEDED" for q in sup) / len(sup)) if sup else 0.0
    conf = [q for q in by_query.values() if q["gold"]["material_conflict"]]
    conf_recall = (sum(q["r3c"]["pred"] == "true" for q in conf) / len(conf)) if conf else 0.0
    wap = [q for q in by_query.values() if q["r3a"]["pred"] != q["gold"]["label"]]
    wrong_authority_pref = (sum(1 for q in wap if q["r3a"]["pred"] == "CURRENT") / n) if n else 0.0
    crit = [q for q in by_query.values() if q["gold"]["authority_type"] in ("T0", "T1")]
    crit_miss = sum(1 for q in crit if q["r3a"]["pred"] == "INVALID")
    invalid = sum(1 for q in by_query.values()
                  if any(q[s]["pred"] == "INVALID" for s in ("r3a", "r3b", "r3c")))
    return {
        "n": n,
        "operative_status_accuracy": round(status_acc, 4),
        "authority_ranking_accuracy": round(auth_acc, 4),
        "superseded_rejection": round(sup_rej, 4),
        "material_conflict_recall": round(conf_recall, 4),
        "wrong_authority_preference_rate": round(wrong_authority_pref, 4),
        "critical_t0_t1_miss": crit_miss,
        "invalid_output": invalid,
        "n_superseded": len(sup),
        "n_conflict": len(conf),
    }


GATE_THRESHOLDS = {
    "operative_status_accuracy": (0.90, ">="),
    "authority_ranking_accuracy": (0.90, ">="),
    "superseded_rejection": (0.95, ">="),
    "material_conflict_recall": (0.95, ">="),
    "wrong_authority_preference_rate": (0.03, "<="),
    "critical_t0_t1_miss": (0, "<="),
    "invalid_output": (0, "<="),
}


def verdict(metrics: dict):
    rows = []
    for k, (thr, op) in GATE_THRESHOLDS.items():
        val = metrics.get(k, 0)
        ok = val >= thr if op == ">=" else val <= thr
        rows.append((k, val, thr, op, ok))
    return rows, ("PASS" if all(ok for *_, ok in rows) else "FAIL")


def main() -> int:
    ap = argparse.ArgumentParser(description="R3A/R3B/R3C 组合测试")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--base", action="store_true", help="含 base 对照 (Shadow)")
    ap.add_argument("--dry-run", action="store_true", help="静态校验，不加载模型")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    rows = []
    for p in sorted(glob.glob("data/r3/fresh/*.jsonl")):
        rows += [json.loads(l) for l in open(p, encoding="utf-8")]
    if args.limit:
        rows = rows[:args.limit]

    # 静态校验
    if not rows:
        print(f"[r3_combo] FAIL: data/r3/fresh/ 无数据", file=sys.stderr)
        return 1
    missing = [f for f in ("query_id", "text", "label", "authority_type",
                           "material_conflict") if f not in rows[0]]
    if missing:
        print(f"[r3_combo] FAIL: fresh 数据缺字段 {missing}", file=sys.stderr)
        return 1
    missing_adapters = [s for s in ADAPTER_DIR if not os.path.isdir(ADAPTER_DIR[s])]
    if missing_adapters:
        print(f"[r3_combo] FAIL: adapter 目录缺失 -> {missing_adapters}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"[r3_combo] DRY-RUN PASS | fresh={len(rows)} rows | "
              f"adapters={json.dumps({s: os.path.isdir(p) for s, p in ADAPTER_DIR.items()}, ensure_ascii=False)}")
        return 0

    results = {}
    if args.base:
        from mlx_lm import generate, load
        model, tokenizer = load(MODEL)
        base = []
        for i, r in enumerate(rows):
            f = tokenizer.apply_chat_template(
                [{"role": "user", "content": r["text"]}], tokenize=False,
                add_generation_prompt=True, enable_thinking=False)
            out = generate(model, tokenizer, prompt=f, max_tokens=32, verbose=False)
            s = parse_one(out, RE_STATUS)
            base.append({"query_id": r["query_id"], "stage": "base", "pred": s,
                         "gold": r["label"]})
        del model
        results["base"] = {"n": len(base),
                           "operative_status_accuracy": round(
                               sum(b["pred"] == b["gold"] for b in base) / len(base), 4)}
    # 三个 stage 依次推理
    per_stage = {s: run_stage(s, rows, args.limit) for s in ("r3a", "r3b", "r3c")}
    # 按 query_id 合并
    by_query = {}
    for r in rows:
        by_query.setdefault(r["query_id"], {"gold": r})
    for s, recs in per_stage.items():
        for rec in recs:
            by_query.setdefault(rec["query_id"], {"gold": {}})
            by_query[rec["query_id"]][s] = {"pred": rec["pred"]}
    metrics = merge_and_metrics(by_query)
    results["combo"] = metrics
    rows_v, v = verdict(metrics)
    gate = {"metrics": [{"metric": k, "value": v, "threshold": t, "op": o, "ok": ok}
                        for k, v, t, o, ok in rows_v], "verdict": v}
    print(f"\n=== R3 组合门禁判定: {v} ===")
    for k, v, t, o, ok in rows_v:
        print(f"  {k}: {v} {o} {t} {'✓' if ok else '✗'}")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"results": results, "gate": gate}, fh, ensure_ascii=False, indent=2)
    print(f"报告: {args.out}")
    return 0 if gate["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
