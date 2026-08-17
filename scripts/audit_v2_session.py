#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v2 架构自动审计
===============

读取一个 Continue CLI 会话文件（~/.continue/sessions/*.json）+ LocalQwen 审计日志
（日志/local_qwen.log），对"v2 管道是否被真正执行"给出数据化判定。

审计项（每项 PASS / WARN / FAIL + 数据）：
    1. model_compliance   所有 assistant 消息的 usage.model 是否为预期 agent 模型
    2. boot_check         含真实工作（有 dispatch_next_task 调用）的会话，
                          是否先调了 local_health 和 dispatcher_health
    3. local_pipeline     local_* 工具调用次数 / FALLBACK 次数与比例（会话内 + 日志内）
    4. raw_bypass         未经蒸馏直接进入上下文的 raw 内容（非 local_* 工具的
                          大输出，默认阈值 2K 字符）——v2 的核心违规指标
    5. schema_limits      local_* 输出是否遵守 schema 硬限长
    6. token_economics    Token 经济引擎：蒸馏比 / 避免的重发 token / 节省金额估算 /
                          会话总账与 f0b59135 基线对照（费率 = 基线隐含混合价）

用法：
    ./venv/bin/python scripts/audit_v2_session.py                 # 审计最新会话
    ./venv/bin/python scripts/audit_v2_session.py <session.json>  # 审计指定会话
    ./venv/bin/python scripts/audit_v2_session.py --json          # 只输出机器可读报告

退出码：0 = 无 FAIL；1 = 存在 FAIL 项。报告同时写入 日志/audit/。
"""

import json
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.qxen_cd_audit import (load as load_qxen_audit_ledger,
                                       load_local_qwen as load_all_local_qwen,
                                       summarize_local_qwen as summarize_all_local_qwen,
                                       summarize_observable_paths)
except ModuleNotFoundError:  # direct execution: scripts/ is sys.path[0]
    from qxen_cd_audit import (load as load_qxen_audit_ledger,
                               load_local_qwen as load_all_local_qwen,
                               summarize_local_qwen as summarize_all_local_qwen,
                               summarize_observable_paths)

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = Path.home() / ".continue" / "sessions"
LOCAL_LOG = ROOT / "日志" / "local_qwen.log"
QXEN_AUDIT_LOG = ROOT / "日志" / "qxen_cd_audit.jsonl"
AUDIT_DIR = ROOT / "日志" / "audit"
BASELINE_DIR = Path.home() / ".kimi-code" / "cache" / "session-bootstrap"

EXPECTED_MODEL = os.environ.get("AUDIT_EXPECTED_MODEL", "deepseek-v4-flash")
RAW_BYPASS_WARN_CHARS = 2_000        # 单条非 local 工具输出超过即计入 bypass
RAW_BYPASS_FAIL_CHARS = 20_000       # bypass 总量超过即 FAIL
FALLBACK_WARN_RATE = 0.3

# f0b59135（2026-08-13，v1 下 DS 干完 T001-T002）基线
BASELINE = {
    "session": "f0b59135-4a06-48be-8c17-84231bd9c239",
    "ds_prompt_tokens": 5_247_396,
    "ds_completion_tokens": 47_926,
    "cost_usd": 5.67,
}
# 基线隐含混合单价（USD/token）：用真实账单反推，不依赖可能变动的官方报价。
IMPLIED_RATE = BASELINE["cost_usd"] / (
    BASELINE["ds_prompt_tokens"] + BASELINE["ds_completion_tokens"])

LOCAL_TOOLS = {"local_health", "local_distill", "local_summarize_files",
               "local_extract_failure", "local_classify"}
DISPATCHER_TOOLS = {"dispatcher_health", "dispatch_next_task", "request_decision"}
QXEN_NON_COMPARABLE_BASELINES = {"unknown", "none"}

# local_* 输出的硬限长（与 local_qwen_mcp.py 的 schema 对齐）
SCHEMA_LIMITS = {
    "local_distill": ("summary", 21),          # max_lines 20 + 截断标记
    "local_extract_failure": (None, None),     # 检查 test/expected/actual 键
    "local_classify": ("label", 1),
}


# ---------------------------------------------------------------- 数据提取

def load_session(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_tool_calls(session: dict):
    """产出 (tool_name, output_text, usage_model) 三元组，按历史顺序。"""
    for item in session.get("history", []):
        msg = item.get("message", {})
        model = (msg.get("usage") or {}).get("model")
        for tcs in item.get("toolCallStates") or []:
            fn = ((tcs.get("toolCall") or {}).get("function") or {})
            name = fn.get("name", "")
            out_text = ""
            for out in tcs.get("output") or []:
                out_text += str(out.get("content", ""))
            yield name, out_text, model


def parse_mcp_text(output_text: str) -> dict | None:
    """MCP 工具输出是 [{'type':'text','text':'<json>'}] 的字符串化 JSON，剥两层。"""
    try:
        arr = json.loads(output_text)
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            return json.loads(arr[0].get("text", ""))
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return None


def load_local_log(session_path: Path) -> tuple[list, str]:
    """读取 local_qwen.log，按会话文件创建~修改时间窗过滤。无日志时返回空。"""
    if not LOCAL_LOG.is_file():
        return [], "log file not found"
    try:
        created = datetime.fromtimestamp(session_path.stat().st_birthtime, timezone.utc)
        modified = datetime.fromtimestamp(session_path.stat().st_mtime, timezone.utc)
    except OSError:
        created = modified = None
    entries, skipped = [], 0
    for line in LOCAL_LOG.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
            t = datetime.fromisoformat(e["time"])
            if created and (created - _skew()) <= t <= (modified + _skew()):
                entries.append(e)
            else:
                skipped += 1
        except (json.JSONDecodeError, KeyError, ValueError):
            skipped += 1
    note = f"{len(entries)} entries in session window, {skipped} outside/unparsed"
    return entries, note


def load_qxen_usage_log(session_id: str) -> tuple[list, str]:
    """Load deduplicated QXEN usage observations for one session."""
    if not QXEN_AUDIT_LOG.is_file():
        return [], "qxen audit log not found"
    entries, skipped = [], 0
    seen = set()
    for line in QXEN_AUDIT_LOG.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if event.get("event_type") != "usage_observation":
            continue
        if event.get("session_id") != session_id:
            continue
        usage_id = str(event.get("usage_id", "")).strip()
        work_item_id = str(event.get("work_item_id", "")).strip()
        eval_window = str(event.get("eval_window", "")).strip()
        dedupe_key = (work_item_id, usage_id, eval_window)
        if not usage_id or dedupe_key in seen:
            skipped += 1
            continue
        seen.add(dedupe_key)
        entries.append(event)
    note = f"{len(entries)} usage observations, {skipped} skipped/unparsed"
    return entries, note


def load_all_qxen_usage_log() -> tuple[list, str]:
    """Load deduplicated QXEN usage observations across all sessions."""
    if not QXEN_AUDIT_LOG.is_file():
        return [], "qxen audit log not found"
    entries, skipped = [], 0
    seen = set()
    for line in QXEN_AUDIT_LOG.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if event.get("event_type") != "usage_observation":
            continue
        usage_id = str(event.get("usage_id", "")).strip()
        work_item_id = str(event.get("work_item_id", "")).strip()
        eval_window = str(event.get("eval_window", "")).strip()
        dedupe_key = (work_item_id, usage_id, eval_window)
        if not usage_id or dedupe_key in seen:
            skipped += 1
            continue
        seen.add(dedupe_key)
        entries.append(event)
    note = f"{len(entries)} usage observations, {skipped} skipped/unparsed"
    return entries, note


def _skew():
    from datetime import timedelta
    return timedelta(minutes=5)


# ---------------------------------------------------------------- 审计项

def audit_model_compliance(session: dict) -> dict:
    models = {}
    for item in session.get("history", []):
        msg = item.get("message", {})
        if msg.get("role") == "assistant":
            m = (msg.get("usage") or {}).get("model")
            if m:
                models[m] = models.get(m, 0) + 1
    bad = {m: n for m, n in models.items() if m != EXPECTED_MODEL}
    return {
        "verdict": "FAIL" if bad else "PASS",
        "expected": EXPECTED_MODEL,
        "by_model": models,
        "violations": bad,
    }


def audit_boot(session: dict) -> dict:
    tool_seq = [name for name, _, _ in iter_tool_calls(session)]
    did_work = "dispatch_next_task" in tool_seq
    first_local_health = tool_seq.index("local_health") if "local_health" in tool_seq else None
    first_disp_health = tool_seq.index("dispatcher_health") if "dispatcher_health" in tool_seq else None
    first_dispatch = tool_seq.index("dispatch_next_task") if did_work else None
    if not did_work:
        return {"verdict": "PASS", "note": "会话不含 dispatch 工作（纯对话），不要求 boot 自检",
                "local_health_called": first_local_health is not None}
    ok = (first_local_health is not None and first_disp_health is not None
          and first_local_health < first_dispatch and first_disp_health < first_dispatch)
    return {
        "verdict": "PASS" if ok else "FAIL",
        "local_health_called": first_local_health is not None,
        "dispatcher_health_called": first_disp_health is not None,
        "before_first_dispatch": ok,
        "tool_sequence_head": tool_seq[:8],
    }


def audit_local_pipeline(session: dict, log_entries: list) -> dict:
    from collections import Counter
    in_session = Counter()
    for name, _, _ in iter_tool_calls(session):
        if name in LOCAL_TOOLS:
            in_session[name] += 1
    fb = [e for e in log_entries if e.get("status") == "FALLBACK"]
    finished = [e for e in log_entries if e.get("status") in ("OK", "FALLBACK")]
    total = len(finished)
    rate = (len(fb) / total) if total else 0.0
    if not in_session and not total:
        verdict, note = "WARN", "会话中没有任何 local_* 调用"
    elif rate > FALLBACK_WARN_RATE:
        verdict, note = "WARN", f"FALLBACK 比例 {rate:.0%} 超过 {FALLBACK_WARN_RATE:.0%}"
    else:
        verdict, note = "PASS", ""
    return {
        "verdict": verdict, "note": note,
        "calls_in_session": dict(in_session),
        "log_calls": total, "log_fallbacks": len(fb),
        "fallback_rate": round(rate, 3),
        "log_input_chars": sum(e.get("input_chars", 0) for e in finished),
        "log_by_tool": dict(Counter(e.get("tool") for e in finished)),
    }


def audit_baseline(session_id: str) -> dict:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    path = BASELINE_DIR / f"audit_baseline_{digest}"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"session_id": session_id, "tool_calls_before_window": 0, "available": False}


def audit_raw_bypass(session: dict) -> dict:
    offenders = []
    historical = []
    total_chars = 0
    session_id = session.get("sessionId", "")
    baseline = audit_baseline(session_id)
    start_index = int(baseline.get("tool_calls_before_window", 0) or 0)
    for index, (name, out_text, _) in enumerate(iter_tool_calls(session)):
        if name in LOCAL_TOOLS or name in DISPATCHER_TOOLS or not name:
            continue
        n = len(out_text)
        if n > RAW_BYPASS_WARN_CHARS:
            target = offenders if index >= start_index else historical
            target.append({"tool": name, "chars": n, "tool_index": index})
            if index >= start_index:
                total_chars += n
    strict_budget = os.environ.get("QXEN_STRICT_AUDIT", "0") == "1"
    verdict = ("FAIL" if total_chars > RAW_BYPASS_FAIL_CHARS else ("WARN" if offenders else "PASS")) if strict_budget else ("WARN" if offenders else "PASS")
    return {
        "verdict": verdict,
        "bypass_outputs": len(offenders),
        "bypass_chars": total_chars,
        "est_bypass_tokens": total_chars // 4,
        "threshold_chars": RAW_BYPASS_WARN_CHARS,
        "fail_threshold_chars": RAW_BYPASS_FAIL_CHARS,
        "worst": sorted(offenders, key=lambda o: -o["chars"])[:5],
        "historical_bypass_outputs": len(historical),
        "historical_bypass_chars": sum(item["chars"] for item in historical),
        "baseline_tool_calls": start_index,
        "baseline_available": bool(baseline.get("available", True)),
        "note": f"当前窗口 {verdict}；基线前 raw_bypass 仅计入历史债务，不参与当前 PASS/FAIL",
    }


def audit_schema_limits(session: dict) -> dict:
    violations = []
    checked = 0
    for name, out_text, _ in iter_tool_calls(session):
        if name not in LOCAL_TOOLS or name == "local_health":
            continue
        payload = parse_mcp_text(out_text)
        if not payload:
            continue
        checked += 1
        if name == "local_distill":
            lines = str(payload.get("summary", "")).splitlines()
            if len(lines) > SCHEMA_LIMITS["local_distill"][1]:
                violations.append({"tool": name, "lines": len(lines)})
        elif name == "local_extract_failure":
            if not all(k in payload for k in ("test", "expected", "actual")):
                violations.append({"tool": name, "reason": "missing keys"})
        elif name == "local_classify":
            if payload.get("label") not in ("PIN", "DROP", "KEEP", "VERBATIM"):
                violations.append({"tool": name, "label": payload.get("label")})
    return {
        "verdict": "FAIL" if violations else "PASS",
        "checked_outputs": checked,
        "violations": violations,
    }


def summarize_qxen_usage_entries(usage_entries: list[dict], note: str, *,
                                 note_if_empty: str) -> dict:
    """Summarize deduplicated QXEN usage observations."""
    if not usage_entries:
        return {
            "verdict": "N/A",
            "note": note_if_empty,
            "qxen_data": "absent",
            "qxen_calls": 0,
            "qxen_input_chars": 0,
            "qxen_output_chars": 0,
            "qxen_avoided_tokens": 0,
            "qxen_estimated_avoided_tokens": 0,
            "qxen_est_savings_usd": 0.0,
            "qxen_estimated_savings_usd": 0.0,
            "per_usage": [],
            "log_note": note,
        }

    qxen_calls = len(usage_entries)
    total_in = total_out = 0
    avoided_tokens = estimated_avoided_tokens = 0
    savings_usd = estimated_savings_usd = 0.0
    success_count = comparable_count = 0
    per_usage = []

    for event in usage_entries:
        source_chars = int(event.get("source_chars") or 0)
        payload_chars = int(event.get("payload_chars") or 0)
        total_in += source_chars
        total_out += payload_chars
        outcome = event.get("outcome", "unknown")
        baseline_mode = event.get("baseline_mode", "unknown")
        estimated = bool(event.get("estimated", False))
        baseline_gpt = event.get("baseline_gpt_tokens")
        qxen_gpt = int(event.get("qxen_gpt_tokens") or 0)
        qxen_local = int(event.get("qxen_local_tokens") or 0)
        gpt_review = int(event.get("gpt_review_tokens") or 0)
        fallback_replay = int(event.get("fallback_replay_gpt_tokens") or 0)
        comparable = (
            outcome == "success"
            and baseline_mode not in QXEN_NON_COMPARABLE_BASELINES
            and baseline_gpt is not None
        )
        avoided = 0
        if comparable:
            success_count += 1
            comparable_count += 1
            effective_qxen_tokens = qxen_gpt + qxen_local + gpt_review + fallback_replay
            avoided = max(0, int(baseline_gpt) - effective_qxen_tokens)
            if estimated:
                estimated_avoided_tokens += avoided
                estimated_savings_usd += avoided * IMPLIED_RATE
            else:
                avoided_tokens += avoided
                savings_usd += avoided * IMPLIED_RATE
        per_usage.append({
            "usage_id": event.get("usage_id"),
            "work_item_id": event.get("work_item_id"),
            "outcome": outcome,
            "baseline_mode": baseline_mode,
            "estimated": estimated,
            "avoided_tokens": avoided,
        })

    ratio = (total_out / total_in) if total_in else None
    if comparable_count == 0:
        verdict, verdict_note = "N/A", "存在 QXEN usage，但无可比 baseline-success 样本"
    elif ratio is not None and ratio >= 0.5:
        verdict, verdict_note = "WARN", f"QXEN 压缩比 {ratio:.1%} 偏弱（输出/输入 ≥ 50%）"
    else:
        verdict, verdict_note = "PASS", ""
    return {
        "verdict": verdict,
        "note": verdict_note,
        "qxen_data": "present",
        "qxen_calls": qxen_calls,
        "success_calls": success_count,
        "comparable_calls": comparable_count,
        "qxen_input_chars": total_in,
        "qxen_output_chars": total_out,
        "qxen_ratio": (f"{ratio:.1%}" if ratio is not None else "n/a"),
        "qxen_avoided_tokens": avoided_tokens,
        "qxen_estimated_avoided_tokens": estimated_avoided_tokens,
        "qxen_est_savings_usd": round(savings_usd, 4),
        "qxen_estimated_savings_usd": round(estimated_savings_usd, 4),
        "log_note": note,
        "per_usage": per_usage[:20],
    }


def audit_qxen_economics(session: dict) -> dict:
    """Summarize QXEN economics from the dedicated audit ledger only."""
    session_id = session.get("sessionId", "")
    usage_entries, note = load_qxen_usage_log(session_id)
    summary = summarize_qxen_usage_entries(
        usage_entries,
        note,
        note_if_empty="无 QXEN usage 记录：尚未接入或本会话未使用",
    )
    ledger_rows = [r for r in load_qxen_audit_ledger(QXEN_AUDIT_LOG)
                   if r.get("session_id") == session_id]
    summary["observable_path_accounting"] = summarize_observable_paths(ledger_rows)
    return summary


def historical_qxen_economics() -> dict:
    """Summarize cumulative QXEN economics conservatively across history."""
    usage_entries, note = load_all_qxen_usage_log()
    summary = summarize_qxen_usage_entries(
        usage_entries,
        note,
        note_if_empty="无历史 QXEN usage 记录",
    )
    summary["scope"] = "historical_cumulative"
    observable_rows = [r for r in load_qxen_audit_ledger(QXEN_AUDIT_LOG)
                       if r.get("workspace")]
    summary["observable_path_accounting"] = summarize_observable_paths(observable_rows)
    summary["local_qwen_observable_path_accounting"] = summarize_all_local_qwen(
        load_all_local_qwen()).get("observable_path_accounting", {})
    summary["primary_savings_metric"] = "observable path input - returned chars - MCP reread chars"
    return summary


def audit_token_economics(session: dict, log_entries: list, baseline: dict | None = None,
                          qxen_summary: dict | None = None) -> dict:
    """Token 经济引擎：蒸馏省了多少，用数据说话。

    经济模型（agent 循环的 prompt 费用 = 重发费）：
        每次 local_* 调用让 (input_chars - output_chars) 的 raw 内容免于进入历史；
        免于进入的字节在之后的每一次 LLM 调用中都不再重发计费。
        avoided_retransmit_tokens = Σ (avoided_chars / 4) × 该调用之后的剩余调用数。

    input/output 优先取 local_qwen.log 的真实计量（按调用顺序与会话内 local_*
    调用一一对应）；无日志时退化为会话内的 arguments / output 长度估计。
    费率用 f0b59135 基线隐含混合单价，不依赖官方报价。
    """
    baseline = baseline or {}
    history_start = int(baseline.get("history_items_before_window", 0) or 0)
    history_window = session.get("history", [])[history_start:]
    # 1. 当前窗口：LLM 调用总数 + 每个 local_* 调用的位置与字节量
    llm_calls = 0
    local_calls = []  # (position, name, args_chars, output_chars)
    for item in history_window:
        msg = item.get("message", {})
        if msg.get("role") == "assistant" and msg.get("usage"):
            llm_calls += 1
        for tcs in item.get("toolCallStates") or []:
            fn = ((tcs.get("toolCall") or {}).get("function") or {})
            name = fn.get("name", "")
            if name not in LOCAL_TOOLS or name == "local_health":
                continue
            out_text = "".join(str(o.get("content", ""))
                               for o in (tcs.get("output") or []))
            local_calls.append({
                "position": llm_calls, "tool": name,
                "args_chars": len(str(fn.get("arguments", ""))),
                "output_chars": len(out_text),
            })

    # 2. 与日志按顺序配对，取真实 input_chars
    ok_entries = [e for e in log_entries
                  if e.get("tool") != "local_health" and e.get("status") == "OK"]
    for call, entry in zip(local_calls, ok_entries):
        call["input_chars"] = entry.get("input_chars", call["args_chars"])
        call["log_output_chars"] = entry.get("output_chars")

    # 3. 逐调用计算避免的重发 token
    total_in = total_out = avoided_tokens = 0
    per_call = []
    for call in local_calls:
        in_chars = call.get("input_chars") or call["args_chars"]
        out_chars = call.get("log_output_chars") or call["output_chars"]
        avoided_chars = max(0, in_chars - out_chars)
        remaining = max(0, llm_calls - call["position"])
        avoided = avoided_chars // 4 * remaining
        avoided_tokens += avoided
        total_in += in_chars
        total_out += out_chars
        per_call.append({"tool": call["tool"], "input_chars": in_chars,
                         "output_chars": out_chars, "remaining_calls": remaining,
                         "avoided_tokens": avoided})

    # 4. 会话总账
    prompt = completion = 0
    for item in history_window:
        u = item.get("message", {}).get("usage") or {}
        prompt += u.get("prompt_tokens", 0)
        completion += u.get("completion_tokens", 0)

    ratio = (total_out / total_in) if total_in else None
    est_savings = avoided_tokens * IMPLIED_RATE
    est_cost = (prompt + completion) * IMPLIED_RATE
    qxen_summary = qxen_summary or {}
    qxen_avoided_tokens = int(qxen_summary.get("qxen_avoided_tokens", 0) or 0)
    qxen_estimated_avoided_tokens = int(qxen_summary.get("qxen_estimated_avoided_tokens", 0) or 0)
    qxen_est_savings_usd = float(qxen_summary.get("qxen_est_savings_usd", 0.0) or 0.0)
    qxen_estimated_savings_usd = float(qxen_summary.get("qxen_estimated_savings_usd", 0.0) or 0.0)
    if not local_calls and qxen_summary.get("qxen_data") != "present":
        verdict, note = "N/A", "无 local_* 调用：快速路径无需蒸馏，经济数据不适用"
    elif ratio is not None and ratio >= 0.5:
        verdict, note = "WARN", f"蒸馏比 {ratio:.1%} 偏弱（输出/输入 ≥ 50%）"
    else:
        verdict, note = "PASS", ""
    return {
        "verdict": verdict, "note": note,
        "llm_calls": llm_calls,
        "local_calls": len(local_calls),
        "distill_input_chars": total_in,
        "distill_output_chars": total_out,
        "distill_ratio": (f"{ratio:.1%}" if ratio is not None else "n/a"),
        "avoided_retransmit_tokens": avoided_tokens,
        "est_savings_usd": round(est_savings, 4),
        "qxen_calls": int(qxen_summary.get("qxen_calls", 0) or 0),
        "qxen_input_chars": int(qxen_summary.get("qxen_input_chars", 0) or 0),
        "qxen_output_chars": int(qxen_summary.get("qxen_output_chars", 0) or 0),
        "qxen_avoided_tokens": qxen_avoided_tokens,
        "qxen_estimated_avoided_tokens": qxen_estimated_avoided_tokens,
        "qxen_est_savings_usd": round(qxen_est_savings_usd, 4),
        "qxen_estimated_savings_usd": round(qxen_estimated_savings_usd, 4),
        "combined_avoided_tokens": avoided_tokens + qxen_avoided_tokens,
        "combined_estimated_avoided_tokens": qxen_estimated_avoided_tokens,
        "combined_est_savings_usd": round(est_savings + qxen_est_savings_usd, 4),
        "combined_estimated_savings_usd": round(qxen_estimated_savings_usd, 4),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "current_window_history_items": len(history_window),
        "current_window_history_start": history_start,
        "historical_prompt_tokens": int(baseline.get("prompt_tokens_before_window", 0) or 0),
        "historical_completion_tokens": int(baseline.get("completion_tokens_before_window", 0) or 0),
        "session_total_prompt_tokens": prompt + int(baseline.get("prompt_tokens_before_window", 0) or 0),
        "session_total_completion_tokens": completion + int(baseline.get("completion_tokens_before_window", 0) or 0),
        "window_source": "session_start_baseline" if baseline.get("history_items_before_window") is not None else "full_session_legacy",
        "est_cost_usd": round(est_cost, 4),
        "baseline_f0b59135": BASELINE,
        "prompt_vs_baseline": (f"{prompt / BASELINE['ds_prompt_tokens']:.1%}"
                               if prompt else "n/a"),
        "rate_note": f"费率为基线隐含混合价 ${IMPLIED_RATE:.2e}/token",
        "per_call": per_call[:20],
    }


# ---------------------------------------------------------------- 主流程

def audit(session_path: Path) -> dict:
    session = load_session(session_path)
    log_entries, log_note = load_local_log(session_path)
    qxen_summary = audit_qxen_economics(session)
    qxen_historical = historical_qxen_economics()
    checks = {
        "model_compliance": audit_model_compliance(session),
        "boot_check": audit_boot(session),
        "local_pipeline": audit_local_pipeline(session, log_entries),
        "raw_bypass": audit_raw_bypass(session),
        "schema_limits": audit_schema_limits(session),
        "qxen_economics": qxen_summary,
        "token_economics": audit_token_economics(
            session,
            log_entries,
            audit_baseline(session.get("sessionId", session_path.stem)),
            qxen_summary,
        ),
    }
    fails = [k for k, v in checks.items() if v["verdict"] == "FAIL"]
    warns = [k for k, v in checks.items() if v["verdict"] == "WARN"]
    return {
        "session_id": session.get("sessionId", session_path.stem),
        "workspace": session.get("workspaceDirectory"),
        "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall": "FAIL" if fails else ("WARN" if warns else "PASS"),
        "fails": fails, "warns": warns,
        "log_window": log_note,
        "historical_rollups": {
            "qxen_economics": qxen_historical,
        },
        "checks": checks,
    }


def print_human(report: dict) -> None:
    icon = {"PASS": "✓", "WARN": "!", "FAIL": "✗", "N/A": "-"}
    print(f"session: {report['session_id']}  overall: {report['overall']}")
    print(f"  log window: {report['log_window']}")
    for name, c in report["checks"].items():
        print(f"  [{icon[c['verdict']]}] {name}: {c['verdict']}")
        for k, v in c.items():
            if k in ("verdict", "note", "worst", "tool_sequence_head") and not v:
                continue
            if k == "baseline_f0b59135":
                v = f"prompt={v['ds_prompt_tokens']:,} cost=${v['cost_usd']}"
            print(f"      {k}: {v}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    json_only = "--json" in sys.argv
    if args:
        session_path = Path(args[0])
    else:
        candidates = [p for p in SESSIONS_DIR.glob("*.json") if p.name != "sessions.json"]
        if not candidates:
            print("no session files found", file=sys.stderr)
            return 1
        session_path = max(candidates, key=lambda p: p.stat().st_mtime)

    report = audit(session_path)

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = AUDIT_DIR / f"audit_{report['session_id'][:8]}_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
        print(f"report: {out}")
    return 1 if report["overall"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
