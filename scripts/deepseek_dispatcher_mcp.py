#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek Dispatcher MCP Server
================================

通用任务调度基础设施。与任何业务项目解耦。

架构（Token 经济版）：
    DeepSeek    = 决策层（只做高价值决策：有界任务拆分 / 架构抉择 / 升级裁决）
    Qwen3.5     = 执行层（高 token 消耗的扫描、阅读、检索、压缩、执行、验证）
    Continue CLI = Agent Harness（Agent Loop）

核心原则：
    高 token × 低智力密度  -> Qwen 本地处理（grep/read/pytest/蒸馏/简单修复）
    低 token × 高决策密度  -> DeepSeek 处理（只读蒸馏后的 STATE，绝不读 raw context）

本服务暴露三个 MCP Tool：

    dispatcher_health()
    dispatch_next_task(overall_goal, completed_tasks=None, current_state=None, constraints=None)
        -> 返回唯一一个"有界批次" TASK（allowed_actions + stop_conditions） / DONE / BLOCKED
    request_decision(question, context, options=None, constraints=None)
        -> 返回 DECISION / BLOCKED（架构选择、连续失败升级、高风险操作审批）

每次 dispatch 只返回一个 TASK；禁止返回多个任务；禁止返回 "tasks" 数组。
输入永远是"蒸馏后"的信息，禁止把完整日志 / 完整源码 / 完整聊天记录传给本服务。

安全与纪律：
    - DeepSeek 不执行 shell / python，不读写目标项目文件。
    - API key 只从 .env.local 读取，绝不写入日志 / stdout / 源码 / README / 测试。
    - 不使用 eval()。
    - 响应必须经过 JSON 提取 -> schema 校验 -> 安全检查 -> MCP return。
    - malformed（无法提取 JSON）或 schema 不完整（缺字段/字段非法）时，
      只允许一次"严格重试"（带 STRICT_JSON_HINT 重新请求），绝不猜测修复；
      两次都失败才明确失败。

运行：
    ./venv/bin/python deepseek_dispatcher_mcp.py
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from openai import OpenAI

# ---------------------------------------------------------------------------
# 路径与配置
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env.local"
LOG_DIR = ROOT / "日志"
LEDGER_PATH = ROOT / "调度状态" / "任务账本.json"

LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# 日志：只记录调度信息，绝不记录任何 secret
# ---------------------------------------------------------------------------

log = logging.getLogger("deepseek_dispatcher")
log.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_DIR / "dispatcher.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
log.addHandler(_fh)
log.propagate = False  # 日志不进 stdout，保护 API key

# ---------------------------------------------------------------------------
# 加载配置
# ---------------------------------------------------------------------------

load_dotenv(ENV_FILE)

# 模型来源：优先 GPT_*（codex CLI 在用的 GPT API，2026-08-14 切换），
# 缺失时回退 DEEPSEEK_*（保留 DeepSeek 配置）。
# 变量名沿用 DEEPSEEK_* 以最小化改动面；实际值由 GPT_* 优先决定。
DEEPSEEK_API_KEY = (
    os.environ.get("GPT_API_KEY", "").strip()
    or os.environ.get("DEEPSEEK_API_KEY", "").strip()
)
DEEPSEEK_MODEL = (
    os.environ.get("GPT_MODEL", "").strip()
    or os.environ.get("DEEPSEEK_MODEL", "").strip()
)
DEEPSEEK_BASE_URL = (
    os.environ.get("GPT_BASE_URL", "").strip()
    or os.environ.get("DEEPSEEK_BASE_URL", "").strip()
)
# 当前唯一 fallback：仅当 DEEPSEEK_MODEL 缺失时使用，并在日志中显式记录。
# 禁止静默切换到已废弃模型（如 deepseek-chat）。
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_BASE_URL = "https://api.deepseek.com"

if not DEEPSEEK_API_KEY:
    raise SystemExit(
        "FATAL: 未找到 API key。请在 .env.local 中配置 GPT_API_KEY（codex API）或 DEEPSEEK_API_KEY。"
    )

