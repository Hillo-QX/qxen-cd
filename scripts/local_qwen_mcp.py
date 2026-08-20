#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LocalQwen MCP Server (shared MLX backend)
====================

v3 架构的"本地蒸馏器官"：把本地 Qwen MLX + QXEN LoRA 包装成
一组单轮、窄 prompt、schema 校验的 MCP 工具，供主 Agent 调用。

设计原则：
    - qwen 永远无状态：每次调用独立单轮，不给历史、不给工具、不给协议。
    - 路径优先、蒸馏出：长原文由 MCP 从本地路径读取，短文本仅作兼容输入。
    - 失败可降级：任一工具调用失败重试 1 次，再失败返回 status=FALLBACK，
      由 DS 自己读原文兜底，系统永远不死在 9B 手上。
    - 全程可审计：每次调用向 日志/local_qwen.log 追加一行 JSON
      （工具名 / 输入字节 / 输出行数 / 重试次数 / 耗时 / 状态）。

环境变量：
    QXEN_BASE_MODEL       MLX 基础模型目录
    QXEN_ADAPTER          QXEN LoRA 目录
    LOCAL_QWEN_TIMEOUT    单次调用超时秒数（默认 120）
    LOCAL_QWEN_LOG        审计日志路径（默认 <项目根>/日志/local_qwen.log）

运行：
    ./venv/bin/python local_qwen_mcp.py
"""

import json
import fcntl
import hashlib
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from mlx_shared_backend import generate as mlx_generate, health as mlx_health
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent

MODEL = os.environ.get("LOCAL_QWEN_MODEL", "qwen3.5-9b-mlx-4bit+qxen_joint_v1_clean_full")
TIMEOUT = float(os.environ.get("LOCAL_QWEN_TIMEOUT", "120"))
LOG_PATH = Path(os.environ.get("LOCAL_QWEN_LOG", str(ROOT / "日志" / "local_qwen.log")))
HEALTH_CACHE_PATH = Path(os.environ.get(
    "LOCAL_QWEN_HEALTH_CACHE",
    str(ROOT / "调度状态" / "local_qwen_health_cache.json"),
))
HEALTH_CACHE_TTL = float(os.environ.get("LOCAL_QWEN_HEALTH_TTL", "900"))
HEALTH_LOCK_PATH = Path(os.environ.get(
    "LOCAL_QWEN_HEALTH_LOCK",
    str(ROOT / "调度状态" / "local_qwen_health_probe.lock"),
))


@contextmanager
def _health_probe_lock():
    HEALTH_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(HEALTH_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

MAX_INPUT_CHARS = 200_000        # 单次调用输入上限，超出截断并标记
MAX_FILES_PER_CALL = 50          # local_summarize_files 单次文件数上限
MAX_CHARS_PER_FILE = 20_000      # 单文件读入上限
LABELS = ("PIN", "DROP", "KEEP", "VERBATIM")
MONITOR_VERDICTS = ("recoverable", "needs_decision", "dangerous")
FACTOR_REVIEW_DECISIONS = ("KEEP", "REVIEW", "DROP")
MAX_FACTOR_CANDIDATES = 100
MAX_FAILURE_ITEMS = 100

LOCAL_QWEN_AUDIT_CLASSES = {
    "local_health": "health_probe",
    "local_distill": "context_distillation",
    "local_summarize_files": "context_distillation",
    "local_extract_failure": "failure_analysis",
    "local_classify": "context_selection",
    "local_research_log_distill": "research_assist",
    "local_factor_generate": "candidate_generation",
    "local_factor_review": "expression_review",
    "local_failure_cluster": "failure_clustering",
    "local_monitor_analyze": "monitor_assist",
}
CONTEXT_SAVING_ELIGIBLE_CLASSES = {
    "context_distillation",
    "failure_analysis",
    "context_selection",
    "research_assist",
}

# qxen_cd_mcp temporarily overrides this for its audit-only delegation paths.
_AUDIT_CONTEXT = {"usage_class": "direct_local_assist", "origin": "local-qwen",
                  "work_item_id": "", "session_id": ""}

mcp = FastMCP("local-qwen")


# ---------------------------------------------------------------- 基础设施

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(entry: dict) -> None:
    """每次工具调用追加一行 JSON 审计日志。日志写失败不影响主流程。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


