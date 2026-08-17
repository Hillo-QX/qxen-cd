#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T356 — R3A/R3B/R3C 拆分 adapter Fresh Test 门禁评估。

对 data/r3/fresh/ 540 条用指定 stage 的 adapter 推理，解析对应目标，
计算 skill §8 Gate 指标（按 stage 过滤，只评估本 stage 负责的目标）：

  R3A (operative_status, CURRENT/STALE/SUPERSEDED):
    Operative Status Accuracy       >= 0.90
    Superseded Rejection            >= 0.95
    Wrong-Authority Preference Rate <= 0.03
    Critical T0/T1 Miss            ~= 0
    Invalid Output                  = 0

  R3B (authority, T0-T4): Authority Ranking Accuracy >= 0.90, Invalid Output = 0
  R3C (material_conflict): Material Conflict Recall >= 0.95, Invalid Output = 0

用法:
  ./venv/bin/python scripts/r3_gate_eval.py --stage r3a --runs r3a      # 只跑 R3A adapter
  ./venv/bin/python scripts/r3_gate_eval.py --stage r3a --runs base r3a # base 对照 (Shadow)
  ./venv/bin/python scripts/r3_gate_eval.py --stage r3a --dry-run       # 静态校验（不加载模型）
  ./venv/bin/python scripts/r3_gate_eval.py --stage r3a --limit 20      # 冒烟
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, glob
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
from r3a_v3_context import (  # noqa: E402
    make_prompt as make_prompt_v3,
    synth_timeline as synth_timeline_v3,
)
MODEL = "models/qwen3.5-9b-mlx-4bit"
ADAPTER_DIR = {
    "r3a": "models/r3a",
    "r3b": "models/r3b",
    "r3c": "models/r3c",
}
OUT_DIR = "reports/r3"
RE_STATUS = re.compile(r"CURRENT|STALE|SUPERSEDED")
RE_STATUS_LINE = re.compile(r"效力状态\s*[:：]\s*(CURRENT|STALE|SUPERSEDED)", re.IGNORECASE)
RE_AUTH = re.compile(r"T[0-4]")
RE_CONFLICT = re.compile(r"true|false", re.IGNORECASE)


def parse(out, stage: str, structured: bool = False):
    """按 stage 从输出中解析目标字段。缺省/非法 -> INVALID。"""
    out = out.strip()
    if stage == "r3a":
        if structured:
            matches = RE_STATUS_LINE.findall(out)
            return (matches[-1].upper() if matches else "INVALID",)
        s = RE_STATUS.search(out.upper())
        return (s.group(0) if s else "INVALID",)
    if stage == "r3b":
        a = RE_AUTH.search(out)
        return (a.group(0) if a else "INVALID",)
    if stage == "r3c":
        c = RE_CONFLICT.search(out)
        return (("true" if c and c.group(0).lower() == "true" else
                 "false" if c else "INVALID"),)
    raise ValueError(f"未知 stage: {stage}")


def prompt_for(r, stage: str, structured: bool = False):
    if stage == "r3a":
        if structured:
            # v3：注入 as_of 时间锚点 + 权威源链 + 五行 TAIL，与训练共用同一构造
            return make_prompt_v3(r, synth_timeline_v3(r))
        return (r["text"] +
                "\n只输出一行：\n效力状态：CURRENT/STALE/SUPERSEDED")
    if stage == "r3b":
        return (r["text"] +
                "\n只输出一行：\n权威层级：T0-T4")
    if stage == "r3c":
        return (r["text"] +
                "\n只输出一行：\n冲突：true/false")
    raise ValueError(f"未知 stage: {stage}")


