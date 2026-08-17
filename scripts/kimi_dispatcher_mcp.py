#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kimi Dispatcher MCP Server（kimi mode）
=======================================

通用任务调度基础设施。与任何业务项目解耦。
与 deepseek_dispatcher_mcp.py 同构，唯一区别：决策层后端从 DeepSeek API
换成本机 Kimi Code CLI（订阅版 OAuth，非 API key）。

架构（Token 经济版）：
    Kimi K3（订阅）= 决策层（只做高价值决策：有界任务拆分 / 架构抉择 / 升级裁决）
    Qwen3.5（Ollama）= 执行层（高 token 消耗的扫描、阅读、检索、压缩、执行、验证）
    Kimi CLI         = Agent Harness（执行层）+ 决策层调用通道

核心原则：
    高 token × 低智力密度  -> Qwen 本地处理（grep/read/pytest/蒸馏/简单修复）
    低 token × 高决策密度  -> K3 处理（只读蒸馏后的 STATE，绝不读 raw context）

本服务暴露三个 MCP Tool（与 DeepSeek 版完全一致）：

    dispatcher_health()
    dispatch_next_task(overall_goal, completed_tasks=None, current_state=None, constraints=None)
        -> 返回唯一一个"有界批次" TASK（allowed_actions + stop_conditions） / DONE / BLOCKED
    request_decision(question, context, options=None, constraints=None)
        -> 返回 DECISION / BLOCKED（架构选择、连续失败升级、高风险操作审批）

每次 dispatch 只返回一个 TASK；禁止返回多个任务；禁止返回 "tasks" 数组。
输入永远是"蒸馏后"的信息，禁止把完整日志 / 完整源码 / 完整聊天记录传给本服务。

与 DeepSeek 版的差异：
    - 无 API key：鉴权由 kimi CLI 的订阅 OAuth 自行处理，本服务不接触任何凭证。
    - 决策调用 = 子进程 `kimi -p <prompt> --output-format stream-json`，
      解析 NDJSON 中最后一条 role=="assistant" 的消息作为模型回复。
    - 通过 --agent-file（tools: []）硬性禁用决策层的一切工具调用；
      通过 --skills-dir 指向空目录，避免注入无关 skill 浪费 token。
    - 无 temperature / max_tokens / reasoning_effort 控制（CLI 不暴露），
      靠 prompt 约束 + 既有 schema 校验兜底。

安全与纪律（与 DeepSeek 版一致）：
    - 决策层不执行 shell / python，不读写目标项目文件。
    - 不使用 eval()。
    - 响应必须经过 JSON 提取 -> schema 校验 -> 安全检查 -> MCP return。
    - malformed（无法提取 JSON）或 schema 不完整（缺字段/字段非法）时，
      只允许一次"严格重试"（带 STRICT_JSON_HINT 重新请求），绝不猜测修复；
      两次都失败才明确失败。

环境变量（均可选）：
    KIMI_DISPATCHER_MODEL    决策层模型别名，默认 kimi-code/k3-256k
    KIMI_DISPATCHER_TIMEOUT  单次 kimi CLI 调用超时秒数，默认 300
    KIMI_CLI_PATH            kimi 二进制路径，默认从 PATH 查找

运行：
    ./venv/bin/python kimi_dispatcher_mcp.py
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# 路径与配置
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "日志"
MODE_DIR = ROOT / "kimi-mode"
# 决策层 agent 定义：tools: [] 硬性禁用一切工具调用
DISPATCHER_AGENT_FILE = MODE_DIR / "dispatcher-agent.md"
# 空 skills 目录：避免 kimi CLI 自动注入用户级 skill，浪费决策层 token
EMPTY_SKILLS_DIR = MODE_DIR / "empty-skills"

LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# 日志：只记录调度信息；本模式不涉及任何 secret
# ---------------------------------------------------------------------------

log = logging.getLogger("kimi_dispatcher")
log.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_DIR / "kimi_dispatcher.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
log.addHandler(_fh)
log.propagate = False  # 日志不进 stdout

# ---------------------------------------------------------------------------
# 加载配置（无 API key；仅模型名 / 超时 / CLI 路径）
# ---------------------------------------------------------------------------

KIMI_MODEL = os.environ.get("KIMI_DISPATCHER_MODEL", "kimi-code/k3-256k").strip()
KIMI_TIMEOUT = int(os.environ.get("KIMI_DISPATCHER_TIMEOUT", "300"))
KIMI_CLI = os.environ.get("KIMI_CLI_PATH", "").strip() or shutil.which("kimi") or "kimi"

# ---------------------------------------------------------------------------
# 常量与校验规则（与 DeepSeek 版完全一致）
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
    """决策层响应未通过 schema / 安全检查。"""


# ---------------------------------------------------------------------------
# 工具函数（纯函数，便于单元测试）
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def infer_next_task_id(completed_tasks: list | None) -> str:
    """根据 completed_tasks 推断下一个任务编号：T001, T002, ..."""
    nums = []
    for t in completed_tasks or []:
        tid = t.get("task_id") if isinstance(t, dict) else None
        if isinstance(tid, str):
            m = re.fullmatch(r"T(\d+)", tid)
            if m:
                nums.append(int(m.group(1)))
    nxt = (max(nums) + 1) if nums else 1
    return f"T{nxt:03d}"