if not DEEPSEEK_MODEL:
    log.warning("模型未配置，使用代码中明确记录的 fallback: %s", DEFAULT_MODEL)
    DEEPSEEK_MODEL = DEFAULT_MODEL

if not DEEPSEEK_BASE_URL:
    DEEPSEEK_BASE_URL = DEFAULT_BASE_URL

# ---------------------------------------------------------------------------
# 常量与校验规则
# ---------------------------------------------------------------------------

ALLOWED_STATUSES = {"TASK", "DONE", "BLOCKED", "DECISION"}

REQUIRED_TASK_FIELDS = [
    "task_id",
    "title",
    "goal",
    "reason",
    "inputs",
    "allowed_paths",
    "forbidden_paths",
    "actions",
    "allowed_actions",   # 有界批次：授权 Executor 本地完成的全部确定性子步骤
    "stop_conditions",   # 有界批次：出现任一条件即必须停止本地执行
    "acceptance_criteria",
    "do_not_do",
]

STRING_TASK_FIELDS = ("title", "goal", "reason")
LIST_TASK_FIELDS = (
    "inputs",
    "allowed_paths",
    "forbidden_paths",
    "actions",
    "allowed_actions",
    "stop_conditions",
    "do_not_do",
)

TASK_ID_RE = re.compile(r"^T\d{3,}$")

DISALLOWED_KEYS = ("tasks", "task_list", "plan", "future_tasks")


class DispatcherError(Exception):
    """Dispatcher 明确失败时抛出。"""


class ValidationError(DispatcherError):
    """DeepSeek 响应未通过 schema / 安全检查。"""


# ---------------------------------------------------------------------------
# 工具函数（纯函数，便于单元测试）
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def infer_next_task_id(completed_tasks: list | None) -> str:
    """根据 completed_tasks 推断下一个任务编号：T001, T002, ...

    兼容两种输入格式：
      - dict:  {"task_id": "T001", ...}
      - str:   "T001: PASS xxx"（userRules 规定的蒸馏摘要格式）
    """
    nums = []
    for t in completed_tasks or []:
        if isinstance(t, dict):
            tid = t.get("task_id")
        elif isinstance(t, str):
            # 从字符串摘要头部提取 T\d{3,}
            m = re.match(r"^T(\d{3,})\b", t.strip())
            tid = m.group(0) if m else None
        else:
            tid = None
        if isinstance(tid, str):
            m = re.fullmatch(r"T(\d+)", tid)
            if m:
                nums.append(int(m.group(1)))
    nxt = (max(nums) + 1) if nums else 1
    return f"T{nxt:03d}"


def extract_json(content: str):
    """从 DeepSeek 文本响应中提取 JSON 对象。

    允许剥离 ```json 代码围栏。失败时抛出 ValidationError。
    """
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("DeepSeek 返回了空内容，无法提取 JSON")

    text = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValidationError("响应中未找到 JSON 对象")

    candidate = text[start : end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"malformed JSON，解析失败: {exc}") from exc

    return payload


