#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Continue CLI PreToolUse 强制蒸馏守卫（把"自觉"变"强制"）。

配 Continue hooks（dispatcher-agent-v2.yaml）使用：
  hooks:
    - name: force-distill-guard
      matcher: ".*"
      hooks:
        - type: command
          command: .../scripts/force_distill.py
          timeout: 10
          statusMessage: "强制蒸馏守卫"

职责（确定性、可配置 fail-closed、零本地 Qwen 推理）：
  1. 训练保护检测：存在 MLX/LoRA 训练进程 → 注入"禁止 local_*/LocalQwen"胶囊。
  2. Read 大文件守卫：目标文件 >50K 字符或预计 >100 行 → 注入"先 local_summarize_files"胶囊。
  3. Bash/Search/Grep 大输出守卫：命令命中危险输出模式（log/cat/json 整读）→ 注入
     "长文本只传 source_path 给 qxen_cd_longtext_distill / 失败日志只传
     log_path 给 local_extract_failure"胶囊。
  4. 写审计标记：每次触发记录到 日志/force_distill_guard.log（一行 JSON），
     供 scripts/audit_v2_session.py 或人工核查。

Continue 把 stdin 传入 {type, tool_name, tool_input, cwd, session_id, ...}，
本脚本把胶囊写到 stdout（Continue 注入上下文）；默认异常 fail-open。设置
`FORCE_DISTILL_STRICT=1` 后，命中守卫会返回 deny 并以非零退出阻止工具调用。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

GUARD_LOG = ROOT / "日志" / "force_distill_guard.log"
# 软阈值（与 AGENTS.md token 经济铁律对齐）
READ_LINE_LIMIT = 100          # Read 单次 >100 行 = 违规
READ_CHAR_LIMIT = 1_500        # 直接 Read 的上下文预算上限
OUTPUT_CHAR_LIMIT = 2_000      # 单条工具输出 >2K 字符 = raw_bypass
LARGE_FILE_CHARS = 50_000      # 超过此体积的文件 Read 直接触发守卫
STRICT_MODE = os.environ.get("FORCE_DISTILL_STRICT", "0") == "1"

# Bash/Search 输出可能超大的危险模式
BIG_OUTPUT_PATTERNS = (
    r"cat\s+\S+\.(log|json|jsonl|pkl|csv)(\s|$)",
    r"tail\s+-[0-9]+\s+\S+\.log",
    r"head\s+-[0-9]+\s+\S+\.(log|json|jsonl)",
    r"sed\s+-n\s+['\"]?1,[0-9]{3,}p['\"]?\s+\S+",
    r"(?:cat|head\s+-[0-9]+)\s+\S+\.(md|txt|rst)(\s|$)",
    r"less\s|more\s|vim\s|nano\s",
    r"python.*(print|json\.dump).*\.json",
    r"find\s+.*-name.*\.(jsonl|log|pkl)",
    r"rg\s+.*\.(jsonl|log)",
)
STRICT_LONG_READ_PATTERNS = (
    r"sed\s+-n\s+['\"]?1,[0-9]{3,}p['\"]?\s+\S+",
    r"(?:cat|head\s+-[0-9]+)\s+\S+\.(md|txt|rst)(\s|$)",
)
SKILL_INSTRUCTION_RE = re.compile(r"(^|[/\\])SKILL\.md($|[\\s'\"`])")
STRUCTURED_AUDIT_HINTS = re.compile(
    r"文档.{0,12}(核对|审查|一致)|核对.{0,12}(代码|文档)|"
    r"(DOCX|docx).{0,20}(代码|一致|核对|审查)|"
    r"(代码|配置).{0,12}(一致|描述|对照)",
    re.I,
)

TRAIN_PATTERNS = (
    "mlx_lm.lora", "mlx_lm lora", "mlx_lm_lora",
    "python.*train.*\\.py", "train.*\\.py.*mlx",
    "qxen_joint_train", "run_minimal_training", "r3_split_train",
)