@contextmanager
def audit_context(*, usage_class: str, origin: str = "local-qwen",
                  work_item_id: str = "", session_id: str = ""):
    """Attach business/audit provenance to subsequent LocalQwen log rows."""
    global _AUDIT_CONTEXT
    previous = _AUDIT_CONTEXT
    _AUDIT_CONTEXT = {"usage_class": usage_class, "origin": origin,
                      "work_item_id": work_item_id, "session_id": session_id}
    try:
        yield
    finally:
        _AUDIT_CONTEXT = previous


def _audit_fields(tool: str) -> dict:
    audit_class = LOCAL_QWEN_AUDIT_CLASSES.get(tool, "other")
    return {
        "audit_class": audit_class,
        "usage_class": _AUDIT_CONTEXT.get("usage_class", "direct_local_assist"),
        "origin": _AUDIT_CONTEXT.get("origin", "local-qwen"),
        "work_item_id": _AUDIT_CONTEXT.get("work_item_id", ""),
        "session_id": _AUDIT_CONTEXT.get("session_id", ""),
        "local_tokens_estimated": True,
        "count_as_gpt_saving": False,
        "context_saving_eligible": audit_class in CONTEXT_SAVING_ELIGIBLE_CLASSES,
    }


def _load_health_cache() -> dict | None:
    """读取短期 OK 缓存；错误结果不落盘，因此不会掩盖离线状态。"""
    try:
        if not HEALTH_CACHE_PATH.is_file():
            return None
        cache = json.loads(HEALTH_CACHE_PATH.read_text(encoding="utf-8"))
        if cache.get("status") != "OK":
            return None
        if cache.get("backend") != "mlx-shared" or cache.get("model") != MODEL:
            return None
        if time.time() - float(cache.get("checked_at", 0)) > HEALTH_CACHE_TTL:
            return None
        result = dict(cache.get("result") or {})
        age_s = max(0.0, time.time() - float(cache["checked_at"]))
        result.update({"status": "OK", "cached": True,
                       "cache_age_s": round(age_s, 1),
                       "next_probe_after_s": round(max(0.0, HEALTH_CACHE_TTL - age_s), 1)})
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _save_health_cache(result: dict) -> None:
    try:
        HEALTH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "OK", "checked_at": time.time(),
            "backend": "mlx-shared", "model": MODEL, "result": result,
        }
        HEALTH_CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


class QwenError(Exception):
    """本地模型调用或输出校验失败。"""


def _chat(prompt: str, num_predict: int) -> str:
    """单轮调用本地 qwen。think:false 硬性关闭思考（2026-08-13 实测生效路径）。

    返回模型文本。网络错误 / HTTP 错误 / 空响应一律抛 QwenError。
    """
    try:
        text = mlx_generate(prompt, num_predict)["text"]
    except Exception as e:  # MLX runtime errors are normalized below
        raise QwenError(f"mlx call failed: {e}") from e
    if not text.strip():
        raise QwenError("empty response from mlx")
    return text.strip()