def validate_response(
    payload,
    expected_task_id: str | None = None,
    completed_task_ids: list | None = None,
) -> dict:
    """严格的响应 schema 与安全检查。

    任何不合规输入都会抛出 ValidationError，绝不猜测修复。
    """
    if not isinstance(payload, dict):
        raise ValidationError(
            f"响应必须是单个 JSON 对象，实际为 {type(payload).__name__}"
        )

    for key in DISALLOWED_KEYS:
        if key in payload:
            raise ValidationError(f"响应包含被禁止的键: '{key}'")

    status = payload.get("status")
    if status not in ALLOWED_STATUSES:
        raise ValidationError(
            f"status 非法: {status!r}（只允许 TASK / DONE / BLOCKED）"
        )

    if status == "TASK":
        for field in REQUIRED_TASK_FIELDS:
            if field not in payload:
                raise ValidationError(f"TASK 缺少必需字段: '{field}'")

        task_id = payload["task_id"]
        if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
            raise ValidationError(f"task_id 非法: {task_id!r}")

        if completed_task_ids and task_id in completed_task_ids:
            raise ValidationError(
                f"task_id {task_id} 已经在 completed_tasks 中，禁止重复分发"
            )

        if expected_task_id and task_id != expected_task_id:
            raise ValidationError(
                f"task_id {task_id} 与 Dispatcher 推断的下一编号 {expected_task_id} 不一致"
            )

        for field in STRING_TASK_FIELDS:
            value = payload[field]
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"TASK 字段 '{field}' 必须是非空字符串")

        for field in LIST_TASK_FIELDS:
            if not isinstance(payload[field], list):
                raise ValidationError(f"TASK 字段 '{field}' 必须是数组")

        # 有界批次：allowed_actions / stop_conditions 必须非空
        for field in ("allowed_actions", "stop_conditions"):
            if not payload[field]:
                raise ValidationError(f"有界批次 TASK 的 '{field}' 必须是非空数组")

        ac = payload["acceptance_criteria"]
        if not isinstance(ac, list) or not ac:
            raise ValidationError("TASK 的 acceptance_criteria 必须是非空数组")

        return payload

    if status == "DECISION":
        decision = payload.get("decision")
        if not isinstance(decision, str) or not decision.strip():
            raise ValidationError("DECISION 必须包含非空 decision 字段")
        instructions = payload.get("instructions")
        if not isinstance(instructions, list) or not instructions:
            raise ValidationError("DECISION 的 instructions 必须是非空数组")

    # DONE / BLOCKED / DECISION
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError(f"{status} 必须包含非空 reason")

    if status == "BLOCKED":
        if "required_information" not in payload:
            raise ValidationError("BLOCKED 缺少 required_information 字段")
        if not isinstance(payload["required_information"], list):
            raise ValidationError("BLOCKED 的 required_information 必须是数组")

    return payload


# 严格重试提示：malformed / schema 不完整内容不"猜测修复"，
# 而是带更强约束重新请求一次。
STRICT_JSON_HINT = (
    "\n\n重要：你的响应必须是合法 JSON 对象本身，"
    "并且必须包含格式中列出的所有必填字段，一个都不能少。"
    "不要输出任何解释文字，不要使用 markdown 代码围栏，"
    "不要输出 JSON 之外的任何内容。"
)


def _call_extract_validate(
    prompt: str,
    system_prompt: str,
    expected_task_id: str | None,
    completed_task_ids: list | None,
) -> dict:
    """单次 调用 -> JSON 提取 -> schema 校验。任何失败抛 ValidationError。"""
    content = call_deepseek(prompt, system_prompt=system_prompt)
    payload = extract_json(content)
    return validate_response(
        payload,
        expected_task_id=expected_task_id,
        completed_task_ids=completed_task_ids,
    )


def call_and_validate(
    prompt: str,
    system_prompt: str | None = None,
    expected_task_id: str | None = None,
    completed_task_ids: list | None = None,
) -> dict:
    """调用 -> 提取 -> 校验，整个链路带一次"严格重试"。

    malformed（无法提取 JSON）或 schema 不完整（缺字段/字段非法）时，
    不猜测修复，而是带 STRICT_JSON_HINT 重新请求一次。
    两次都失败则按纪律明确失败。
    """
    if system_prompt is None:  # 延迟解析，避免模块级定义顺序问题
        system_prompt = SYSTEM_PROMPT
    try:
        return _call_extract_validate(
            prompt, system_prompt, expected_task_id, completed_task_ids
        )
    except DispatcherError as exc:
        # 覆盖空 content（DispatcherError）与 malformed / schema 不完整（ValidationError）
        log.info("响应未通过提取/校验，进行一次严格重试: %.100s", str(exc))
        return _call_extract_validate(
            prompt + STRICT_JSON_HINT,
            system_prompt,
            expected_task_id,
            completed_task_ids,
        )


