#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一会话 bootstrap 工具（kimi hooks / codex --manual 共用）。

模式：
  --hook         读 stdin 的 hook JSON（session_id、cwd），每会话首次注入
                 bootstrap 胶囊到 stdout（配 UserPromptSubmit 使用）。
                 加 --force 无视去重标记强制注入（配 codex SessionStart，
                 覆盖 startup/resume/clear/compact 四种来源，compact 后重注入）。
  --reset-marker 删除本会话的去重标记（配 kimi PostCompact：compact 后下个
                 UserPromptSubmit 重新注入胶囊）。
  --audit-check  SessionEnd 用：cwd 有适配器且当日无新审计报告则写
                 pending_audit 标记，供下次 bootstrap 追账。
  --manual       直接对当前目录（或 --cwd 指定）输出胶囊，供 codex/人工使用。

无适配器（向上找不到 .session-bootstrap.json）时一律静默退出 0，
保证全局 hook 不打扰无关项目。所有异常 fail-open（退出 0，不输出）。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
import re

sys.dont_write_bytecode = True
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

MARKER_DIR = Path.home() / ".kimi-code" / "cache" / "session-bootstrap"
ADAPTER_NAME = ".session-bootstrap.json"
CAPSULE_LIMIT = 1500
HANDOFF_MAX_ITEMS = 6
HANDOFF_MAX_AGE_DAYS = 30
LINE_MAX_AGE_DAYS = 120
MIN_HANDOFF_SCORE = 2
BOOTSTRAP_MARKER_MAX_AGE_SECONDS = 24 * 60 * 60

DEFAULTS = {
    "handoff_doc": "调度状态/QWEN蒸馏上下文_codex_kimi.md",
    "state_glob": "调度状态/STATE_*.md",
    "ledger": "调度状态/任务账本.json",
    "audit_dir": "日志/audit",
    "finish_script": "scripts/finish_session.sh",
}


def find_adapter(cwd: str) -> tuple[Path, dict] | None:
    """从 cwd 向上查找适配器，返回 (workspace, config)。"""
    try:
        cur = Path(cwd).expanduser().resolve()
    except OSError:
        return None
    for directory in (cur, *cur.parents):
        candidate = directory / ADAPTER_NAME
        if candidate.is_file():
            try:
                cfg = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            workspace = Path(cfg.get("workspace") or directory).expanduser()
            merged = {**DEFAULTS, **{k: v for k, v in cfg.items()
                                     if k != "workspace"}}
            return workspace, merged
    return None


def marker_path(prefix: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return MARKER_DIR / f"{prefix}_{digest}"


def session_tool_call_count(session_id: str) -> int:
    path = Path.home() / ".continue" / "sessions" / f"{session_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(len(item.get("toolCallStates") or []) for item in data.get("history", []))


def session_window_snapshot(session_id: str) -> dict:
    path = Path.home() / ".continue" / "sessions" / f"{session_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"history_items_before_window": 0, "prompt_tokens_before_window": 0,
                "completion_tokens_before_window": 0, "tool_calls_before_window": 0}
    usage = data.get("usage") or {}
    return {
        "history_items_before_window": len(data.get("history", [])),
        "prompt_tokens_before_window": int(usage.get("promptTokens") or 0),
        "completion_tokens_before_window": int(usage.get("completionTokens") or 0),
        "tool_calls_before_window": sum(len(item.get("toolCallStates") or []) for item in data.get("history", [])),
    }