def training_processes() -> list[str]:
    """检测训练进程（确定性，只读 shell，不加载任何模型）。"""
    try:
        proc = os.popen("ps -axo pid=,command=")
        lines = proc.read().splitlines()
        proc.close()
    except OSError:
        return []
    found = []
    for line in lines:
        lower = line.lower()
        if "force_distill" in lower or "session_bootstrap" in lower:
            continue
        if any(re.search(p, lower) for p in TRAIN_PATTERNS):
            found.append(line.strip()[:100])
    return found[:3]


def file_size_info(path_str: str) -> tuple[int, int] | None:
    """返回 (行数, 字符数)；无法读取返回 None。"""
    path = Path(path_str).expanduser()
    if not path.is_file():
        return None
    try:
        stat = path.stat()
        if stat.st_size > 2_000_000:      # >2MB 不再数行，直接按大文件处理
            return -1, stat.st_size
        text = path.read_text(encoding="utf-8", errors="replace")
        return text.count("\n") + 1, len(text)
    except OSError:
        return None


def write_guard_log(entry: dict) -> None:
    try:
        GUARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with GUARD_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def build_capsule(parts: list[str]) -> str:
    text = "\n".join(parts)
    return text[:1500]


def _is_safe_run(command: str) -> bool:
    return bool(re.match(r"^\s*(?:\S*/)?safe_run\.sh\s+--\b", command))