def extract_json(content: str):
    """从决策层文本响应中提取 JSON 对象。

    允许剥离 ```json 代码围栏。失败时抛出 ValidationError。
    """
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("决策层返回了空内容，无法提取 JSON")

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
    content = call_kimi(prompt, system_prompt=system_prompt)
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
# Prompt 构建（与 DeepSeek 版完全一致）
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
        tid = t.get("task_id", "?")
        st = t.get("status", "?")
        summ = t.get("summary", "")
        # 蒸馏：只保留 T001: PASS + 一行摘要，绝不携带 raw tool output
        summary_lines.append(f"{tid}: {st} {summ}".strip())

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
# Kimi CLI 调用（订阅版 OAuth，无 API key）
# ---------------------------------------------------------------------------


def parse_stream_json(stdout: str) -> str:
    """从 kimi CLI stream-json 输出（NDJSON）中取最后一条 assistant 消息。

    每行一个 JSON 对象；只有 role=="assistant" 的行携带模型回复 content，
    meta 行（version / session.resume_hint 等）一律忽略。
    找不到 assistant 内容时抛 DispatcherError。
    """
    content: str | None = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # 容忍非 JSON 行（横幅 / 警告等）
        if obj.get("role") == "assistant" and isinstance(obj.get("content"), str):
            content = obj["content"]
    if content is None or not content.strip():
        raise DispatcherError("kimi CLI 输出中未找到 assistant 消息内容")
    return content


def call_kimi(prompt: str, system_prompt: str = SYSTEM_PROMPT, retries: int = 2) -> str:
    """通过 kimi CLI 无头模式调用决策层模型并返回原始文本内容。

    实现要点：
    - CLI 无独立 system role，system_prompt 与 user prompt 拼接为单条 -p 输入。
    - --output-format stream-json：NDJSON 输出，assistant 行即为纯模型回复，
      不混入 thinking / 横幅 / session 提示。
    - --agent-file 指向 tools: [] 的 agent 定义，硬性禁用决策层一切工具调用。
    - --skills-dir 指向空目录，避免注入无关 skill。
    - 消耗的是订阅额度（周额度 + 5 小时滚动限流窗）；单次调用输入 2K~8K token。

    重试预算（与 DeepSeek 版同构）：
    - 非零退出码 / 超时 / 输出中无 assistant 内容：内部有限重试（最多 retries 次），
      带退避。内部重试耗尽仍失败，才由上层 call_and_validate 带 STRICT_JSON_HINT
      严格重试一次。
    - CLI 二进制不存在（FileNotFoundError）属配置错误，立即失败不重试。
    """
    full_prompt = system_prompt + "\n\n---\n\n" + prompt
    cmd = [
        KIMI_CLI,
        "-p", full_prompt,
        "--output-format", "stream-json",
        "-m", KIMI_MODEL,
    ]
    if DISPATCHER_AGENT_FILE.exists():
        cmd += ["--agent-file", str(DISPATCHER_AGENT_FILE)]
    if EMPTY_SKILLS_DIR.is_dir():
        cmd += ["--skills-dir", str(EMPTY_SKILLS_DIR)]

    last_exc: DispatcherError | None = None
    for attempt in range(retries + 1):
        if attempt > 0:
            log.info("kimi CLI 瞬时失败，重试 attempt=%d/%d", attempt, retries)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=KIMI_TIMEOUT,
                cwd=str(ROOT),
            )
        except FileNotFoundError as exc:
            # 配置错误，重试无意义
            raise DispatcherError(f"kimi CLI 未找到: {KIMI_CLI} ({exc})") from exc
        except subprocess.TimeoutExpired:
            last_exc = DispatcherError(f"kimi CLI 调用超时（>{KIMI_TIMEOUT}s）")
        except OSError as exc:
            last_exc = DispatcherError(f"kimi CLI 启动失败: {type(exc).__name__}: {exc}")
        else:
            if proc.returncode != 0:
                last_exc = DispatcherError(
                    "kimi CLI 退出码 %d: %.300s"
                    % (proc.returncode, (proc.stderr or proc.stdout or "").strip())
                )
            else:
                try:
                    return parse_stream_json(proc.stdout)
                except DispatcherError as exc:
                    last_exc = exc

        if attempt < retries:
            time.sleep(1 + attempt)  # 简单退避

    raise last_exc


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("kimi-dispatcher")


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
        KIMI_MODEL,
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
        KIMI_MODEL,
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
    """Dispatcher 健康检查。不调用决策层模型，只报告服务本身状态。

    能调用本工具即代表 MCP 注册成功；不能调用即代表 Dispatcher 不可用。
    本模式无 API key，改为报告 kimi CLI 与订阅登录凭证是否存在。
    """
    cli_path = shutil.which("kimi") if KIMI_CLI == "kimi" else KIMI_CLI
    credentials_present = (Path.home() / ".kimi-code" / "credentials").exists()
    return {
        "status": "OK",
        "server": "kimi-dispatcher",
        "kimi_cli_present": bool(cli_path),
        "kimi_cli_path": cli_path,
        "subscription_credentials_present": credentials_present,
        "model": KIMI_MODEL,
        "time": _now(),
    }


if __name__ == "__main__":
    log.info("kimi_dispatcher starting | model=%s | stdio transport", KIMI_MODEL)
    mcp.run()