def gate_metrics(records, stage: str):
    n = len(records)
    def acc(f):
        return sum(f(r) for r in records) / n if n else 0.0
    m = {"n": n}
    if stage == "r3a":
        status_acc = acc(lambda r: r["pred_status"] == r["gold"]["label"])
        sup = [r for r in records if r["gold"]["label"] == "SUPERSEDED"]
        sup_rej = (sum(r["pred_status"] == "SUPERSEDED" for r in sup) / len(sup)) if sup else 0.0
        wap = [r for r in records if r["pred_status"] != r["gold"]["label"]]
        wrong_authority_pref = (sum(1 for r in wap if r["pred_status"] == "CURRENT") / n) if n else 0.0
        crit = [r for r in records if r["gold"]["authority_type"] in ("T0", "T1")]
        crit_miss = sum(1 for r in crit if r["pred_status"] == "INVALID")
        invalid = sum(1 for r in records if r["pred_status"] == "INVALID")
        m.update({
            "operative_status_accuracy": round(status_acc, 4),
            "superseded_rejection": round(sup_rej, 4),
            "wrong_authority_preference_rate": round(wrong_authority_pref, 4),
            "critical_t0_t1_miss": crit_miss,
            "invalid_output": invalid,
            "n_superseded": len(sup),
        })
    elif stage == "r3b":
        auth_acc = acc(lambda r: r["pred_auth"] == r["gold"]["authority_type"])
        invalid = sum(1 for r in records if r["pred_auth"] == "INVALID")
        m.update({"authority_ranking_accuracy": round(auth_acc, 4),
                  "invalid_output": invalid})
    elif stage == "r3c":
        conf = [r for r in records if r["gold"]["material_conflict"]]
        conf_recall = (sum(r["pred_conflict"] == "true" for r in conf) / len(conf)) if conf else 0.0
        invalid = sum(1 for r in records if r["pred_conflict"] == "INVALID")
        m.update({"material_conflict_recall": round(conf_recall, 4),
                  "invalid_output": invalid,
                  "n_conflict": len(conf)})
    else:
        raise ValueError(f"未知 stage: {stage}")
    return m


GATE_THRESHOLDS = {
    "r3a": {
        "operative_status_accuracy": (0.90, ">="),
        "superseded_rejection": (0.95, ">="),
        "wrong_authority_preference_rate": (0.03, "<="),
        "critical_t0_t1_miss": (0, "<="),
        "invalid_output": (0, "<="),
    },
    "r3b": {
        "authority_ranking_accuracy": (0.90, ">="),
        "invalid_output": (0, "<="),
    },
    "r3c": {
        "material_conflict_recall": (0.95, ">="),
        "invalid_output": (0, "<="),
    },
}

STAGE_TARGET = {
    "r3a": "operative_status",
    "r3b": "authority",
    "r3c": "material_conflict",
}


def run_eval(name, adapter, records, stage: str, structured: bool = False):
    from mlx_lm import generate, load
    model, tokenizer = load(MODEL, adapter_path=adapter)
    out_records, t0 = [], time.time()
    for i, r in enumerate(records):
        f = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt_for(r, stage, structured)}], tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        out = generate(model, tokenizer, prompt=f, max_tokens=192, verbose=False)
        if stage == "r3a":
            ps = parse(out, stage, structured)[0]
            out_records.append({
                "query_id": r["query_id"], "candidate_id": r["candidate_id"],
                "gold": {"label": r["label"], "authority_type": r["authority_type"],
                         "material_conflict": r["material_conflict"]},
                "pred_status": ps, "raw": out})
        elif stage == "r3b":
            pa = parse(out, stage)[0]
            out_records.append({
                "query_id": r["query_id"], "candidate_id": r["candidate_id"],
                "gold": {"label": r["label"], "authority_type": r["authority_type"],
                         "material_conflict": r["material_conflict"]},
                "pred_auth": pa, "raw": out})
        elif stage == "r3c":
            pc = parse(out, stage)[0]
            out_records.append({
                "query_id": r["query_id"], "candidate_id": r["candidate_id"],
                "gold": {"label": r["label"], "authority_type": r["authority_type"],
                         "material_conflict": r["material_conflict"]},
                "pred_conflict": pc, "raw": out})
        if (i + 1) % 50 == 0:
            print(f"[r3_gate] {name}: {i+1}/{len(records)} ({time.time()-t0:.0f}s)", flush=True)
    del model
    return gate_metrics(out_records, stage), out_records