# ---------------------------------------------------------------------------
# Prompt 构建
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是任务调度器 (Dispatcher)，是决策层，不是执行者。

你的职责：
- 只读"蒸馏后"的输入，绝不期待 raw 上下文。
- 把复杂目标拆成"有界批次"(bounded batch)：一次授权 Executor 本地完成 3~5 个确定性子步骤，而不是一个原子动作。
- 每个有界批次必须给出 allowed_actions（授权子步骤）与 stop_conditions（停止条件）。
- 根据当前状态动态决定唯一下一步，不要提前生成完整计划，不要分发未来任务。

输入纪律（你收到的永远是压缩信息）：
- completed_tasks 是摘要（如 "T001: PASS alpha.txt 已创建并验证"），不是完整记录。
- current_state 是最新蒸馏 STATE，不是完整日志 / 完整源码 / 完整聊天历史。
- 不要因为没有看到原始日志 / 完整代码而要求更多原始材料；需要决策依据时指明需要"蒸馏后的"哪一类事实。

你绝对禁止：
- 执行 shell / python、读写任何项目文件、修改代码、声称自己执行或验证了什么。
- 一次分发多个 TASK，返回 "tasks" 数组，或提前分发未来任务。
- 重复分发已经完成的任务。

响应规则（必须严格遵守）：
1. 只输出一个 JSON 对象，不要输出任何解释性文字。
2. 每次只返回一个有界批次任务。
3. 四种状态之一：TASK、DONE、BLOCKED、DECISION。

TASK 是有界批次，格式（所有字段必填）：
{"status":"TASK","task_id":"<由调度器指定>","title":"...","goal":"...","reason":"...","inputs":[],"allowed_paths":[],"forbidden_paths":[],"actions":[],"allowed_actions":["授权本地完成的确定性子步骤1","子步骤2","子步骤3"],"stop_conditions":["出现任一条件即停止本地执行"],"acceptance_criteria":["可客观验收的标准"],"do_not_do":[]}

allowed_actions 数量要求：3~5 个，每个必须足够小、可独立客观验收。禁止只给 1 个原子动作（那是旧架构）；禁止给超过 5 个。

stop_conditions 要求：明确列出 Executor 必须停止本地执行并回到 Dispatcher 的情况（如：任一验收失败、出现意外文件状态、需要操作 allowed_paths 之外、需要跨模块修改）。

DONE 格式（目标已全部完成）：
{"status":"DONE","reason":"..."}

BLOCKED 格式（信息不足无法继续）：
{"status":"BLOCKED","reason":"...","required_information":[...]}

DECISION 格式（当通过 request_decision 被问询时）：
{"status":"DECISION","decision":"<明确决定>","reason":"...","instructions":["可执行要点"]}

任务粒度要求：有界批次内的每个子步骤都必须可独立完成、可客观验收。
当前任务编号已由调度器指定，TASK 的 task_id 必须与指定编号一致。
"""

DECISION_PROMPT = """你是决策层 (Dispatcher)。Executor 需要你做一项具体决策，而不是分发完整计划。

输入只有蒸馏后的 context：相关代码摘录、verified facts、失败摘要、选项。

你的职责：
- 只做一个明确、可执行的决定（决策 / 修复方向 / 是否批准高风险操作）。
- 决定必须让本地 Executor 能立刻行动，不需要再回来问。

你绝对禁止：
- 执行任何工作、声称自己验证了什么。
- 返回完整计划、多个任务、tasks 数组。
- 要求原始日志 / 完整源码（你只有蒸馏后的 context，就在 context 字段里）。

响应规则：
1. 只输出一个 JSON 对象。
2. 两种状态之一：DECISION、BLOCKED。

DECISION 格式：
{"status":"DECISION","decision":"<明确决定>","reason":"<为什么>","instructions":["可执行要点"]}