def _run_tool(tool: str, input_chars: int, fn, input_meta: dict | None = None):
    """工具执行骨架：尝试 -> 重试 1 次 -> FALLBACK，全程审计。

    fn(attempt) 返回 (result_dict, output_lines) 或
    (result_dict, output_lines, actual_input_chars)——第三种形式用于
    input_chars 只能在执行中确定的工具（如 local_summarize_files 的文件内容量）。
    审计条目含 input_chars / output_chars——token 经济审计（audit_v2_session.py）
    直接依赖这两个字段计算蒸馏比与避免的重发 token。
    """
    t0 = time.time()
    last_err = ""
    # START 事件：让 tail -f 的实时观察能看到"qwen 正在跑"，而不只是事后结果。
    input_meta = dict(input_meta or {})
    _audit({"time": _now(), "tool": tool, "status": "START",
            "input_chars": input_chars, "model": MODEL, **input_meta,
            **_audit_fields(tool)})
    for attempt in (1, 2):
        try:
            out = fn(attempt)
            result, out_lines = out[0], out[1]
            if len(out) > 2 and out[2] is not None:
                input_chars = out[2]
            entry = {
                "time": _now(), "tool": tool, "status": "OK",
                "input_chars": input_chars,
                "output_chars": len(json.dumps(result, ensure_ascii=False)),
                "output_lines": out_lines,
                "attempt": attempt, "latency_s": round(time.time() - t0, 2),
                "model": MODEL, **input_meta,
            }
            entry.update(_audit_fields(tool))
            _audit(entry)
            return {"status": "OK", **result}
        except QwenError as e:
            last_err = str(e)
    _audit({
        "time": _now(), "tool": tool, "status": "FALLBACK",
        "input_chars": input_chars, "reason": last_err,
        "attempt": 2, "latency_s": round(time.time() - t0, 2),
        "model": MODEL, **input_meta,
        **_audit_fields(tool),
    })
    return {
        "status": "FALLBACK",
        "reason": last_err,
        "instructions": "本地模型连续 2 次失败。由你（DS）自行小批量读取原文完成本步骤，"
                        "并在批次汇报中注明 LocalQwen 降级。",
    }