def verdict(metrics, stage: str):
    rows = []
    for k, (thr, op) in GATE_THRESHOLDS[stage].items():
        val = metrics.get(k, 0)
        ok = val >= thr if op == ">=" else val <= thr
        rows.append((k, val, thr, op, ok))
    all_ok = all(ok for *_, ok in rows)
    return rows, ("PASS" if all_ok else "FAIL")


def main():
    ap = argparse.ArgumentParser(description="R3 拆分 adapter Fresh gate eval")
    ap.add_argument("--stage", required=True, choices=["r3a", "r3b", "r3c"])
    ap.add_argument("--runs", nargs="+", default=None,
                    help="base 对照 + 本 stage adapter 名（默认只用本 stage adapter）")
    ap.add_argument("--adapter-dir", default=None,
                    help="覆盖本 stage adapter 目录；不改变 Gate 协议，用于独立实验目录")
    ap.add_argument("--structured", action="store_true",
                    help="R3A 结构化五行 completion（v3 注入 as_of+权威源链）；状态解析锚定最后一行效力状态")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true", help="静态校验，不加载模型")
    args = ap.parse_args()
    stage = args.stage
    out = args.out or f"{OUT_DIR}/{stage}_gate_eval.json"
    runs = args.runs or [stage]
    rows = []
    for p in sorted(glob.glob("data/r3/fresh/*.jsonl")):
        rows += [json.loads(l) for l in open(p, encoding="utf-8")]
    if args.limit:
        rows = rows[:args.limit]
    # 静态校验：数据 / adapter 路径 / 目标字段
    if not rows:
        print(f"[r3_gate] FAIL: data/r3/fresh/ 无数据", file=sys.stderr)
        return 1
    missing = [f for f in ("query_id", "candidate_id", "text", "label",
                           "authority_type", "material_conflict") if f not in rows[0]]
    if missing:
        print(f"[r3_gate] FAIL: fresh 数据缺字段 {missing}", file=sys.stderr)
        return 1
    adapter_dir = args.adapter_dir or ADAPTER_DIR[stage]
    adapter_ok = all(os.path.isdir(adapter_dir) for s in runs if s != "base")
    if not adapter_ok:
        print(f"[r3_gate] FAIL: adapter 路径不存在 -> "
              f"{[adapter_dir for s in runs if s != 'base' and not os.path.isdir(adapter_dir)]}",
              file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"[r3_gate] DRY-RUN PASS | stage={stage} target={STAGE_TARGET[stage]} "
              f"fresh={len(rows)} rows | adapters={ {s: (None if s == 'base' else adapter_dir) for s in runs} }")
        return 0
    results, details = {}, {}
    for name in runs:
        adapter = None if name == "base" else adapter_dir
        m, det = run_eval(name, adapter, rows, stage, structured=args.structured)
        results[name] = m
        details[name] = det
        print(f"[r3_gate] {name}: {json.dumps(m, ensure_ascii=False)}", flush=True)
    gate = None
    if stage in results:
        rows_v, v = verdict(results[stage], stage)
        gate = {"metrics": [{"metric": k, "value": v, "threshold": t, "op": o, "ok": ok}
                            for k, v, t, o, ok in rows_v], "verdict": v}
        print(f"\n=== R3{stage[1:].upper()} 门禁判定: {v} ===")
        for k, v, t, o, ok in rows_v:
            print(f"  {k}: {v} {o} {t} {'✓' if ok else '✗'}")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"stage": stage, "target": STAGE_TARGET[stage],
                   "results": results, "gate": gate, "details": details},
                  fh, ensure_ascii=False, indent=2)
    print(f"报告: {out}")
    return 0 if (gate is None or gate["verdict"] == "PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