BLOCKED 格式：
{"status":"BLOCKED","reason":"...","required_information":["还缺哪一类蒸馏后信息"]}
"""


def build_dispatcher_prompt(
    overall_goal: str,
    completed_tasks: list | None = None,
    current_state: str | None = None,
    constraints: str | None = None,
    expected_task_id: str = "T001",
) -> str:
    completed = completed_tasks or []
    summary_lines = []
    for t in completed:
        # 兼容两种输入：dict 对象（task_id/status/summary）或字符串摘要（"T001: PASS xxx"）
        if isinstance(t, dict):
            tid = t.get("task_id", "?")
            st = t.get("status", "?")
            summ = t.get("summary", "")
            summary_lines.append(f"{tid}: {st} {summ}".strip())
        elif isinstance(t, str):
            summary_lines.append(t.strip())
        # 蒸馏：只保留 T001: PASS + 一行摘要，绝不携带 raw tool output

    sections = [
        "## overall_goal",
        overall_goal or "(未提供)",
        "",
        "## completed_tasks（蒸馏摘要）",
        "\n".join(summary_lines) if summary_lines else "(无已完成任务)",
        "",
        "## current_state（蒸馏 STATE）",
        current_state or "(未提供)",
        "",
        "## constraints",
        constraints or "(无)",
        "",
        "## 下一步",
        f"你必须只返回唯一一个有界批次任务，其 task_id 必须是 {expected_task_id}。",
        "不要分发 future_tasks，不要输出 tasks 数组。",
    ]
    return "\n".join(sections)


def build_decision_prompt(
    question: str,
    context: str,
    options: list | None = None,
    constraints: str | None = None,
) -> str:
    """构建 request_decision 的 prompt。context 必须是蒸馏后的信息。"""
    sections = [
        "## question",
        question or "(未提供)",
        "",
        "## context（蒸馏后，禁止期待 raw 日志 / 完整源码）",
        context or "(未提供)",
        "",
        "## options（可选）",
        "\n".join(f"- {o}" for o in (options or [])) if options else "(未提供)",
        "",
        "## constraints",
        constraints or "(无)",
        "",
        "## 要求",
        "只返回一个明确、可执行的 DECISION（或 BLOCKED），不要返回计划 / tasks 数组。",
    ]
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# DeepSeek API 调用
# ---------------------------------------------------------------------------

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


def call_deepseek(prompt: str, system_prompt: str = SYSTEM_PROMPT, retries: int = 2) -> str:
    """调用 DeepSeek API 并返回原始文本内容。

    性能与可靠性（2026-08-12 实测，deepseek-v4-flash）：
    - reasoning_effort="none" 关闭推理：dispatch 52s -> 6.0s（4/4 通过严格校验），
      request_decision 3.1s；且空响应率从约 50% 降至 0/4。
    - max_tokens=1000：TASK JSON 输出实测约 600 token，1000 留余量；
      同时硬性封顶最坏延迟（无推理时 ~6s，留限流重试余量）。

    重试预算（省 token）：
    - 网络 / 鉴权 / 限流等异常：内部有限重试（最多 retries 次），带退避。
    - 空 content：历史实测为 DeepSeek API 间歇性空响应（约 50% 概率、与 prompt 无关），
      推理关闭后已不出现；仍按"瞬时失败"内部有限重试兜底，内部重试耗尽仍为空，
      才由上层 call_and_validate 带 STRICT_JSON_HINT 严格重试一次。
    """
    last_exc: DispatcherError | None = None
    for attempt in range(retries + 1):
        if attempt > 0:
            log.info("DeepSeek API 瞬时失败，重试 attempt=%d/%d", attempt, retries)
        try:
            resp = _get_client().chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1000,
                temperature=0,
                reasoning_effort="none",
            )
        except Exception as exc:  # 网络 / 鉴权 / 限流等
            last_exc = DispatcherError(
                f"DeepSeek API 调用失败: {type(exc).__name__}: {exc}"
            )
        else:
            content = resp.choices[0].message.content
            if content and content.strip():
                return content
            last_exc = DispatcherError("DeepSeek 返回了空 content，无法提取任务")

        if attempt < retries:
            time.sleep(1 + attempt)  # 简单退避

    raise last_exc


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("deepseek-dispatcher")


@mcp.tool()
async def dispatch_next_task(
    overall_goal: str,
    completed_tasks: list | None = None,
    current_state: str | None = None,
    constraints: str | None = None,
) -> dict:
    """根据总体目标与当前状态，返回唯一一个下一步 TASK（或 DONE / BLOCKED）。

    每次调用只返回一个任务。
    """
    request_id = uuid.uuid4().hex[:12]
    completed_tasks = completed_tasks or []
    expected_task_id = infer_next_task_id(completed_tasks)
    completed_ids = [
        t.get("task_id")
        for t in completed_tasks
        if isinstance(t, dict) and t.get("task_id")
    ]

    prompt = build_dispatcher_prompt(
        overall_goal=overall_goal,
        completed_tasks=completed_tasks,
        current_state=current_state,
        constraints=constraints,
        expected_task_id=expected_task_id,
    )

    log.info(
        "request_id=%s | goal=%.60s | completed=%d | expected_task_id=%s | model=%s",
        request_id,
        (overall_goal or "").replace("\n", " "),
        len(completed_tasks),
        expected_task_id,
        DEEPSEEK_MODEL,
    )

    try:
        validated = call_and_validate(
            prompt,
            expected_task_id=expected_task_id,
            completed_task_ids=completed_ids,
        )
    except DispatcherError as exc:
        log.error(
            "request_id=%s | FAILED | error_type=%s | message=%.120s",
            request_id,
            type(exc).__name__,
            str(exc),
        )
        raise DispatcherError(
            f"[request_id={request_id}] {type(exc).__name__}: {exc}"
        ) from exc

    log.info(
        "request_id=%s | OK | status=%s | task_id=%s | validated=True",
        request_id,
        validated.get("status"),
        validated.get("task_id", "-"),
    )
    return validated


@mcp.tool()
async def request_decision(
    question: str,
    context: str,
    options: list | None = None,
    constraints: str | None = None,
) -> dict:
    """在 Executor 需要高层决策时调用（架构/方向选择、连续失败升级、高风险操作审批）。

    只接收蒸馏后的 context：相关代码摘录、verified facts、失败摘要、候选选项。
    绝不接收 raw 日志 / 完整源码 / 完整聊天历史。

    返回 DECISION（明确决定 + 可执行 instructions）或 BLOCKED。
    """
    request_id = uuid.uuid4().hex[:12]
    prompt = build_decision_prompt(
        question=question,
        context=context,
        options=options,
        constraints=constraints,
    )

    log.info(
        "request_id=%s | DECISION-REQ | question=%.60s | model=%s",
        request_id,
        (question or "").replace("\n", " "),
        DEEPSEEK_MODEL,
    )

    try:
        validated = call_and_validate(prompt, system_prompt=DECISION_PROMPT)
    except DispatcherError as exc:
        log.error(
            "request_id=%s | DECISION FAILED | error_type=%s | message=%.120s",
            request_id,
            type(exc).__name__,
            str(exc),
        )
        raise DispatcherError(
            f"[request_id={request_id}] {type(exc).__name__}: {exc}"
        ) from exc

    log.info(
        "request_id=%s | DECISION OK | status=%s | validated=True",
        request_id,
        validated.get("status"),
    )
    return validated


@mcp.tool()
async def dispatcher_health() -> dict:
    """Dispatcher 健康检查。不调用 DeepSeek API，只报告服务本身状态。

    能调用本工具即代表 MCP 注册成功；不能调用即代表 Dispatcher 不可用。
    绝不返回 API key 本身，只返回是否存在。
    """
    return {
        "status": "OK",
        "server": "deepseek-dispatcher",
        "api_key_present": bool(DEEPSEEK_API_KEY),
        "model": DEEPSEEK_MODEL,
        "time": _now(),
    }


if __name__ == "__main__":
    log.info("deepseek_dispatcher starting | model=%s | stdio transport", DEEPSEEK_MODEL)
    mcp.run()
