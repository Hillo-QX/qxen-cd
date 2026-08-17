#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Global QXEN-CD MCP server.

QXEN-CD is registered globally in Codex, while its model assets remain in
this workspace. The MCP layer exposes the production contract; the skill is
only the usage policy. Health/capabilities/compaction are deterministic and
do not load MLX. Evidence processing loads the clean v1 adapter on demand.
"""
from __future__ import annotations

import json
import hashlib
import re
import os
import sys
import time
import contextlib
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
from qxen_cd_compact import compact  # noqa: E402
from qxen_pdf_preflight import extract_pdf_text, preflight_pdf  # noqa: E402
from qxen_cd_audit import (  # noqa: E402
    DEFAULT_LOG as AUDIT_LOG,
    load as load_audit,
    estimate_tokens,
    record_processing,
    record_capsule_use,
    record_path_distill,
    record_source_retrieval,
    record_usage,
    register_work_item,
    summarize_local_qwen,
    load_local_qwen,
    summarize as summarize_audit,
)
from qxen_cd_runtime import (  # noqa: E402
    ADAPTER,
    BASE_MODEL,
    CAPABILITIES,
    TASK_INSTRUCTIONS,
    QXEN_PRIMARY_TASKS,
    LOCAL_QWEN_PRIMARY_TASKS,
    WORK_ROUTING,
    append_audit,
    infer_one,
    route_backend,
)
from qxen_v1_guard import guard_text  # noqa: E402
from codex_workflow_bootstrap import discover as discover_workflow  # noqa: E402
import local_qwen_mcp as local_qwen  # noqa: E402
DETECTION_TASKS_PATH = ROOT / "configs" / "qxen_detection_tasks_v1.json"

MCP_LOG = Path(os.environ.get("QXEN_CD_MCP_LOG", str(ROOT / "日志" / "qxen_cd_mcp.log")))
SERVER_VERSION = "qxen-cd-mcp-v1"
mcp = FastMCP("qxen-cd")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit_text_input(inline_text: str, source_path: str = "") -> tuple[str, dict[str, Any]]:
    """Resolve audit text inside the MCP process so GPT can pass only a path."""
    if inline_text:
        return inline_text, {"input_mode": "inline"}
    if not source_path:
        raise ValueError("missing_inline_text_or_source_path")
    path = Path(source_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    raw = path.read_bytes()
    return raw.decode("utf-8", errors="replace"), {
        "input_mode": "local_path", "source_path": str(path),
        "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw),
    }


def _audit(tool: str, status: str, **fields: Any) -> None:
    try:
        MCP_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": _now(), "server": "qxen-cd", "tool": tool,
                  "status": status, **fields}
        with MCP_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _capsule_update(capsule_id: str, action: str, reason: str = "", latency: str = "",
                    claim_token: str = "", worker_id: str = "",
                    result_payload: dict | None = None) -> dict:
    """Run one atomic capsule transition and return callback telemetry."""
    started = time.perf_counter()
    if not capsule_id:
        return {"ok": True, "claim_token": "", "callback_latency_s": 0.0}
    try:
        from response_capsule import transition_status
        with contextlib.redirect_stdout(io.StringIO()):
            result = transition_status(capsule_id, action, reason, latency,
                                       claim_token=claim_token, worker_id=worker_id,
                                       result_payload=result_payload)
        result["callback_latency_s"] = round(time.perf_counter() - started, 4)
        if not result.get("ok"):
            result["error"] = f"capsule_status_{action}_error:{result.get('reason', 'transition_rejected')}"
        return result
    except Exception as exc:
        return {"ok": False, "claim_token": "",
                "callback_latency_s": round(time.perf_counter() - started, 4),
                "error": f"capsule_status_{action}_error:{str(exc)[:200]}"}


def _stable_capsule_id(result: dict) -> str:
    capsule = (result.get("gpt_context") or {}).get("capsule")
    if not isinstance(capsule, dict):
        return ""
    existing = capsule.get("capsule_id")
    if isinstance(existing, str) and existing.strip():
        return existing
    canonical = json.dumps(capsule, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "EC-HASH-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _auto_work_item_id(source: str, task: str, evidence: str) -> str:
    """为未显式登记的调用派生稳定业务工作项 id（同材料同任务幂等）。"""
    digest = hashlib.sha256(
        f"{source}|{task}|{evidence[:256]}".encode("utf-8")).hexdigest()[:10]
    return f"auto-{digest}"


def _gpt_payload_chars(result: dict[str, Any]) -> int:
    """Count only the payload GPT may consume, not MCP/runtime metadata."""
    if result.get("guard_status") == "FALLBACK":
        return 0
    context = result.get("gpt_context")
    if isinstance(context, dict):
        return len(json.dumps(context, ensure_ascii=False))
    return 0


def _ensure_business_audit(work_item_id: str, task: str, source: str,
                           evidence: str, result: dict, workspace: str,
                           session_id: str) -> None:
    """自动埋点：登记业务工作项（未登记时）+ 记录 baseline/QXEN 成对观测。

    baseline 采用 direct_gpt 反事实（GPT 直接读原文）按 chars/4 估计，
    estimated=True；FALLBACK 路径 GPT 需读原文重放，qxen_gpt_tokens 仍记
    输出侧估计，outcome=fallback，不粉饰节省。任何异常不得影响主流程。
    """
    try:
        registered = {r.get("work_item_id") for r in load_audit(AUDIT_LOG)
                      if r.get("event_type") == "work_item_registered"}
        if work_item_id not in registered:
            register_work_item(
                work_item_id, f"auto:{task}:{(source or '')[:60]}",
                origin="upstream_agent", baseline_required=True,
                baseline_mode="direct_gpt", workspace=workspace,
                session_id=session_id, path=AUDIT_LOG)
        src_chars = len(source or "") + len(evidence or "")
        fallback = result.get("guard_status") == "FALLBACK"
        payload_chars = _gpt_payload_chars(result)
        usage_id = "auto-" + hashlib.sha256(
            f"{work_item_id}|{time.time()}|{src_chars}".encode("utf-8")
        ).hexdigest()[:16]
        record_usage(
            work_item_id, usage_id, baseline_mode="direct_gpt",
            eval_window="auto:" + _now()[:10],
            outcome="fallback" if fallback else "success",
            baseline_gpt_tokens=estimate_tokens(src_chars),
            qxen_gpt_tokens=estimate_tokens(payload_chars),
            qxen_local_tokens=0, source_chars=src_chars, payload_chars=payload_chars,
            pipeline="process", baseline_scope="source_plus_evidence",
            fallback_replay_gpt_tokens=estimate_tokens(src_chars) if fallback else 0,
            capsule_id=str(result.get("capsule_id") or ""), estimated=True,
            workspace=workspace, session_id=session_id,
            note="auto-instrumented: baseline=direct_gpt raw-passthrough est(chars/4)",
            path=AUDIT_LOG)
    except Exception:
        pass


@mcp.tool()
async def qxen_cd_health() -> dict:
    """检查全局 QXEN-CD MCP、v1 模型资产、护栏和 compacting 是否可用。"""
    guard_path = ROOT / "scripts" / "qxen_v1_guard.py"
    compact_path = ROOT / "scripts" / "qxen_cd_compact.py"
    ok = BASE_MODEL.is_dir() and ADAPTER.is_dir() and guard_path.is_file() and compact_path.is_file()
    result = {
        "status": "OK" if ok else "ERROR",
        "server": "qxen-cd",
        "server_version": SERVER_VERSION,
        "scope": "global-codex",
        "model": str(BASE_MODEL),
        "adapter": str(ADAPTER),
        "guard": str(guard_path),
        "compactor": str(compact_path),
        "model_assets_present": BASE_MODEL.is_dir() and ADAPTER.is_dir(),
        "mcp_log": str(MCP_LOG),
        "time": _now(),
    }
    _audit("qxen_cd_health", result["status"], model_assets_present=result["model_assets_present"])
    return result


@mcp.tool()
async def qxen_cd_bootstrap(workspace: str = "", paths: list[str] | None = None,
                            max_files: int = 16) -> dict:
    """Bootstrap a Codex session with distilled handoff/state/log context.

    Discovery is deterministic. When no MLX/LoRA training process is present,
    selected files are immediately summarized by LocalQwen. During training,
    only deterministic discovery runs and LocalQwen inference is disabled.
    """
    result = discover_workflow(workspace, paths, max_files)
    summaries = []
    if result.get("status") == "OK" and not result.get("training_protected", False):
        health = await local_qwen.local_health()
        result["local_qwen_health"] = {
            "status": health.get("status"),
            "cached": health.get("cached", False),
            "next_probe_after_s": health.get("next_probe_after_s"),
        }
        if health.get("status") != "OK":
            result["startup_summaries"] = []
            result["bootstrap_fallback"] = "local_qwen_health_error"
            result["local_qwen_allowed"] = False
            result.update({"server": "qxen-cd", "scope": "global-codex",
                           "workflow": "bootstrap_discover_then_distill",
                           "semantic_owner": "GPT-main-agent",
                           "deterministic_owner": "project-python-engine"})
            _audit("qxen_cd_bootstrap", "FALLBACK",
                   workspace=result.get("workspace", ""),
                   material_count=len(result.get("materials", [])),
                   training_protected=False)
            record_processing(
                task="bootstrap", origin="system_required", pipeline="bootstrap",
                baseline_scope="bootstrap_manifest",
                source_chars=len(json.dumps(result.get("materials", []), ensure_ascii=False)),
                qxen_output_chars=0, guard_status="FALLBACK", fallback=True,
                workspace=workspace, session_id="", path=AUDIT_LOG,
            )
            return result
        selected = result.get("materials", [])
        # Startup must stay bounded: keep the handoff plus the newest state and
        # at most two newest logs. Full discovery remains available in the
        # returned manifest for later targeted work.
        handoff = [x for x in selected if x.get("category") == "handoff"][:1]
        checkpoints = sorted(
            [x for x in selected if x.get("category") == "checkpoint"],
            key=lambda x: x.get("modified", 0), reverse=True,
        )[:1]
        logs = sorted(
            [x for x in selected if x.get("category") == "log"],
            key=lambda x: x.get("modified", 0), reverse=True,
        )[:2]
        selected = handoff + checkpoints + logs
        summary_paths = [item["path"] for item in selected
                         if item.get("category") in {"handoff", "checkpoint"}]
        if summary_paths:
            summary_result = await local_qwen.local_summarize_files(
                summary_paths, lines_per_file=3)
            summaries.extend(summary_result.get("summaries", []))
        for item in selected:
            if item.get("category") != "log":
                continue
            try:
                content = Path(item["path"]).read_text(encoding="utf-8", errors="replace")[-200_000:]
            except OSError as exc:
                summaries.append({"path": item["path"], "status": "ERROR",
                                  "reason": str(exc)})
                continue
            log_result = await local_qwen.local_research_log_distill(
                content, task="启动前读取长日志：提取状态、失败点、关键指标和下一步")
            summaries.append({"path": item["path"], "category": "log",
                              "distill": log_result})
    result["startup_summaries"] = summaries
    result["local_qwen_allowed"] = bool(
        result.get("local_qwen_allowed", not result.get("training_protected", False))
    )
    result["startup_protocol"] = {
        "handoff_checkpoint_logs": "distilled_before_main_agent_context",
        "candidate_generation": "local_qwen_advisory_only",
        "expression_review": "local_qwen_advisory_only",
        "deterministic_metrics": "project_python_engine",
        "strategy_decision": "GPT-main-agent",
    }
    result.update({"server": "qxen-cd", "scope": "global-codex",
                   "workflow": "bootstrap_discover_then_distill",
                   "local_qwen_allowed": not result.get("training_protected", False),
                   "semantic_owner": "GPT-main-agent",
                   "deterministic_owner": "project-python-engine"})
    _audit("qxen_cd_bootstrap", result.get("status", "ERROR"),
           workspace=result.get("workspace", ""),
           material_count=len(result.get("materials", [])),
           training_protected=result.get("training_protected", False),
           fallback_reason=result.get("fallback_reason", ""),
           model_called=result.get("model_called", False))
    record_processing(
        task="bootstrap", origin="system_required", pipeline="bootstrap",
        baseline_scope="bootstrap_manifest",
        source_chars=len(json.dumps(result.get("materials", []), ensure_ascii=False)),
        qxen_output_chars=len(json.dumps(result.get("startup_summaries", []), ensure_ascii=False)),
        guard_status="BOOTSTRAP", fallback=bool(result.get("bootstrap_fallback")),
        workspace=workspace, session_id="", path=AUDIT_LOG,
    )
    return result


@mcp.tool()
async def qxen_cd_capabilities() -> dict:
    """返回 QXEN-CD 可委派任务、建议字段和系统独占边界。"""
    result = {
        "status": "OK",
        "server": "qxen-cd",
        "capabilities": CAPABILITIES,
        "tasks": sorted(TASK_INSTRUCTIONS),
        "model_policy": "clean_v1_lora_only",
        "final_decision_owner": "GPT-main-agent",
        "requires_guard": "task_scoped",
        "guard_modes": {
            "qxen_longtext_distill": {
                "requires_guard": False,
                "mode": "lightweight_json",
                "key_evidence_required": False,
            },
            "faithful_chunk_distill": {
                "requires_guard": False,
                "mode": "lightweight_json",
                "key_evidence_required": False,
            },
            "high_risk_evidence": {
                "requires_guard": True,
                "mode": "full_deterministic",
                "key_evidence_required": True,
            },
        },
        "routing": {
            "qxen_primary": sorted(QXEN_PRIMARY_TASKS),
            "local_qwen_primary": sorted(LOCAL_QWEN_PRIMARY_TASKS),
            "work_types": WORK_ROUTING,
            "policy": "longtext uses lightweight advisory validation; high-risk evidence keeps full deterministic Guard",
        },
        "source_access": {
            "ingest": "qxen_cd_longtext_distill(source_path=..., evidence='')",
            "targeted_retrieval": "qxen_cd_source_slice(raw_pointer=..., query=... or start_line/end_line)",
            "policy": "capsule_first_targeted_retrieval",
        },
        "time": _now(),
    }
    _audit("qxen_cd_capabilities", "OK")
    return result


@mcp.tool()
async def qxen_cd_route(task: str, content_type: str = "") -> dict:
    """返回全局任务路由建议：证据语义优先 QXEN-CD，技术型任务优先 LocalQwen。"""
    route = route_backend(task, content_type)
    result = {"status": "OK", "server": "qxen-cd", "scope": "global-codex",
              "task": task, "content_type": content_type, **route,
              "must_not_change": ["final_decision", "audit_denominator", "gate"]}
    _audit("qxen_cd_route", "OK", backend=route["backend"], task=task)
    return result


@mcp.tool()
async def qxen_cd_detection_tasks() -> dict:
    """返回 QXEN-CD 检测任务契约，不执行文件修改或最终裁决。"""
    try:
        contract = json.loads(DETECTION_TASKS_PATH.read_text(encoding="utf-8"))
        return {"status": "OK", "server": "qxen-cd", "authority": "specification_only",
                "contract": contract}
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "FALLBACK", "server": "qxen-cd",
                "fallback_reason": "detection_contract_unavailable:" + str(exc)}


@mcp.tool()
async def qxen_cd_detection_plan(task_ids: list[str], target: str,
                                 workspace: str = "", read_only: bool = True) -> dict:
    """根据检测类型生成只读执行计划；不读取目标、不修改文件。"""
    try:
        contract = json.loads(DETECTION_TASKS_PATH.read_text(encoding="utf-8"))
        by_id = {item["id"]: item for item in contract["tasks"]}
        unknown = [task_id for task_id in task_ids if task_id not in by_id]
        if unknown or not read_only:
            return {"status": "FALLBACK", "server": "qxen-cd",
                    "fallback_reason": "unknown_task_or_non_read_only_request",
                    "unknown_task_ids": unknown, "read_only_required": True}
        selected = [by_id[task_id] for task_id in task_ids]
        plan = []
        for item in selected:
            plan.append({
                "task_id": item["id"],
                "target": target,
                "workspace": workspace,
                "mode": "read_only",
                "phase_1_qxen": item["qxen_scope"],
                "phase_2_deterministic": item["deterministic_checks"],
                "phase_3_escalation": item["escalate"],
                "forbidden": item["forbidden"],
            })
        return {"status": "OK", "server": "qxen-cd", "authority": "plan_only",
                "target": target, "read_only": True, "plan": plan,
                "requires_gpt_review": True}
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        return {"status": "FALLBACK", "server": "qxen-cd", "fallback_reason": str(exc)}


@mcp.tool()
async def qxen_cd_audit_register(work_item_id: str, title: str,
                                 origin: str = "upstream_agent",
                                 baseline_required: bool | None = True,
                                 baseline_mode: str = "unknown",
                                 parent_task_id: str = "", workspace: str = "",
                                 session_id: str = "") -> dict:
    """登记业务工作项，区分原始任务与 QXEN-CD 新增/审计任务。"""
    try:
        result = register_work_item(work_item_id, title, origin=origin,
                                    baseline_required=baseline_required,
                                    baseline_mode=baseline_mode,
                                    parent_task_id=parent_task_id,
                                    workspace=workspace, session_id=session_id,
                                    path=AUDIT_LOG)
        _audit("qxen_cd_audit_register", "OK", work_item_id=work_item_id,
               origin=origin, baseline_required=baseline_required)
        return {"status": "OK", "server": "qxen-cd", "record": result}
    except ValueError as exc:
        _audit("qxen_cd_audit_register", "FALLBACK", reason=str(exc))
        return {"status": "FALLBACK", "server": "qxen-cd", "fallback_reason": str(exc)}


@mcp.tool()
async def qxen_cd_audit_usage(work_item_id: str, usage_id: str,
                              baseline_mode: str, eval_window: str, outcome: str,
                              baseline_gpt_tokens: int | None = None,
                              qxen_gpt_tokens: int | None = None,
                              qxen_local_tokens: int | None = 0,
                              gpt_review_tokens: int | None = None,
                              fallback_replay_gpt_tokens: int | None = None,
                              source_chars: int | None = None, workspace: str = "",
                              session_id: str = "", estimated: bool = False,
                              note: str = "") -> dict:
    """记录同一业务工作项的 baseline/QXEN 成对 token 观测。"""
    try:
        result = record_usage(work_item_id, usage_id, baseline_mode=baseline_mode,
                          eval_window=eval_window, outcome=outcome,
                          baseline_gpt_tokens=baseline_gpt_tokens,
                          qxen_gpt_tokens=qxen_gpt_tokens,
                          qxen_local_tokens=qxen_local_tokens,
                          gpt_review_tokens=gpt_review_tokens,
                          fallback_replay_gpt_tokens=fallback_replay_gpt_tokens,
                          source_chars=source_chars, workspace=workspace,
                          session_id=session_id, estimated=estimated,
                          note=note, path=AUDIT_LOG)
    except ValueError as exc:
        return {"status": "FALLBACK", "server": "qxen-cd", "fallback_reason": str(exc)}
    _audit("qxen_cd_audit_usage", "OK", work_item_id=work_item_id)
    return {"status": "OK", "server": "qxen-cd", "record": result}


@mcp.tool()
async def qxen_cd_audit_capsule_use(capsule_id: str, work_item_id: str,
                                    used_by: str = "gpt", outcome: str = "success",
                                    workspace: str = "", session_id: str = "") -> dict:
    """记录已接受胶囊是否被后续 GPT/任务实际引用。"""
    try:
        result = record_capsule_use(capsule_id, work_item_id, used_by=used_by,
                                    outcome=outcome, workspace=workspace,
                                    session_id=session_id, path=AUDIT_LOG)
        return {"status": "OK", "server": "qxen-cd", "record": result}
    except ValueError as exc:
        return {"status": "FALLBACK", "server": "qxen-cd", "fallback_reason": str(exc)}


@mcp.tool()
async def qxen_cd_audit_summary(workspace: str = "", session_id: str = "") -> dict:
    """汇总任务分类、处理利用率、可比 token 节省和数据缺口。"""
    result = summarize_audit(load_audit(AUDIT_LOG), workspace, session_id)
    _audit("qxen_cd_audit_summary", "OK", comparable_pairs=result["comparable_usage_pairs"])
    return result


@mcp.tool()
async def qxen_cd_audit_local_qwen(workspace: str = "", session_id: str = "") -> dict:
    """Return LocalQwen call/token/fallback audit, separate from GPT savings."""
    result = summarize_local_qwen(load_local_qwen(), workspace, session_id)
    result.update({"status": "OK", "server": "qxen-cd",
                   "authority": "deterministic_audit",
                   "must_not_change": ["business_task_count", "baseline_denominator",
                                        "gpt_saving", "gate_or_final_verdict"]})
    _audit("qxen_cd_audit_local_qwen", "OK",
           calls=result.get("calls_est", 0), tokens=result.get("local_tokens_est", 0))
    return result


def _local_advisory(tool: str, result: dict, fallback_from: str = "") -> dict:
    """Wrap LocalQwen output; semantic advice never becomes audit authority."""
    return {
        "status": result.get("status", "FALLBACK"),
        "server": "qxen-cd",
        "backend": "local-qwen",
        "authority": "advisory_only",
        "fallback_from": fallback_from,
        "audit_tool": tool,
        "result": result,
        "must_not_change": ["business_task_count", "baseline_denominator",
                             "net_saving_formula", "gate_or_final_verdict"],
        "time": _now(),
    }


@mcp.tool()
async def qxen_cd_audit_distill(log_text: str = "", log_path: str = "",
                                focus: str = "保留审计异常、任务分类、token数字和结论",
                                max_lines: int = 20) -> dict:
    """QXEN-CD 优先压缩审计材料；长日志优先传 log_path。"""
    try:
        resolved_text, input_meta = _audit_text_input(log_text, log_path)
    except Exception as exc:
        return {"status": "FALLBACK", "fallback_reason": f"audit_source_error:{str(exc)[:180]}",
                "authority": "advisory_only"}
    qxen_result = await qxen_cd_longtext_distill(
        source=Path(log_path).name if log_path else "audit-log",
        evidence=resolved_text if not log_path else "", source_path=log_path,
        max_tokens=max(256, min(max_lines * 80, 2048)))
    fallback_used = False
    if qxen_result.get("guard_status") != "FALLBACK":
        wrapped = {"status": "OK", "server": "qxen-cd", "backend": "qxen-cd",
                   "authority": "advisory_only", "audit_tool": "audit_distill",
                   "result": qxen_result, "fallback_from": "",
                   "must_not_change": ["business_task_count", "baseline_denominator",
                                        "net_saving_formula", "gate_or_final_verdict"],
                   "time": _now(), **input_meta}
        _audit("qxen_cd_audit_distill", "OK", backend="qxen-cd")
    else:
        fallback_used = True
        with local_qwen.audit_context(usage_class="audit_only"):
            result = await local_qwen.local_distill(
                text=resolved_text if not log_path else "", source_path=log_path,
                goal=focus, max_lines=max(3, min(max_lines, 40)))
        wrapped = _local_advisory("audit_distill", result, fallback_from="qxen-cd")
        wrapped.update(input_meta)
        _audit("qxen_cd_audit_distill", wrapped["status"], backend="local-qwen", fallback_from="qxen-cd")
    record_processing(task="audit_assistant_distill", origin="audit_only", pipeline="audit_assistant",
                      source_chars=len(resolved_text), qxen_output_chars=len(json.dumps(wrapped, ensure_ascii=False)),
                      overhead_chars=len(resolved_text), guard_status="ADVISORY", fallback=fallback_used,
                      path=AUDIT_LOG)
    return wrapped


@mcp.tool()
async def qxen_cd_audit_failure_extract(log_text: str = "", log_path: str = "") -> dict:
    """下放给 LocalQwen：长日志优先传 log_path，提取失败三元组。"""
    with local_qwen.audit_context(usage_class="audit_only"):
        result = await local_qwen.local_extract_failure(log_text=log_text, log_path=log_path)
    wrapped = _local_advisory("audit_failure_extract", result)
    wrapped["input_mode"] = result.get("input_mode", "inline")
    _audit("qxen_cd_audit_failure_extract", wrapped["status"], backend="local-qwen")
    record_processing(task="audit_assistant_failure_extract", origin="audit_only", pipeline="audit_assistant",
                      source_chars=int(result.get("source_bytes") or len(log_text)), qxen_output_chars=len(json.dumps(wrapped, ensure_ascii=False)),
                      overhead_chars=int(result.get("source_bytes") or len(log_text)), guard_status="ADVISORY", fallback=result.get("status") != "OK",
                      path=AUDIT_LOG)
    return wrapped


@mcp.tool()
async def qxen_cd_audit_cluster(alert: str, log_tail: str = "", log_path: str = "") -> dict:
    """下放给 LocalQwen：聚类审计异常并给出可恢复性建议；GPT仍负责处置。"""
    try:
        resolved_tail, input_meta = _audit_text_input(log_tail, log_path) if (log_tail or log_path) else ("", {"input_mode": "inline"})
    except Exception as exc:
        return {"status": "FALLBACK", "fallback_reason": f"audit_source_error:{str(exc)[:180]}",
                "authority": "advisory_only"}
    with local_qwen.audit_context(usage_class="audit_only"):
        result = await local_qwen.local_monitor_analyze(
            alert, resolved_tail if not log_path else "", log_path=log_path)
    wrapped = _local_advisory("audit_cluster", result)
    wrapped.update(input_meta)
    _audit("qxen_cd_audit_cluster", wrapped["status"], backend="local-qwen")
    record_processing(task="audit_assistant_cluster", origin="audit_only", pipeline="audit_assistant",
                      source_chars=len(alert) + len(resolved_tail), qxen_output_chars=len(json.dumps(wrapped, ensure_ascii=False)),
                      overhead_chars=len(alert) + len(resolved_tail), guard_status="ADVISORY", fallback=result.get("status") != "OK",
                      path=AUDIT_LOG)
    return wrapped


@mcp.tool()
async def qxen_cd_audit_classify(block: str = "", source_path: str = "") -> dict:
    """下放给 LocalQwen：建议审计上下文块的 PIN/DROP/KEEP/VERBATIM 级别。"""
    try:
        resolved_block, input_meta = _audit_text_input(block, source_path)
    except Exception as exc:
        return {"status": "FALLBACK", "fallback_reason": f"audit_source_error:{str(exc)[:180]}",
                "authority": "advisory_only"}
    with local_qwen.audit_context(usage_class="audit_only"):
        result = await local_qwen.local_classify(resolved_block)
    wrapped = _local_advisory("audit_classify", result)
    wrapped.update(input_meta)
    _audit("qxen_cd_audit_classify", wrapped["status"], backend="local-qwen")
    record_processing(task="audit_assistant_classify", origin="audit_only", pipeline="audit_assistant",
                      source_chars=len(resolved_block), qxen_output_chars=len(json.dumps(wrapped, ensure_ascii=False)),
                      overhead_chars=len(resolved_block), guard_status="ADVISORY", fallback=result.get("status") != "OK",
                      path=AUDIT_LOG)
    return wrapped


@mcp.tool()
def qxen_cd_guard(raw: str, prompt: str = "") -> dict:
    """Deterministic Guard-only validation; never loads the QXEN model.

    Use this only for deterministic guard fixtures. Real long evidence uses
    qxen_cd_longtext_distill; this tool never invokes the model.
    """
    try:
        result = guard_text(str(raw), str(prompt))
        status = result.get("guard_status", "UNKNOWN") if isinstance(result, dict) else "UNKNOWN"
        _audit("qxen_cd_guard", status, mode="deterministic_guard_only",
               model_called=False)
        return result
    except Exception as exc:
        result = {
            "guard_status": "FALLBACK",
            "fallback_reason": "guard_error:" + str(exc)[:240],
            "requires_gpt_review": True,
            "mode": "deterministic_guard_only",
            "model_called": False,
        }
        _audit("qxen_cd_guard", "FALLBACK", reason=result["fallback_reason"],
               mode="deterministic_guard_only", model_called=False)
        return result
 
async def _qxen_generate(source: str, evidence: str, task: str = "evidence_compression",
                           max_tokens: int = 1000, work_item_id: str = "",
                           task_id: str = "", workspace: str = "", session_id: str = "",
                           capsule_id: str = "") -> dict:
    """Private model-generation primitive used only by longtext distillation."""
    started = time.perf_counter()
    model_called = False
    callback_latency_s = 0.0
    claim_update = _capsule_update(capsule_id, "claim", worker_id=f"qxen-mcp-{os.getpid()}")
    callback_latency_s += float(claim_update.get("callback_latency_s", 0.0))
    capsule_status_error = str(claim_update.get("error", ""))
    claim_token = str(claim_update.get("claim_token", ""))

    def finish_capsule(action: str, reason: str = "", latency: str = "",
                       result_payload: dict | None = None) -> None:
        nonlocal callback_latency_s, capsule_status_error
        update = _capsule_update(capsule_id, action, reason, latency,
                                 claim_token=claim_token, result_payload=result_payload)
        callback_latency_s += float(update.get("callback_latency_s", 0.0))
        capsule_status_error = capsule_status_error or str(update.get("error", ""))
    if not work_item_id:
        work_item_id = _auto_work_item_id(source, task, evidence)
    if capsule_id and not claim_update.get("ok"):
        result = {"runtime": "QXEN-CD", "guard_status": "FALLBACK",
                  "fallback_reason": "capsule_claim_unavailable",
                  "requires_gpt_review": True, "source": source,
                  "capsule_status_error": capsule_status_error,
                  "latency_s": round(time.perf_counter() - started, 4),
                  "capsule_callback_latency_s": round(callback_latency_s, 4)}
        _audit("qxen_cd_generate_internal", "FALLBACK", reason=result["fallback_reason"], task=task,
               latency_s=result["latency_s"], model_called=False, capsule_id=capsule_id,
               capsule_status_error=capsule_status_error)
        return result
    if task not in TASK_INSTRUCTIONS:
        result = {"runtime": "QXEN-CD", "guard_status": "FALLBACK",
                  "fallback_reason": "unknown_task:" + str(task),
                  "requires_gpt_review": True, "source": source}
        finish_capsule("fail", result["fallback_reason"])
        result["latency_s"] = round(time.perf_counter() - started, 4)
        result["capsule_callback_latency_s"] = round(callback_latency_s, 4)
        _audit("qxen_cd_generate_internal", "FALLBACK", reason=result["fallback_reason"], task=task,
               latency_s=result["latency_s"], capsule_callback_latency_s=result["capsule_callback_latency_s"],
               model_called=model_called, capsule_id=capsule_id,
               capsule_status_error=capsule_status_error)
        record_processing(work_item_id=work_item_id, task_id=task_id, task=task,
                          source_chars=len(source) + len(evidence), baseline_scope="source_plus_evidence",
                          pipeline="longtext_internal_generate", guard_status="FALLBACK",
                          fallback=True, workspace=workspace, session_id=session_id,
                          path=AUDIT_LOG)
        _ensure_business_audit(work_item_id, task, source, evidence, result,
                               workspace, session_id)
        return result

    if not BASE_MODEL.is_dir() or not ADAPTER.is_dir():
        result = {"runtime": "QXEN-CD", "guard_status": "FALLBACK",
                  "fallback_reason": "model_or_adapter_missing",
                  "requires_gpt_review": True, "source": source,
                  "gpt_context": {"context_mode": "GPT_REVIEW", "source": source,
                                  "raw_evidence": evidence, "preserve_original": True}}
        finish_capsule("fail", result["fallback_reason"])
        result["latency_s"] = round(time.perf_counter() - started, 4)
        result["capsule_callback_latency_s"] = round(callback_latency_s, 4)
        _audit("qxen_cd_generate_internal", "FALLBACK", reason=result["fallback_reason"], task=task,
               latency_s=result["latency_s"], capsule_callback_latency_s=result["capsule_callback_latency_s"],
               model_called=model_called, capsule_id=capsule_id,
               capsule_status_error=capsule_status_error)
        record_processing(work_item_id=work_item_id, task_id=task_id, task=task,
                          source_chars=len(source) + len(evidence), baseline_scope="source_plus_evidence",
                          pipeline="longtext_internal_generate", guard_status="FALLBACK",
                          fallback=True, workspace=workspace, session_id=session_id,
                          path=AUDIT_LOG)
        _ensure_business_audit(work_item_id, task, source, evidence, result,
                               workspace, session_id)
        return result
    model_started = time.perf_counter()
    try:
        from mlx_lm import load
        model, tokenizer = load(str(BASE_MODEL), adapter_path=str(ADAPTER))
        model_called = True
        result = infer_one(model, tokenizer, source, evidence, task, max(1, min(max_tokens, 4096)))
        result["mcp_server"] = "qxen-cd"
        result["scope"] = "global-codex"
        result["capsule_id"] = _stable_capsule_id(result)
        result["model_latency_s"] = round(time.perf_counter() - model_started, 4)
        result["latency_s"] = round(time.perf_counter() - started, 4)
        if result.get("guard_status") == "FALLBACK":
            finish_capsule("fail", result.get("fallback_reason", "guard_fallback"), str(result["latency_s"]))
        else:
            finish_capsule("complete", latency=str(result["latency_s"]), result_payload=result)
        result["capsule_callback_latency_s"] = round(callback_latency_s, 4)
        if capsule_status_error:
            result["capsule_status_error"] = capsule_status_error
        append_audit(result, ROOT / "logs" / "qxen_cd_runtime.jsonl")
        record_processing(work_item_id=work_item_id, task_id=task_id, task=task,
                          source_chars=len(source) + len(evidence), qxen_output_chars=_gpt_payload_chars(result),
                          baseline_scope="source_plus_evidence", pipeline="longtext_internal_generate",
                          capsule_id=result.get("capsule_id", ""),
                          guard_status=result.get("guard_status", ""),
                          fallback=result.get("guard_status") == "FALLBACK",
                          workspace=workspace, session_id=session_id, path=AUDIT_LOG)
        _ensure_business_audit(work_item_id, task, source, evidence, result,
                               workspace, session_id)
        _audit("qxen_cd_generate_internal", result.get("guard_status", "UNKNOWN"),
               task=task, latency_s=result["latency_s"], capsule_id=capsule_id,
               model_latency_s=result.get("model_latency_s", 0.0),
               capsule_callback_latency_s=result.get("capsule_callback_latency_s", 0.0),
               capsule_status_error=capsule_status_error,
               fallback_reason=result.get("fallback_reason", ""),
               model_called=model_called)
        return result
    except Exception as exc:  # never turn a model/runtime fault into a hard stop
        result = {"runtime": "QXEN-CD", "mcp_server": "qxen-cd",
                  "guard_status": "FALLBACK", "fallback_reason": "runtime_error:" + str(exc)[:240],
                  "requires_gpt_review": True, "source": source,
                  "gpt_context": {"context_mode": "GPT_REVIEW", "source": source,
                                  "raw_evidence": evidence, "preserve_original": True}}
        finish_capsule("fail", result["fallback_reason"])
        result["latency_s"] = round(time.perf_counter() - started, 4)
        result["capsule_callback_latency_s"] = round(callback_latency_s, 4)
        if capsule_status_error:
            result["capsule_status_error"] = capsule_status_error
        _audit("qxen_cd_generate_internal", "FALLBACK", reason=result["fallback_reason"], task=task,
               latency_s=result["latency_s"], capsule_callback_latency_s=result["capsule_callback_latency_s"],
               model_called=model_called, capsule_id=capsule_id,
               capsule_status_error=capsule_status_error)
        record_processing(work_item_id=work_item_id, task_id=task_id, task=task,
                          source_chars=len(source) + len(evidence), baseline_scope="source_plus_evidence",
                          pipeline="longtext_internal_generate", guard_status="FALLBACK",
                          fallback=True, capsule_id="", workspace=workspace, session_id=session_id,
                          path=AUDIT_LOG)
        _ensure_business_audit(work_item_id, task, source, evidence, result,
                               workspace, session_id)
        return result


@mcp.tool()
async def qxen_cd_longtext_distill(source: str, evidence: str = "",
                                    source_path: str = "",
                                    max_tokens: int = 900, work_item_id: str = "",
                                    task_id: str = "", workspace: str = "",
                                    session_id: str = "", capsule_id: str = "") -> dict:
    """Long-text faithful distillation; returns one capsule representation.

    Rolling-state merge is intentionally separate: call qxen_cd_compact explicitly.
    """
    claim = _capsule_update(capsule_id, "claim", worker_id=f"qxen-longtext-{os.getpid()}")
    claim_token = str(claim.get("claim_token", ""))
    if capsule_id and not claim.get("ok"):
        return {"runtime": "QXEN-CD", "task": "qxen_longtext_distill",
                "guard_status": "FALLBACK", "fallback_reason": "capsule_claim_unavailable",
                "capsule_status_error": claim.get("error", claim.get("reason", "")),
                "requires_gpt_review": True}

    def finish(result: dict) -> dict:
        if capsule_id:
            action = "fail" if result.get("guard_status") == "FALLBACK" else "complete"
            update = _capsule_update(capsule_id, action,
                                     str(result.get("fallback_reason", "")),
                                     str(result.get("latency_s", "")),
                                     claim_token=claim_token,
                                     result_payload=result if action == "complete" else None)
            result["capsule_callback"] = update
        return result

    resolved_source = Path(source_path or source.split("#", 1)[0]).expanduser()
    input_mode = "inline"
    source_locator: dict[str, Any] = {}
    if not evidence:
        if not resolved_source.is_file():
            return finish({
                "runtime": "QXEN-CD", "task": "qxen_longtext_distill",
                "guard_status": "FALLBACK",
                "fallback_reason": "missing_evidence_or_source_path",
                "requires_gpt_review": False,
                "review_policy": "conditional",
            })
        try:
            raw_bytes = resolved_source.read_bytes()
            if resolved_source.suffix.lower() == ".pdf":
                evidence = extract_pdf_text(resolved_source)
                extraction = "pdfplumber_page_marked_text"
            else:
                evidence = raw_bytes.decode("utf-8", errors="replace")
                extraction = "utf8_text"
            input_mode = "local_path"
            source_locator = {
                "path": str(resolved_source.resolve()),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "bytes": len(raw_bytes),
                "content_chars": len(evidence),
                "extraction": extraction,
            }
        except Exception as exc:
            return finish({
                "runtime": "QXEN-CD", "task": "qxen_longtext_distill",
                "guard_status": "FALLBACK",
                "fallback_reason": f"source_read_error:{str(exc)[:160]}",
                "requires_gpt_review": False,
                "review_policy": "conditional",
            })
    elif resolved_source.is_file():
        try:
            raw_bytes = resolved_source.read_bytes()
            source_locator = {
                "path": str(resolved_source.resolve()),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "bytes": len(raw_bytes),
                "content_chars": len(evidence),
                "extraction": "caller_supplied_text",
            }
        except OSError:
            source_locator = {}

    consumption_policy = {
        "mode": "capsule_first_targeted_retrieval",
        "equivalence": "task_scoped_not_source_equivalent",
        "use_capsule_first": True,
        "retrieve_original_when": [
            "exact_quote_or_value_required",
            "code_edit_or_line_level_review",
            "conflict_or_missing_evidence",
            "high_risk_decision",
        ],
        "never_claim_full_source_replacement": True,
    }

    def attach_source_contract(result: dict, span: str = "full_source") -> dict:
        result["input_mode"] = input_mode
        result["source_span"] = span
        result["consumption_policy"] = consumption_policy
        if source_locator:
            result["raw_pointer"] = source_locator["path"]
            result["source_locator"] = {**source_locator, "span": span}
        return result

    def record_observable_path(result: dict) -> None:
        if input_mode != "local_path" or not source_locator:
            return
        try:
            record_path_distill(
                source_locator["path"], source_locator["sha256"],
                source_chars=int(source_locator.get("content_chars") or 0),
                returned_chars=len(json.dumps(result, ensure_ascii=False)),
                work_item_id=work_item_id, task_id=task_id,
                capsule_id=str(result.get("capsule_id") or ""),
                workspace=workspace, session_id=session_id, path=AUDIT_LOG)
        except Exception:
            pass
    # Deterministic paragraph-aware chunking: <=6K per call; no model decides chunks.
    max_chars = 6000

    def preflight(text: str) -> dict[str, Any]:
        """Deterministically expose table and numeric context before model use."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        table_lines = [line for line in lines if (
            "|" in line or "\t" in line or re.search(r"\s{2,}", line)
        ) and re.search(r"\d", line)]
        numeric_tokens = re.findall(
            r"[-+]?\d+(?:\.\d+)?%?|[-+]?\d+(?:,\d{3})+(?:\.\d+)?",
            text,
        )
        rules = []
        if re.search(r"同比|去年同期|yoy", text, re.I):
            rules.append("year_over_year")
        if re.search(r"环比|上月|mom", text, re.I):
            rules.append("month_over_month")
        if "%" in text or re.search(r"百分点|bp|亿元|万亿|元", text, re.I):
            rules.append("unit_or_rate_present")
        return {
            "table_lines": table_lines[:20],
            "table_line_count": len(table_lines),
            "numeric_tokens": numeric_tokens[:100],
            "numeric_token_count": len(numeric_tokens),
            "value_rules": rules,
        }

    def model_evidence(text: str, context: dict[str, Any]) -> str:
        if context.get("table_line_count") or context.get("value_rules"):
            return "[DETERMINISTIC_PREFLIGHT]\n" + json.dumps(context, ensure_ascii=False) + "\n[TEXT]\n" + text
        return text

    preflight_source_path = resolved_source
    pdf_preflight = None
    if preflight_source_path.is_file() and preflight_source_path.suffix.lower() == ".pdf":
        try:
            pdf_preflight = preflight_pdf(preflight_source_path)
        except Exception as exc:
            pdf_preflight = {"status": "WARNING", "warnings": [f"pdf_preflight_error:{str(exc)[:160]}"]}

    if len(evidence) <= max_chars:
        context = preflight(evidence)
        result = await _qxen_generate(
            source=source,
            evidence=model_evidence(evidence, context),
            task="qxen_longtext_distill",
            max_tokens=max_tokens, work_item_id=work_item_id, task_id=task_id,
            workspace=workspace, session_id=session_id,
        )
        result["preflight"] = context
        if pdf_preflight is not None:
            result["pdf_preflight"] = pdf_preflight
        result["requires_gpt_review"] = False
        result["review_policy"] = "conditional"
        result["chunking"] = {"mode": "single", "chunk_chars": len(evidence), "chunks": 1}
        attach_source_contract(result)
        record_observable_path(result)
        return finish(result)
    paragraphs = [p.strip() for p in evidence.replace("\r", "\n").split("\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for paragraph in paragraphs:
        if current and current_chars + len(paragraph) + 1 > max_chars:
            chunks.append("\n".join(current))
            current, current_chars = [], 0
        current.append(paragraph)
        current_chars += len(paragraph) + 1
    if current:
        chunks.append("\n".join(current))
    results = []
    preflight_summary = []
    for index, chunk in enumerate(chunks, 1):
        context = preflight(chunk)
        preflight_summary.append({"chunk": index, **context})
        chunk_result = await _qxen_generate(
            source=f"{source}#chunk{index:02d}",
            evidence=model_evidence(chunk, context),
            task="qxen_longtext_distill", max_tokens=max_tokens,
            work_item_id=f"{work_item_id}:chunk{index:02d}" if work_item_id else "",
            task_id=task_id, workspace=workspace, session_id=session_id,
        )
        results.append(attach_source_contract(chunk_result, f"chunk:{index}/{len(chunks)}"))
    combined = {"runtime": "QXEN-CD", "task": "qxen_longtext_distill",
                "guard_status": "ADVISORY",
                "chunking": {"mode": "deterministic_paragraph", "max_chars": max_chars,
                             "chunks": len(chunks), "preflight": preflight_summary},
                "pdf_preflight": pdf_preflight, "results": results,
                "requires_gpt_review": False, "review_policy": "conditional",
                "authority": "advisory_only", "input_mode": input_mode,
                "consumption_policy": consumption_policy}
    if source_locator:
        combined["raw_pointer"] = source_locator["path"]
        combined["source_locator"] = {**source_locator, "span": "full_source"}
    record_observable_path(combined)
    return finish(combined)

@mcp.tool()
def qxen_cd_source_slice(raw_pointer: str, expected_sha256: str = "",
                         start_line: int = 0, end_line: int = 0,
                         query: str = "", context_lines: int = 3,
                         max_chars: int = 12000, work_item_id: str = "",
                         capsule_id: str = "", workspace: str = "",
                         session_id: str = "") -> dict:
    """Deterministically retrieve a bounded exact source excerpt from a capsule pointer."""
    try:
        path = Path(raw_pointer).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 and digest != expected_sha256:
            return {"status": "FALLBACK", "fallback_reason": "source_hash_mismatch",
                    "source": str(path), "actual_sha256": digest,
                    "expected_sha256": expected_sha256}
        if path.suffix.lower() == ".pdf":
            text = extract_pdf_text(path)
            extraction = "pdfplumber_page_marked_text"
        else:
            text = raw.decode("utf-8", errors="replace")
            extraction = "utf8_text"
        lines = text.splitlines()
        if query:
            needle = query.casefold()
            matches = [index for index, line in enumerate(lines) if needle in line.casefold()]
            if not matches:
                return {"status": "NOT_FOUND", "source": str(path), "query": query,
                        "source_sha256": digest}
            center = matches[0]
            start = max(0, center - max(0, min(context_lines, 20)))
            stop = min(len(lines), center + max(0, min(context_lines, 20)) + 1)
        elif start_line > 0:
            start = min(max(start_line - 1, 0), len(lines))
            requested_end = end_line if end_line >= start_line else start_line
            stop = min(requested_end, len(lines))
        else:
            return {"status": "FALLBACK", "fallback_reason": "target_selector_required",
                    "source": str(path), "source_sha256": digest}
        limit = max(200, min(max_chars, 20000))
        excerpt = "\n".join(lines[start:stop])
        truncated = len(excerpt) > limit
        if truncated:
            excerpt = excerpt[:limit]
        result = {
            "status": "OK", "source": str(path), "source_sha256": digest,
            "hash_verified": not expected_sha256 or digest == expected_sha256,
            "source_span": {"start_line": start + 1, "end_line": stop},
            "text": excerpt, "truncated": truncated, "extraction": extraction,
            "authority": "verbatim_source_excerpt",
        }
        _audit("qxen_cd_source_slice", "OK", source=str(path),
               start_line=start + 1, end_line=stop, chars=len(excerpt))
        record_source_retrieval(
            str(path), digest, returned_chars=len(excerpt),
            work_item_id=work_item_id, capsule_id=capsule_id,
            workspace=workspace, session_id=session_id, path=AUDIT_LOG)
        return result
    except Exception as exc:
        result = {"status": "FALLBACK",
                  "fallback_reason": "source_slice_error:" + str(exc)[:200]}
        _audit("qxen_cd_source_slice", "FALLBACK", reason=result["fallback_reason"])
        return result


@mcp.tool()
async def qxen_cd_compact(records: list[dict], state: dict | None = None,
                           task_id: str = "", as_of: str = "", max_items: int = 64,
                           max_chars: int = 24000, work_item_id: str = "",
                           workspace: str = "", session_id: str = "") -> dict:
    """确定性合并 QXEN-CD 胶囊，隔离 fallback 并执行去重/预算裁剪。"""
    try:
        result = compact(records, state or {"task_id": task_id, "as_of": as_of},
                         max(1, min(max_items, 256)), max(1000, min(max_chars, 100000)))
        result["status"] = "OK"
        result["server"] = "qxen-cd"
        result["requires_gpt_review"] = bool(result.get("pending_gpt_review"))
        record_processing(work_item_id=work_item_id, task_id=task_id,
                          task="rolling_context_compact", pipeline="compact",
                          baseline_scope="processed_records", source_chars=len(json.dumps(records, ensure_ascii=False)),
                          qxen_output_chars=len(json.dumps(result, ensure_ascii=False)),
                          capsule_id="", overhead_chars=0,
                          guard_status="COMPACT", fallback=False, workspace=workspace,
                          session_id=session_id, path=AUDIT_LOG)
        _audit("qxen_cd_compact", "OK", accepted=len(result.get("accepted_capsules", [])),
               pending=len(result.get("pending_gpt_review", [])))
        return result
    except Exception as exc:
        result = {"status": "FALLBACK", "server": "qxen-cd",
                  "fallback_reason": "compact_error:" + str(exc)[:240],
                  "requires_gpt_review": True}
        _audit("qxen_cd_compact", "FALLBACK", reason=result["fallback_reason"])
        return result


if __name__ == "__main__":
    mcp.run()
