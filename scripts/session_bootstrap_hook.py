#!/usr/bin/env python3
"""Hook adapter: normalize Codex/Kimi hook payload before bootstrap filtering."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "session_bootstrap.py"
RESPONSE_CAPSULE = ROOT / "scripts" / "response_capsule.py"
import response_capsule
from response_capsule import estimate_context_pressure

ATTACHMENT_PATH_RE = re.compile(r"(?P<path>/[^ \t\r\n`<>\"']+/.codex/attachments/[^ \t\r\n`<>\"']+)")
LONGTEXT_BYTES = 2000
ATTACHMENT_ANALYSIS_RE = re.compile(
    r"(分析|总结|判断|核对|审查|评估|比较|解释|研究|审阅|review|analyse|analyze|summarize|assess|compare|explain)",
    re.I,
)
TARGETED_ATTACHMENT_RE = re.compile(
    r"(第\s*\d+\s*行|行号|lines?\s*\d+|关键词|关键字|keyword|start[_ -]?line|end[_ -]?line)",
    re.I,
)


def first_value(obj, keys):
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in obj.values():
            found = first_value(value, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = first_value(value, keys)
            if found:
                return found
    return ""


def _find_attachment_paths(payload):
    found = []
    def walk(value):
        if isinstance(value, str):
            for match in ATTACHMENT_PATH_RE.finditer(value):
                path = Path(match.group("path").rstrip(".,:;)]}"))
                if path.is_file() and path not in found:
                    found.append(path)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)
    return found[:8]


def attachment_distill_context(payload):
    task = first_value(payload, ("user_prompt", "prompt", "content", "task", "task_type"))
    analysis = bool(ATTACHMENT_ANALYSIS_RE.search(task))
    targeted = bool(TARGETED_ATTACHMENT_RE.search(task))
    entries = []
    for path in _find_attachment_paths(payload):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= LONGTEXT_BYTES:
            continue
        if path.name == "SKILL.md":
            route = (
                "SKILL.md 指令文件必须按 Codex skill 规则完整读取；"
                "不使用 QXEN longtext 替代原文，QXEN 只可用于事后交接/摘要胶囊"
            )
        elif targeted:
            route = "允许确定性局部回源（仅按明确行号/关键词），不触发整附件 QXEN"
        elif analysis:
            route = "必须先调用 qxen_cd_longtext_distill，传 source_path；仅明确行号/关键词局部回源可绕过"
        else:
            route = "长文本附件默认先调用 qxen_cd_longtext_distill，传 source_path；若只需明确行号/关键词局部回源可绕过"
        entries.append(f"- {path} ({size} UTF-8 bytes): {route}")
    if not entries:
        return ""
    prefix = "本轮任务涉及分析/总结/判断/核对，必须先走 QXEN-CD；" if analysis else "本轮按附件路由规则处理；"
    return "[attachment-distill] 发现用户附件长文本，已超过 2000 字节阈值。" + prefix + "再按 Context Burden Ratio 决定注入：\n" + "\n".join(entries)


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
    except (OSError, json.JSONDecodeError):
        payload = {}
    session_id = first_value(payload, ("session_id", "sessionId", "conversation_id")) or "unknown"
    explicit_pressure = next((payload[key] for key in ("context_pressure", "contextPressure", "pressure") if key in payload), None)
    if explicit_pressure is None:
        pressure = estimate_context_pressure(session_id, first_value(payload, ("transcript_path", "transcriptPath")))
    else:
        try:
            pressure = {"pressure": max(0.0, min(float(explicit_pressure), 1.0)),
                        "observed_tokens": 0, "limit_tokens": 0, "source": "hook_payload"}
        except (TypeError, ValueError):
            pressure = estimate_context_pressure(session_id, first_value(payload, ("transcript_path", "transcriptPath")))
    normalized = {
        "cwd": first_value(payload, ("cwd", "working_directory", "workspace")) or os.getcwd(),
        "session_id": session_id,
        "task": first_value(payload, ("task", "task_type", "user_prompt", "prompt", "content")),
        "task_id": first_value(payload, ("task_id", "taskId", "work_item_id", "workItemId")),
        "context_pressure": pressure["pressure"],
        "context_pressure_observed_tokens": pressure["observed_tokens"],
        "context_pressure_limit_tokens": pressure["limit_tokens"],
        "context_pressure_source": pressure["source"],
        "target_workspace": first_value(payload, ("target_workspace", "targetWorkspace")),
        "transcript_path": first_value(payload, ("transcript_path", "transcriptPath")),
    }
    attachment_context = attachment_distill_context(payload)
    transcript = response_capsule.locate_codex_transcript(session_id, normalized["transcript_path"])
    captured = response_capsule.extract_final_response(str(transcript or ""))
    capture_output = ""
    if captured:
        capture_payload = dict(normalized)
        capture_payload["assistant_response"] = captured
        capture = subprocess.run(
            [sys.executable, str(RESPONSE_CAPSULE)], input=json.dumps(capture_payload, ensure_ascii=False),
            text=True, capture_output=True, check=False,
        )
        if "status=PENDING_QXEN" in capture.stdout or "status=DEDUP" in capture.stdout:
            capture_output = capture.stdout
    proc = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--hook"],
        input=json.dumps(normalized, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    pending = subprocess.run(
        [sys.executable, str(RESPONSE_CAPSULE), "--pending"],
        input=json.dumps(normalized, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    output = proc.stdout
    if attachment_context:
        try:
            hook_payload = json.loads(output) if output else {}
            hook_output = hook_payload.setdefault("hookSpecificOutput", {})
            existing = hook_output.get("additionalContext", "")
            hook_output["additionalContext"] = (f"{existing}\n{attachment_context}" if existing else attachment_context)[:1200]
            output = json.dumps(hook_payload, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    sys.stdout.write(output)
    sys.stdout.write(capture_output)
    sys.stdout.write(pending.stdout)
    sys.stderr.write(proc.stderr)
    sys.stderr.write(pending.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
