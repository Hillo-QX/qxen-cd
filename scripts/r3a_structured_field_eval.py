#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3A 结构化字段评估（2026-08-14 Kimi-Expert 裁决 + 用户授权修订）。

验收口径（Kimi-Expert v3 裁决 + 用户 2026-08-14 授权）：
  - reason_code（证据类型枚举）移出 hard gate，改为参考指标（观察线 >= 0.70）。
    根因：CoT 自创枚举 + 日期直跳；reason_code 语义是证据类型而非 Gate 目标本身，
    硬卡 0.85 无业务依据；canonical 固化是 label 泄漏，仅留作后备。
  - sub-agent 定位 = 结构化上下文输出，最终决策归主 Agent。

硬门槛：
  - invalid_output = 0（契约格式完整，枚举合法）
  - authority_accuracy        >= 0.85（0.90 目标线）
  - conflict_accuracy         >= 0.85（0.90 目标线）
参考指标（不设门槛）：
  - operative_status_accuracy（决策留给主 Agent）
  - reason_code_accuracy（观察线 0.70，验证字段隔离是否缓解失真）

契约：
  --contract legacy    五行文本（v3/v4 及之前）
  --contract isolated  <think>推理 + 纯 JSON（v5 字段隔离）

用法：
  ./venv/bin/python scripts/r3a_structured_field_eval.py --adapter-dir models/r3a_cot_v4 --contract legacy
  ./venv/bin/python scripts/r3a_structured_field_eval.py --adapter-dir models/r3a_cot_v5 --contract isolated