def write_audit_baseline(session_id: str, force: bool = False) -> None:
    if not session_id or session_id == "unknown":
        return
    path = marker_path("audit_baseline", session_id)
    if path.is_file() and not force:
        return
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = session_window_snapshot(session_id)
    path.write_text(json.dumps({
        "session_id": session_id,
        **snapshot,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False), encoding="utf-8")


def marker_is_fresh(path: Path) -> bool:
    """A stale marker must not suppress bootstrap forever."""
    try:
        return (time.time() - path.stat().st_mtime) <= BOOTSTRAP_MARKER_MAX_AGE_SECONDS
    except OSError:
        return False


def marker_context(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _task_terms(text: str) -> set[str]:
    terms = set(re.findall(r"[A-Za-z0-9_/-]{3,}", (text or "").lower()))
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        terms.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return terms


def head_lines(path: Path, limit: int) -> list[str]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            lines = []
            for i, line in enumerate(fh):
                if i >= limit:
                    break
                lines.append(line.rstrip())
            return lines
    except OSError:
        return []


def task_profile(task: str) -> tuple[set[str], set[str]]:
    """Map a short task description to workspace keywords and evidence terms."""
    text = str(task or "").lower()
    profiles = {
        "finance": ({"金融模型及数据", "策略", "财务", "回测", "选股", "三策略"},
                    {"策略", "回测", "选股", "总表", "数据", "财务", "收益", "回撤", "夏普"}),
        "training": ({"任务调度器", "qwen", "mlx", "lora", "训练", "gate"},
                      {"训练", "checkpoint", "loss", "gate", "adapter", "验证"}),
        "backtest": ({"金融模型及数据", "回测", "策略", "选股"},
                      {"回测", "收益", "回撤", "夏普", "成本", "样本", "策略"}),
    }
    for name, value in profiles.items():
        if name in text or any(k in text for k in value[1]):
            return value
    return set(), set()


def filtered_handoff(workspace: Path, handoff: Path, task: str = "") -> list[str]:
    """Return a small, recent, task-matching handoff capsule from broad history."""
    if not handoff.is_file():
        return []
    try:
        age_days = (time.time() - handoff.stat().st_mtime) / 86400
        lines = [line.strip() for line in handoff.read_text(encoding="utf-8",
                                                               errors="replace").splitlines()
                 if line.strip()]
    except OSError:
        return []
    workspace_keys, task_keys = task_profile(task)
    workspace_text = str(workspace).lower()
    if not task_keys:
        # No task means no historical material is relevant yet.  Report only
        # availability; loading broad legacy lines would add context pressure.
        return [
            f"交接筛选: filter=off reason=no_task workspace={workspace.name} "
            f"handoff_available=true handoff_mtime="
            f"{time.strftime('%Y-%m-%d', time.localtime(handoff.stat().st_mtime))}",
        ]
    scored = []
    for index, line in enumerate(lines):
        low = line.lower()
        task_hits = sum(k.lower() in low for k in task_keys)
        score = task_hits
        if workspace_keys and any(k.lower() in workspace_text for k in workspace_keys):
            score += sum(k.lower() in low for k in workspace_keys)
        if any(marker in low for marker in ("当前", "状态", "结论", "pending", "uncertain", "next step")):
            score += 1
        date_match = re.search(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", line)
        if date_match:
            try:
                date_text = date_match.group(0).replace("/", "-")
                line_age = (datetime.now().date() - datetime.strptime(date_text, "%Y-%m-%d").date()).days
                if line_age > LINE_MAX_AGE_DAYS:
                    score -= 2
                elif line_age <= HANDOFF_MAX_AGE_DAYS:
                    score += 2
            except ValueError:
                pass
        if any(marker in low for marker in ("pass", "ready", "active", "current", "待办", "下一步")):
            score += 1
        if any(marker in low for marker in ("archived", "废弃", "superseded", "过期", "旧版")):
            score -= 3
        # 低于最低相关分数的历史行不进入胶囊；特别避免“候选不足时用无关历史补齐”。
        if (not task_keys or task_hits > 0) and score >= MIN_HANDOFF_SCORE:
            scored.append((score, index, line[:240]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [line for _, _, line in scored[:HANDOFF_MAX_ITEMS]]
    freshness = f"handoff_mtime={time.strftime('%Y-%m-%d', time.localtime(handoff.stat().st_mtime))}"
    age_note = "fresh" if age_days <= HANDOFF_MAX_AGE_DAYS else "stale_candidate"
    return [f"交接筛选: task={task or 'generic'} workspace={workspace.name} {freshness} age={age_note}"
            ] + selected


def ollama_status() -> str:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags",
                                    timeout=1) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        models = [m.get("name", "?") for m in data.get("models", [])][:5]
        return "OK(" + ",".join(models) + ")" if models else "OK(无模型)"
    except Exception:
        return "不可达"


def local_inference_status() -> str:
    """Report the active MLX backend; Ollama is legacy-only telemetry."""
    root = Path(__file__).resolve().parents[1]
    model = root / "models" / "qwen3.5-9b-mlx-4bit"
    adapter = root / "models" / "qxen_joint_v1_clean_full"
    if model.is_dir() and adapter.is_dir():
        return f"OK(backend=mlx-shared, model={model.name}, adapter={adapter.name})"
    return "不可用（MLX 共享后端）"


def latest_state_summary(workspace: Path, pattern: str) -> str:
    files = sorted(glob.glob(str(workspace / pattern)),
                   key=lambda p: os.path.getmtime(p), reverse=True)
    if not files:
        return "无 STATE 文件"
    latest = Path(files[0])
    lines = [l for l in head_lines(latest, 20) if l.strip()][:5]
    stamp = time.strftime("%m-%d %H:%M", time.localtime(latest.stat().st_mtime))
    return f"{latest.name}({stamp}): " + " / ".join(lines)


def ledger_summary(workspace: Path, rel: str) -> str:
    path = workspace / rel
    if not path.is_file():
        return "任务账本缺失"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "任务账本解析失败"
    if isinstance(data, dict):
        items = data.get("tasks") or data.get("items") or []
        return f"任务账本: {len(items)} 条" if isinstance(items, list) else \
            f"任务账本: {len(data)} 键"
    if isinstance(data, list):
        return f"任务账本: {len(data)} 条"
    return "任务账本: 格式未知"


def build_capsule(workspace: Path, cfg: dict, session_id: str, task: str = "",
                  target_workspace: str = "") -> str:
    try:
        from codex_workflow_bootstrap import training_processes
        trains = training_processes()
    except Exception:
        trains = []

    target = Path(target_workspace).expanduser() if target_workspace else workspace
    parts = ["[session-bootstrap] 交接源: " + str(workspace),
             "目标工作区: " + str(target)]

    if trains:
        parts.append("训练保护模式 ON（检测到训练进程，禁用 local_*/LocalQwen）: "
                     + "; ".join(t[:80] for t in trains[:3]))
    else:
        parts.append("训练保护模式 OFF（无 MLX 训练进程）")

    # SessionStart commonly has no task.  Keep that injection minimal and
    # defer handoff/state/model-health material until a real prompt supplies
    # task relevance.  This avoids charging every new/compacted context for a
    # broad historical capsule.
    if not str(task or "").strip():
        handoff = workspace / cfg["handoff_doc"]
        parts.extend(filtered_handoff(target, handoff, "") if handoff.is_file() else [
            f"交接筛选: filter=off reason=no_task workspace={workspace.name} "
            "handoff_available=false"
        ])
        return "\n".join(parts)

    parts.append("QXEN-CD/MLX: " + local_inference_status())
    parts.append("LocalQwen: backend=mlx-shared（不依赖 Ollama）")
    parts.append("Ollama（legacy_optional）: " + ollama_status())

    handoff = workspace / cfg["handoff_doc"]
    lines = filtered_handoff(target, handoff, task)
    if lines:
        parts.append("交接文档 " + handoff.name + " 摘要:\n" + "\n".join(lines))
    else:
        parts.append("交接文档缺失: " + cfg["handoff_doc"])

    parts.append(latest_state_summary(workspace, cfg["state_glob"]))
    parts.append(ledger_summary(workspace, cfg["ledger"]))

    pending = marker_path("pending_audit", str(workspace))
    if pending.is_file():
        parts.append("⚠ 上次会话未过收工审计门禁：本 workspace 收工前必须运行 "
                     + cfg["finish_script"] + " 直至退出码为 0。")

    parts.append("协议: 1) 先消化本胶囊再工作; 2) >2K 字符材料先走 local_* "
                 "蒸馏(训练保护 ON 时除外); 3) 收工前跑 finish_session.sh。")

    text = "\n".join(parts)
    if len(text) > CAPSULE_LIMIT:
        text = text[:CAPSULE_LIMIT - 20] + "\n…(截断)"
    return text


def mode_hook(stdin_raw: str, force: bool = False) -> int:
    try:
        payload = json.loads(stdin_raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id") or "unknown"

    found = find_adapter(cwd)
    if not found:
        return 0
    workspace, cfg = found

    done = marker_path("bootstrap_done", session_id)
    task = payload.get("task") or payload.get("task_type") or os.environ.get("CODEX_TASK", "")
    target_workspace = (payload.get("target_workspace") or
                        os.environ.get("CODEX_TARGET_WORKSPACE", ""))
    previous = marker_context(done)
    prior_terms = set(previous.get("task_terms") or _task_terms(previous.get("task", "")))
    current_terms = _task_terms(task)
    same_task = previous.get("task", "") == task or not task or len(prior_terms & current_terms) >= 2
    same_context = same_task and previous.get("target_workspace", "") == target_workspace
    if done.is_file() and not force and marker_is_fresh(done) and same_context:
        return 0

    write_audit_baseline(session_id, force=force)
    print(build_capsule(workspace, cfg, session_id, task, target_workspace))
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    done.write_text(json.dumps({"workspace": str(workspace), "task": task,
                                "task_terms": sorted(current_terms)[:64],
                                "target_workspace": target_workspace}, ensure_ascii=False),
                    encoding="utf-8")
    return 0


def mode_reset_marker(stdin_raw: str) -> int:
    """compact 后清除去重标记，让下次 UserPromptSubmit 重新注入。"""
    try:
        payload = json.loads(stdin_raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    session_id = payload.get("session_id")
    if session_id:
        marker_path("bootstrap_done", session_id).unlink(missing_ok=True)
    return 0


def mode_audit_check(stdin_raw: str) -> int:
    try:
        payload = json.loads(stdin_raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()

    found = find_adapter(cwd)
    if not found:
        return 0
    workspace, cfg = found

    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    pending = marker_path("pending_audit", str(workspace))

    audit_dir = workspace / cfg["audit_dir"]
    today = time.strftime("%Y%m%d")
    fresh = False
    if audit_dir.is_dir():
        for entry in audit_dir.iterdir():
            try:
                if entry.is_file() and \
                        time.strftime("%Y%m%d",
                                      time.localtime(entry.stat().st_mtime)) == today:
                    fresh = True
                    break
            except OSError:
                continue
    if fresh:
        pending.unlink(missing_ok=True)
    else:
        pending.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    return 0


def mode_manual(cwd: str, task: str = "", target_workspace: str = "") -> int:
    found = find_adapter(cwd)
    if not found:
        print(f"未找到 {ADAPTER_NAME}（从 {cwd} 向上），无 bootstrap 配置。",
              file=sys.stderr)
        return 0
    workspace, cfg = found
    print(build_capsule(workspace, cfg, "manual", task, target_workspace))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="统一会话 bootstrap 工具")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--hook", action="store_true",
                       help="UserPromptSubmit hook 模式（默认，读 stdin）")
    group.add_argument("--audit-check", action="store_true",
                       help="SessionEnd 审计追账模式（读 stdin）")
    group.add_argument("--manual", action="store_true",
                       help="直接输出胶囊（codex/人工用）")
    group.add_argument("--reset-marker", action="store_true",
                       help="删除本会话去重标记（kimi PostCompact 用，读 stdin）")
    parser.add_argument("--force", action="store_true",
                        help="--hook 时无视去重标记强制注入（codex SessionStart 用）")
    parser.add_argument("--cwd", default=None, help="--manual 时指定目录")
    parser.add_argument("--task", default=None, help="按任务类型筛选交接胶囊")
    parser.add_argument("--target-workspace", default=None,
                        help="指定目标工作区；只用于交接筛选，不读取目标文件")
    args = parser.parse_args()

    try:
        if args.audit_check:
            return mode_audit_check(sys.stdin.read())
        if args.manual:
            return mode_manual(args.cwd or os.getcwd(),
                               args.task or os.environ.get("CODEX_TASK", ""),
                               args.target_workspace or os.environ.get("CODEX_TARGET_WORKSPACE", ""))
        if args.reset_marker:
            return mode_reset_marker(sys.stdin.read())
        return mode_hook(sys.stdin.read(), force=args.force)
    except Exception:
        return 0  # fail-open：hook 异常不得阻断会话


if __name__ == "__main__":
    sys.exit(main())