def _cap_text(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_INPUT_CHARS:
        return text, False
    return text[:MAX_INPUT_CHARS], True


def _resolve_text_input(inline_text: str, source_path: str = "") -> tuple[str, bool, dict]:
    """Resolve inline compatibility input or read a local file outside GPT context."""
    if inline_text:
        text, truncated = _cap_text(inline_text)
        return text, truncated, {"input_mode": "inline"}
    if not source_path:
        raise QwenError("missing_inline_text_or_source_path")
    path = Path(source_path).expanduser().resolve()
    if not path.is_file():
        raise QwenError(f"source_not_file:{path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise QwenError(f"source_read_error:{exc}") from exc
    text, truncated = _cap_text(raw.decode("utf-8", errors="replace"))
    return text, truncated, {
        "input_mode": "local_path",
        "source_path": str(path),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw),
    }


def _clamp_lines(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n[...截断：原 {len(lines)} 行，限 {max_lines} 行]"


def _json_object(text: str) -> dict:
    """从模型输出提取 JSON object，并统一转成 QwenError。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise QwenError(f"no JSON object in response: {text[:120]}")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise QwenError(f"invalid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise QwenError("JSON response is not an object")
    return obj


def _factor_hard_flags(expression: str) -> list[str]:
    """确定性初筛；不替代表达式解析器和回测验证。"""
    flags = []
    if not expression.strip():
        flags.append("empty_expression")
    if len(expression) > 500:
        flags.append("expression_too_long")
    if expression.count("(") != expression.count(")"):
        flags.append("unbalanced_parentheses")
    if any(token in expression.lower() for token in ("future", "forward", "next_return", "lead(")):
        flags.append("possible_lookahead_token")
    if ";" in expression or "\n" in expression:
        flags.append("invalid_expression_char")
    return flags


def _monitor_prompt(combined: str, attempt: int) -> str:
    """监控异常分析 prompt（窄 prompt + 严格 JSON schema）。"""
    strict = "（上一次输出不是合法 JSON。只输出一个 JSON 对象。）" if attempt == 2 else ""
    return (
        "你是训练监控分析器。根据异常摘要与日志片段，输出一个 JSON 对象，"
        '格式严格为 {"verdict": "...", "failure_cluster": "...", "evidence": ["..."], "alert_capsule": "..."}。\n'
        "字段约束：\n"
        "verdict 只取三值之一：recoverable（可恢复故障：进程临时退出、启动参数错误、单次非破坏命令失败）；"
        "needs_decision（需决策：架构/方向/数据划分/冻结资产变化）；"
        "dangerous（危险：OOM/Metal 停滞/整机崩溃风险，必须停止训练动作）。\n"
        "failure_cluster 一句话失败模式聚类，≤40 字。\n"
        "evidence 2-4 条关键证据，每条 ≤60 字。\n"
        "alert_capsule 给主 Agent 的精简告警，≤120 字。\n"
        "只输出一个 JSON 对象，禁止任何其他内容。"
        f"{strict}\n{combined}"
    )


def _parse_monitor(out: str) -> dict:
    """解析监控分析 JSON，校验 verdict 合法。失败抛 QwenError。"""
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end <= start:
        raise QwenError(f"no JSON object in response: {out[:100]}")
    try:
        obj = json.loads(out[start:end + 1])
    except json.JSONDecodeError as e:
        raise QwenError(f"invalid JSON: {e}") from e
    verdict = str(obj.get("verdict", ""))
    if verdict not in MONITOR_VERDICTS:
        raise QwenError(f"invalid verdict: {verdict}")
    return {
        "verdict": verdict,
        "failure_cluster": str(obj.get("failure_cluster", "unknown"))[:80],
        "evidence": [str(x)[:120] for x in obj.get("evidence", [])][:4],
        "alert_capsule": str(obj.get("alert_capsule", ""))[:240],
    }


# ---------------------------------------------------------------- 工具

async def _local_health_unlocked(force: bool = False) -> dict:
    """LocalQwen 健康检查：MLX 资产存在，不访问 Ollama。

    不经过 Dispatcher，不调任何远程 API。会话开始时用于确认本地蒸馏管道已注入。
    """
    t0 = time.time()
    cached = None if force else _load_health_cache()
    if cached is not None:
        _audit({"time": _now(), "tool": "local_health", "status": "OK",
                "cached": True, "cache_age_s": cached.get("cache_age_s"),
                "model": MODEL, **_audit_fields("local_health")})
        return cached
    try:
        base = mlx_health()
        if base["status"] != "OK":
            raise QwenError("MLX model or adapter missing")
        result = {
            "server": "local-qwen",
            "backend": "mlx-shared",
            "model": MODEL,
            "log": str(LOG_PATH),
            "latency_s": round(time.time() - t0, 2),
        }
        _audit({"time": _now(), "tool": "local_health", "status": "OK",
                "latency_s": result["latency_s"], "backend": "mlx-shared",
                "model": MODEL, **_audit_fields("local_health")})
        _save_health_cache(result)
        return {"status": "OK", "cached": False,
                "next_probe_after_s": round(HEALTH_CACHE_TTL, 1), **result}

    except Exception as e:
        _audit({"time": _now(), "tool": "local_health", "status": "ERROR",
                "reason": str(e), "model": MODEL, **_audit_fields("local_health")})
        return {"status": "ERROR", "server": "local-qwen", "reason": str(e)}


@mcp.tool()
async def local_health(force: bool = False) -> dict:
    """Serialize cross-process probes and expose auditable call classification."""
    with _health_probe_lock():
        result = await _local_health_unlocked(force=force)
    cached = bool(result.get("cached"))
    result.update({
        "health_call": 1,
        "actual_probe": 0 if cached else 1,
        "cache_hit": cached,
    })
    return result


@mcp.tool()
async def local_distill(text: str = "", goal: str = "", max_lines: int = 20,
                        source_path: str = "") -> dict:
    """把文本按目标蒸馏成不超过 max_lines 行的摘要。

    长材料优先传 source_path，由 MCP 在主 Agent 上下文外读取；
    text 只用于有界短文本兼容。
    """
    try:
        text, truncated, input_meta = _resolve_text_input(text, source_path)
    except QwenError as exc:
        return {"status": "FALLBACK", "reason": str(exc),
                "instructions": "传入可读 source_path 或有界短文本。"}

    def fn(attempt: int):
        as_of = _now()
        strict = "（上一次输出不合格。只输出摘要正文，禁止任何解释。）" if attempt == 2 else ""
        prompt = (
            f"你是蒸馏器。把下面的原文压缩成不超过 {max_lines} 行的中文摘要，"
            f"只保留与目标相关的信息（路径 / 错误 / 数值 / 结论），丢弃一切寒暄与重复。\n"
            f"观察时刻 as_of = {as_of}（UTC）。摘要内容须以该时刻为判断基准。\n"
            f"目标：{goal}\n"
            f"要求：只输出摘要正文，每行一条事实，不超过 {max_lines} 行。{strict}\n"
            f"原文：\n{text}"
        )
        out = _chat(prompt, num_predict=max_lines * 60)
        summary = _clamp_lines(out, max_lines)
        return ({"summary": summary, "goal": goal, "as_of": as_of,
                 "input_chars": len(text), "truncated_input": truncated,
                 **input_meta},
                len(summary.splitlines()))

    return _run_tool("local_distill", len(text), fn, input_meta)


@mcp.tool()
async def local_summarize_files(paths: list[str], lines_per_file: int = 3) -> dict:
    """批量文件摘要：每个文件单轮总结为不超过 lines_per_file 行。

    用于：定位阶段代替逐文件 Read。单文件读入上限 20K 字符（超出截断），
    单次最多 50 个文件。文件不存在 / 读取失败时该条目标记 ERROR，不影响其他文件。
    """
    if len(paths) > MAX_FILES_PER_CALL:
        paths = paths[:MAX_FILES_PER_CALL]

    def summarize_one(path: str, as_of: str) -> dict:
        p = Path(path)
        if not p.is_file():
            return {"path": path, "status": "ERROR", "reason": "not a file"}
        try:
            content = p.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS_PER_FILE]
        except OSError as e:
            return {"path": path, "status": "ERROR", "reason": str(e)}
        prompt = (
            f"你是蒸馏器。用不超过 {lines_per_file} 行中文概括下面这个文件的内容："
            f"它是什么、关键入口/函数/配置项、当前状态。只输出摘要正文，禁止解释。\n"
            f"观察时刻 as_of = {as_of}（UTC）。\n"
            f"文件路径：{path}\n内容：\n{content}"
        )
        out = _chat(prompt, num_predict=lines_per_file * 60)
        return {"path": path, "status": "OK",
                "summary": _clamp_lines(out, lines_per_file),
                "as_of": as_of,
                "input_chars": len(content)}

    def fn(attempt: int):
        as_of = _now()
        summaries = [summarize_one(p, as_of) for p in paths]
        if not any(s["status"] == "OK" for s in summaries):
            raise QwenError("all file summaries failed")
        out_lines = sum(len(s.get("summary", "").splitlines()) for s in summaries)
        content_chars = sum(s.get("input_chars", 0) for s in summaries)
        return ({"summaries": summaries, "file_count": len(summaries), "as_of": as_of},
                out_lines, content_chars)

    return _run_tool("local_summarize_files", sum(len(p) for p in paths), fn)


@mcp.tool()
async def local_extract_failure(log_text: str = "", log_path: str = "") -> dict:
    """从 pytest / 报错日志中提取 failure 三元组：test / expected / actual 各一行。

    长日志优先传 log_path；用于失败后进入自修流程前，把原始日志
    压缩成固定格式的 current_failure。
    """
    try:
        log_text, truncated, input_meta = _resolve_text_input(log_text, log_path)
    except QwenError as exc:
        return {"status": "FALLBACK", "reason": str(exc),
                "instructions": "传入 safe_run 返回的 log_path 或有界日志片段。"}

    def fn(attempt: int):
        strict = "（上一次输出不是合法 JSON。只输出一个 JSON 对象。）" if attempt == 2 else ""
        prompt = (
            "你是日志解析器。从下面的日志中提取最近一次失败，只输出一个 JSON 对象，"
            '格式严格为 {"test": "...", "expected": "...", "actual": "..."}，'
            "每个值一行以内，禁止输出 JSON 以外的任何内容。无法确定的字段填 \"unknown\"。"
            f"{strict}\n日志：\n{log_text}"
        )
        out = _chat(prompt, num_predict=200)
        start, end = out.find("{"), out.rfind("}")
        if start < 0 or end <= start:
            raise QwenError(f"no JSON object in response: {out[:100]}")
        try:
            obj = json.loads(out[start:end + 1])
        except json.JSONDecodeError as e:
            raise QwenError(f"invalid JSON: {e}") from e
        if not all(k in obj for k in ("test", "expected", "actual")):
            raise QwenError(f"missing keys in {obj}")
        result = {k: str(obj.get(k, "unknown"))[:200] for k in ("test", "expected", "actual")}
        result["truncated_input"] = truncated
        result.update(input_meta)
        return (result, 3)

    return _run_tool("local_extract_failure", len(log_text), fn, input_meta)


@mcp.tool()
async def local_classify(block: str = "", source_path: str = "") -> dict:
    """上下文块分类：只输出 PIN / DROP / KEEP / VERBATIM 之一（QXEN 在训任务）。

    用于：上下文筛选与蒸馏决策的初筛，与训练数据格式直接对齐。
    """
    try:
        block, truncated, input_meta = _resolve_text_input(block, source_path)
    except QwenError as exc:
        return {"status": "FALLBACK", "reason": str(exc),
                "instructions": "传入可读 source_path 或有界短文本。"}

    def fn(attempt: int):
        strict = "（上一次输出不是合法标签。只输出一个标签词。）" if attempt == 2 else ""
        prompt = (
            "决策：(PIN|DROP|KEEP|VERBATIM) 只输出一个标签词。"
            f"{strict}\n\n{block}"
        )
        out = _chat(prompt, num_predict=8)
        label = out.split()[0].upper() if out.split() else ""
        if label not in LABELS:
            raise QwenError(f"invalid label: {out[:50]}")
        return ({"label": label, "truncated_input": truncated, **input_meta}, 1)

    return _run_tool("local_classify", len(block), fn, input_meta)


@mcp.tool()
async def local_research_log_distill(log_text: str = "", task: str = "量化研究运行日志",
                                     log_path: str = "") -> dict:
    """量化研究日志蒸馏；长日志优先传 log_path，固定输出状态、数值和下一步。"""
    try:
        log_text, truncated, input_meta = _resolve_text_input(log_text, log_path)
    except QwenError as exc:
        return {"status": "FALLBACK", "reason": str(exc),
                "instructions": "传入 safe_run 返回的 log_path 或有界日志片段。"}

    def fn(attempt: int):
        strict = "上一次输出不合格；只输出 JSON。" if attempt == 2 else ""
        prompt = (
            "你是量化研究日志蒸馏器。只输出一个 JSON 对象，禁止解释。"
            '格式：{"status":"PASS|FAIL|INCOMPLETE|UNKNOWN",'
            '"summary":"≤120字", "key_metrics":["≤6条"], '
            '"evidence":["≤5条，每条≤100字"], "next_action":"≤100字"}。'
            "不要猜测缺失数值；不把模型意见写成事实。"
            f"{strict}\n任务：{task}\n日志：\n{log_text}"
        )
        obj = _json_object(_chat(prompt, num_predict=600))
        status = str(obj.get("status", "UNKNOWN")).upper()
        if status not in ("PASS", "FAIL", "INCOMPLETE", "UNKNOWN"):
            raise QwenError(f"invalid research status: {status}")
        result = {
            "status": status,
            "summary": str(obj.get("summary", ""))[:240],
            "key_metrics": [str(x)[:160] for x in obj.get("key_metrics", [])][:6],
            "evidence": [str(x)[:160] for x in obj.get("evidence", [])][:5],
            "next_action": str(obj.get("next_action", ""))[:200],
            "truncated_input": truncated,
            **input_meta,
        }
        return result, 1 + len(result["key_metrics"]) + len(result["evidence"])

    return _run_tool("local_research_log_distill", len(log_text), fn, input_meta)


@mcp.tool()
async def local_factor_generate(
    mechanism: str,
    fields: list[str],
    operators: list[str],
    count: int = 10,
    existing_signatures: list[str] | None = None,
) -> dict:
    """生成量价因子候选；只提出表达式，不执行回测，不宣称有效。"""
    count = max(1, min(int(count), MAX_FACTOR_CANDIDATES))
    fields = [str(x)[:80] for x in fields[:50]]
    operators = [str(x)[:80] for x in operators[:50]]
    existing_signatures = [str(x)[:160] for x in (existing_signatures or [])[:200]]

    def fn(attempt: int):
        strict = "上一次输出不合格；只输出 JSON。" if attempt == 2 else ""
        prompt = (
            "你是 A 股量价因子候选生成器。只输出一个 JSON 对象，禁止解释。"
            '格式：{"hypothesis":"≤100字", "candidates":['
            '{"expression":"合法单行表达式", "mechanism":"≤40字", "rationale":"≤100字"}]}'
            f"。必须生成恰好 {count} 个候选；只能使用给定 fields/operators；"
            "不得使用 future/forward/next_return/lead 等未来信息；不要声称候选已通过回测。"
            f"{strict}\n机制：{mechanism[:160]}\nfields：{fields}\noperators：{operators}"
            f"\n已有结构签名（避免重复）：{existing_signatures}"
        )
        obj = _json_object(_chat(prompt, num_predict=min(4000, 220 * count)))
        candidates = obj.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != count:
            raise QwenError(f"candidate count mismatch: expected {count}")
        allowed = set(fields + operators)
        clean = []
        for item in candidates:
            if not isinstance(item, dict):
                raise QwenError("candidate is not an object")
            expr = str(item.get("expression", "")).strip()
            if not expr or any(x in expr.lower() for x in ("future", "forward", "next_return", "lead(")):
                raise QwenError("candidate contains empty or lookahead expression")
            clean.append({
                "expression": expr[:500],
                "mechanism": str(item.get("mechanism", mechanism))[:100],
                "rationale": str(item.get("rationale", ""))[:180],
                "hard_flags": _factor_hard_flags(expr),
            })
        return ({"hypothesis": str(obj.get("hypothesis", ""))[:200],
                 "candidates": clean, "count": len(clean)}, 1 + len(clean))

    return _run_tool("local_factor_generate", len(mechanism) + sum(map(len, fields + operators)), fn)


@mcp.tool()
async def local_factor_review(candidates: list[dict], known_signatures: list[str] | None = None) -> dict:
    """因子表达式初审：确定性硬标记 + Qwen 语义审查；不执行回测。"""
    candidates = candidates[:MAX_FACTOR_CANDIDATES]
    known_signatures = [str(x)[:160] for x in (known_signatures or [])[:200]]
    prepared = []
    for item in candidates:
        expr = str(item.get("expression", ""))[:500]
        prepared.append({"expression": expr, "hard_flags": _factor_hard_flags(expr),
                         "mechanism": str(item.get("mechanism", ""))[:100]})
    payload = json.dumps(prepared, ensure_ascii=False)

    def fn(attempt: int):
        strict = "上一次输出不合格；只输出 JSON。" if attempt == 2 else ""
        prompt = (
            "你是量价因子表达式初审器。只输出一个 JSON 对象，禁止解释。"
            '格式：{"reviews":[{"expression":"原样", "decision":"KEEP|REVIEW|DROP",'
            '"risk_flags":["≤5条"], "reason":"≤120字"}]}。'
            f"必须逐项审查 {len(prepared)} 个候选，不能回测，不能判断收益。"
            "DROP 仅用于明确语法/未来函数/跨量纲/不可解释风险；不确定时用 REVIEW。"
            f"{strict}\n已知结构签名：{known_signatures}\n候选：{payload}"
        )
        obj = _json_object(_chat(prompt, num_predict=min(4000, 220 * max(1, len(prepared)))))
        reviews = obj.get("reviews")
        if not isinstance(reviews, list) or len(reviews) != len(prepared):
            raise QwenError("review count mismatch")
        clean = []
        for source, item in zip(prepared, reviews):
            decision = str(item.get("decision", "REVIEW")).upper()
            if decision not in FACTOR_REVIEW_DECISIONS:
                raise QwenError(f"invalid factor decision: {decision}")
            flags = list(source["hard_flags"])
            flags.extend(str(x)[:80] for x in item.get("risk_flags", [])[:5])
            if source["hard_flags"] and decision == "KEEP":
                decision = "REVIEW"
            clean.append({"expression": source["expression"], "decision": decision,
                          "risk_flags": flags[:8], "reason": str(item.get("reason", ""))[:200]})
        return ({"reviews": clean, "count": len(clean), "deterministic_hard_screen": True},
                1 + len(clean))

    return _run_tool("local_factor_review", len(payload), fn)


@mcp.tool()
async def local_failure_cluster(failures: list[dict], context: str = "") -> dict:
    """聚类因子/回测失败模式；只归纳证据，不决定修复方案。"""
    failures = failures[:MAX_FAILURE_ITEMS]
    payload = json.dumps(failures, ensure_ascii=False)[:MAX_INPUT_CHARS]
    context, truncated = _cap_text(context)

    def fn(attempt: int):
        strict = "上一次输出不合格；只输出 JSON。" if attempt == 2 else ""
        prompt = (
            "你是量化研究失败模式聚类器。只输出一个 JSON 对象，禁止解释。"
            '格式：{"clusters":[{"name":"≤50字", "count":整数, '
            '"failure_ids":["原样ID"], "evidence":["≤3条"], "pattern":"≤100字"}], '
            '"unclustered_ids":["原样ID"], "summary":"≤120字"}。'
            "只能依据输入证据归纳，不能编造 ID、数值或因果；不提供自动修复结论。"
            f"{strict}\n上下文：{context}\n失败样本：{payload}"
        )
        obj = _json_object(_chat(prompt, num_predict=1800))
        clusters = obj.get("clusters", [])
        if not isinstance(clusters, list):
            raise QwenError("clusters is not a list")
        clean = []
        for cluster in clusters[:20]:
            ids = [str(x)[:100] for x in cluster.get("failure_ids", [])[:MAX_FAILURE_ITEMS]]
            clean.append({"name": str(cluster.get("name", "unknown"))[:100],
                          "count": max(0, int(cluster.get("count", len(ids)))),
                          "failure_ids": ids,
                          "evidence": [str(x)[:160] for x in cluster.get("evidence", [])[:3]],
                          "pattern": str(cluster.get("pattern", ""))[:180]})
        result = {"clusters": clean,
                  "unclustered_ids": [str(x)[:100] for x in obj.get("unclustered_ids", [])[:MAX_FAILURE_ITEMS]],
                  "summary": str(obj.get("summary", ""))[:220],
                  "truncated_input": truncated}
        return result, 1 + len(clean)

    return _run_tool("local_failure_cluster", len(payload) + len(context), fn)


@mcp.tool()
async def local_monitor_analyze(alert: str, log_tail: str = "", log_path: str = "") -> dict:
    """监控异常语义分析：可恢复性分类 + 失败聚类 + 告警胶囊。

    用于：训练监控巡检触发异常时，把异常摘要 + 日志末尾片段压缩成
    可恢复性判断（recoverable / needs_decision / dangerous）、失败模式聚类
    和给主 Agent 的精简告警。shell 负责采数判阈值，qwen 只做语义层。
    """
    alert, _ = _cap_text(alert)
    if log_tail or log_path:
        try:
            log_tail, truncated, input_meta = _resolve_text_input(log_tail, log_path)
        except QwenError as exc:
            return {"status": "FALLBACK", "reason": str(exc),
                    "instructions": "传入可读 log_path 或有界日志片段。"}
    else:
        truncated, input_meta = False, {"input_mode": "inline"}
    combined = f"异常摘要：{alert}\n日志片段：\n{log_tail}"

    def fn(attempt: int):
        out = _chat(_monitor_prompt(combined, attempt), num_predict=400)
        result = _parse_monitor(out)
        result["truncated_input"] = truncated
        result.update(input_meta)
        return (result, 1 + len(result["evidence"]))

    return _run_tool("local_monitor_analyze", len(combined), fn, input_meta)


if __name__ == "__main__":
    mcp.run()