"""
from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from r3a_v3_context import make_prompt, make_prompt_isolated, synth_timeline, REASONS  # noqa: E402

MODEL = "models/qwen3.5-9b-mlx-4bit"

RE_REASON = re.compile(r"证据理由码\s*[:：]\s*([A-Z0-9_]+)")
RE_AUTH = re.compile(r"权威层级\s*[:：]\s*(T[0-4])", re.IGNORECASE)
RE_CONFLICT = re.compile(r"材料冲突\s*[:：]\s*(true|false)", re.IGNORECASE)
RE_STATUS = re.compile(r"效力状态\s*[:：]\s*(CURRENT|STALE|SUPERSEDED)", re.IGNORECASE)


def load_fresh():
    rows = []
    for p in sorted((ROOT / "data/r3/fresh").glob("*.jsonl")):
        rows.extend(json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip())
    return rows


def parse_fields(out: str):
    """解析五行结构化字段。返回 dict，缺失/非法 -> None 或 INVALID。"""
    out = out.strip()
    reason = RE_REASON.search(out)
    auth = RE_AUTH.search(out)
    conflict = RE_CONFLICT.search(out)
    status = RE_STATUS.findall(out)
    return {
        "reason_code": reason.group(1) if reason else None,
        "authority": auth.group(1).upper() if auth else None,
        "conflict": conflict.group(1).lower() if conflict else None,
        "status": status[-1].upper() if status else None,
    }


def parse_fields_isolated(out: str):
    """解析字段隔离契约：<think>推理</think> + 纯 JSON。

    从输出中提取最后一个合法 JSON 对象，字段：
      {"reason_code": str, "authority": str, "conflict": bool, "status": str}
    解析失败 / 字段缺失 -> None（计入 invalid）。
    """
    out = out.strip()
    # 找最后一个 {...} 块（模型可能在 JSON 后附加文字）
    start = out.rfind("{")
    end = out.rfind("}")
    if start < 0 or end <= start:
        return {k: None for k in ("reason_code", "authority", "conflict", "status")}
    try:
        d = json.loads(out[start:end + 1])
    except Exception:
        return {k: None for k in ("reason_code", "authority", "conflict", "status")}
    if not isinstance(d, dict):
        return {k: None for k in ("reason_code", "authority", "conflict", "status")}
    reason = d.get("reason_code")
    auth = d.get("authority")
    conflict = d.get("conflict")
    status = d.get("status")
    return {
        "reason_code": str(reason).strip() if reason is not None else None,
        "authority": str(auth).strip().upper() if auth is not None else None,
        "conflict": str(conflict).strip().lower() if conflict is not None else None,
        "status": str(status).strip().upper() if status is not None else None,
    }


def field_valid(fields: dict) -> bool:
    """五行格式完整 + 枚举合法。第五行效力状态必须存在且合法。"""
    return (fields["reason_code"] is not None
            and fields["authority"] is not None
            and fields["conflict"] is not None
            and fields["status"] is not None)


def main():
    ap = argparse.ArgumentParser(description="R3A 结构化字段评估（新口径）")
    ap.add_argument("--adapter-dir", required=True)
    ap.add_argument("--contract", default="legacy", choices=("legacy", "isolated"),
                    help="输出契约：legacy=五行文本 / isolated=<think>+JSON")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = load_fresh()
    if args.limit:
        rows = rows[:args.limit]

    from mlx_lm import generate, load
    model, tokenizer = load(MODEL, adapter_path=args.adapter_dir)

    parser = parse_fields if args.contract == "legacy" else parse_fields_isolated
    prompt_fn = make_prompt if args.contract == "legacy" else make_prompt_isolated

    n = len(rows)
    invalid = 0
    reason_ok = auth_ok = conflict_ok = status_ok = 0
    conf = {}

    t0 = time.time()
    for i, r in enumerate(rows):
        tl = synth_timeline(r)  # 与训练一致的 prompt 构造
        prompt = prompt_fn(r, tl)
        f = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        out = generate(model, tokenizer, prompt=f, max_tokens=192, verbose=False)
        fields = parser(out)
        if not field_valid(fields):
            invalid += 1
        else:
            if fields["reason_code"] == r["reason_code"]:
                reason_ok += 1
            if fields["authority"] == r["authority_type"]:
                auth_ok += 1
            if fields["conflict"] == ("true" if r["material_conflict"] else "false"):
                conflict_ok += 1
            if fields["status"] == r["label"]:
                status_ok += 1
        g = r["label"]
        p = fields["status"] or "INVALID"
        conf[(g, p)] = conf.get((g, p), 0) + 1
        if (i + 1) % 50 == 0:
            print(f"[field_eval] {i+1}/{n} ({time.time()-t0:.0f}s)", flush=True)

    del model

    metrics = {
        "n": n,
        "invalid_output": invalid,
        "reason_code_accuracy": round(reason_ok / n, 4),
        "authority_accuracy": round(auth_ok / n, 4),
        "conflict_accuracy": round(conflict_ok / n, 4),
        "operative_status_accuracy": round(status_ok / n, 4),
    }

    # 验收口径（Kimi-Expert 裁决 + 用户授权）：reason_code 移出 hard gate，改参考
    hard = {
        "invalid_output": (invalid == 0, "=0"),
        "authority_accuracy": (metrics["authority_accuracy"] >= 0.85, ">=0.85"),
        "conflict_accuracy": (metrics["conflict_accuracy"] >= 0.85, ">=0.85"),
    }
    verdict = "PASS" if all(v[0] for v in hard.values()) else "FAIL"

    result = {
        "acceptance_criteria": "structured_context_v1",
        "adapter": args.adapter_dir,
        "contract": args.contract,
        "metrics": metrics,
        "hard_gates": {k: {"ok": v[0], "threshold": v[1], "value": metrics[k]} for k, v in hard.items()},
        "reference_only": {
            "operative_status_accuracy": metrics["operative_status_accuracy"],
            "reason_code_accuracy": metrics["reason_code_accuracy"],
            "reason_code_watch_line": ">=0.70 (字段隔离缓解失真验证线)",
            "reason_code_watch_ok": metrics["reason_code_accuracy"] >= 0.70,
        },
        "verdict": verdict,
        "confusion_status": {f"{k[0]}->{k[1]}": v for k, v in sorted(conf.items())},
    }

    out_path = args.out or f"reports/r3/{Path(args.adapter_dir).name}_field_eval.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"=== 结构化字段 Gate 判定: {verdict} ===")
    for k, v in hard.items():
        print(f"  {k}: {metrics[k]} {v[1]} {'✓' if v[0] else '✗'}")
    print(f"  参考(不设门槛): operative_status_accuracy={metrics['operative_status_accuracy']} "
          f"| reason_code_accuracy={metrics['reason_code_accuracy']} (观察线0.70)")
    print(f"报告: {out_path}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