def _structured_audit(payload: dict, command: str = "") -> bool:
    """Allow deterministic structure/code comparison workflows to read raw structure."""
    values = [command]
    for key in ("task", "task_type", "user_prompt", "prompt", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
    return bool(STRUCTURED_AUDIT_HINTS.search("\n".join(values)))


def _is_skill_instruction_path(path_str: str) -> bool:
    return Path(path_str).name == "SKILL.md"


def _command_reads_skill_instruction(cmd: str) -> bool:
    return bool(SKILL_INSTRUCTION_RE.search(cmd))


def main() -> int:
    stdin_raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(stdin_raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    tool = payload.get("tool_name") or payload.get("type") or ""
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id") or "unknown"
    task_is_structured_audit = _structured_audit(payload)

    parts: list[str] = []
    updated_command: str | None = None
    log_entry = {"event": "PreToolUse", "tool": tool, "cwd": cwd,
                 "session_id": session_id[:16]}
    long_read_deny = False

    # ---- 1. 训练保护（最优先，适用于任何工具）----
    trains = training_processes()
    if trains:
        parts.append("[force-distill] 训练保护模式 ON：存在训练进程，"
                     "本批次禁止调用任何 local_*/LocalQwen/QXEN 模型推理；"
                     "改用 shell 确定性监控。进程: " + "; ".join(trains))
        log_entry["train_protect"] = trains

    # ---- 2. Read 大文件守卫 ----
    if tool in ("Read", "read"):
        fpath = tool_input.get("file_path") or tool_input.get("path") or ""
        if fpath:
            info = file_size_info(fpath)
            if info:
                n_lines, n_chars = info
                if _is_skill_instruction_path(fpath):
                    parts.append(
                        "[force-distill] Skill 指令文件例外：SKILL.md 必须由主 Agent "
                        "按 Codex 规则完整读取；不使用 QXEN longtext 替代原文。"
                        "QXEN 只可在事后生成交接/摘要胶囊。"
                    )
                    log_entry["action"] = "skill_instruction_verbatim_allow"
                    log_entry["path"] = fpath
                elif (not task_is_structured_audit and
                        (n_lines == -1 or n_lines > READ_LINE_LIMIT or
                         n_chars > READ_CHAR_LIMIT)):
                    parts.append(
                        f"[force-distill] Read 守卫：{Path(fpath).name} "
                        f"约 {n_lines if n_lines > 0 else '?'} 行 / "
                        f"{n_chars} 字符，超过 {READ_LINE_LIMIT} 行 / "
                        f"{READ_CHAR_LIMIT} 字符铁律。先 local_summarize_files "
                        f"（≤3 行/文件）定位，再 Read ≤{READ_LINE_LIMIT} 行小段。"
                    )
                    log_entry["action"] = "read_guard"
                    log_entry["path"] = fpath

    # ---- 3. Bash/Search/Grep 大输出守卫 ----
    if tool in ("Bash", "bash", "Search", "search", "Grep", "grep"):
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = str(tool_input.get("command") or tool_input.get("pattern") or "")
        task_is_structured_audit = _structured_audit(payload, cmd)
        skill_instruction_read = cmd and _command_reads_skill_instruction(cmd)
        if skill_instruction_read:
            parts.append(
                "[force-distill] Skill 指令文件例外：命令读取 SKILL.md 时，默认按 "
                "Codex skill 规则完整读取，不要求 qxen_cd_longtext_distill；"
                "若需要复用，可在事后对已读结论生成短交接胶囊。"
            )
            log_entry["action"] = "skill_instruction_verbatim_allow"
            log_entry["command"] = cmd[:120]
        elif cmd and STRICT_MODE and tool in ("Bash", "bash") and not _is_safe_run(cmd):
            updated_command = f"{ROOT}/scripts/safe_run.sh -- bash -lc {shlex.quote(cmd)}"
            parts.append(
                "[force-distill] Bash 已自动重写为 safe_run；原始输出先落盘并按 "
                "1500 字节预算截断，超限后再调用 QXEN-CD/LocalQwen。"
            )
            log_entry["action"] = "rewrite_safe_run"
            log_entry["command"] = cmd[:120]
        elif cmd and any(re.search(p, cmd) for p in BIG_OUTPUT_PATTERNS):
            parts.append(
                "[force-distill] 输出守卫：该命令可能输出 >2K 字符。"
                "输出进入上下文前必须先压缩：长文本/证据语义走 "
                "qxen_cd_longtext_distill(source_path=...)；测试失败日志走 "
                "local_extract_failure(log_path=raw_output)"
                "（test/expected/actual 各 1 行）；禁止整段 raw 输出。"
            )
            log_entry["action"] = "output_guard"
            log_entry["command"] = cmd[:120]
            if any(re.search(p, cmd) for p in STRICT_LONG_READ_PATTERNS):
                if task_is_structured_audit:
                    log_entry["action"] = "structured_audit_allow"
                    parts.append(
                        "[force-distill] 结构化文档核对路径：允许确定性抽取标题/表格/数字，"
                        "再用代码与审计记录核对；长叙述段落仍需 QXEN 蒸馏。"
                    )
                else:
                    long_read_deny = True
                    log_entry["action"] = "long_read_deny"

    # ---- 4. 无触发时静默（不注入噪音）----
    if not parts:
        return 0

    capsule = build_capsule(parts)
    write_guard_log(log_entry)
    # Continue PreToolUse 只注入 JSON 输出（hookSpecificOutput.additionalContext）；
    # 纯文本 stdout 会被丢弃。参考 dist/index.js rki(): PreToolUse 分支。
    hook_output = {
        "hookEventName": "PreToolUse",
        "additionalContext": capsule,
    }
    if STRICT_MODE or long_read_deny:
        hook_output.update({
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "检测到大范围原文读取，已阻止进入上下文；请将 source_path 交给 "
                "qxen_cd_longtext_distill 或 local_* 蒸馏后再局部回源"
            ),
        })
    if updated_command is not None:
        hook_output.update({
            "permissionDecision": "allow",
            "updatedInput": {"command": updated_command},
        })
    print(json.dumps({"hookSpecificOutput": hook_output}, ensure_ascii=False), flush=True)
    return 0 if updated_command is not None else (2 if STRICT_MODE or long_read_deny else 0)


if __name__ == "__main__":
    sys.exit(main())
