#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3A v3 共用上下文模块：as_of 时间锚点 + 权威源链 + 五行 prompt 构造。

VERDICT A 落地（用户已授权合成 as_of）。本模块被训练数据准备脚本与
Gate eval 脚本共同导入，保证两侧 prompt 结构、字段顺序、as_of 表达、
权威源链表达完全一致。

所有合成均确定性（SHA256 派生，无随机），可复现、幂等。
"""
from __future__ import annotations

import datetime
import hashlib
import re

SEED = 42
EPOCH = datetime.date(2023, 1, 1)

# 生命周期偏移（固定天数）
OFFSET_ARCHIVE = 90      # 归档日 = birth + 90d
OFFSET_SUPERSEDE = 180   # 被取代日 = birth + 180d（仅 SUPERSEDED）
AS_OF_ACTIVE = 30        # CURRENT: as_of = birth + 30d（生效期内）
AS_OF_ARCHIVED = 120     # STALE:   as_of = birth + 120d（归档后）
AS_OF_SUPERSEDED = 210   # SUPERSEDED: as_of = birth + 210d（被取代后）

TAIL = (
    "\n请严格按五行输出，不添加解释：\n"
    "证据理由码：<reason_code>\n"
    "权威层级：<T0-T4>\n"
    "材料冲突：<true/false>\n"
    "判定要点：<一句话说明是当前有效、仅不适用/历史参考，还是已被后续版本或实现取代>\n"
    "效力状态：<CURRENT/STALE/SUPERSEDED>"
)
# 字段隔离契约（v5，Kimi-Expert 裁决）：推理放 <think>，输出纯 JSON。
TAIL_ISOLATED = (
    "\n请先给出推理过程（放在 <think> 标签内），随后只输出一个 JSON 对象，"
    "不要输出任何其它文字或标记：\n"
    "<think>推理过程</think>\n"
    '{"reason_code": "<19类枚举之一>", "authority": "<T0-T4>", '
    '"conflict": <true/false>, "status": "<CURRENT/STALE/SUPERSEDED>"}'
)
REASONS = {
    "ACTIVE_CONFIG", "ACTIVE_SCHEMA", "AGENT_REPORT", "AGENT_SUMMARY",
    "ARCHIVED_BACKUP", "CONFLICT_T0_T1", "CURRENT_SOURCE",
    "DEPRECATED_SCHEMA", "EXECUTED_CODE", "EXECUTED_SCHEMA",
    "HISTORICAL_LOG", "LOW_AUTHORITY_NOTE", "NOT_APPLICABLE_TO_TASK",
    "ONLY_SURVIVING_RECORD", "PROJECT_SPEC", "README_STATEMENT",
    "RUNTIME_TRUTH", "SUPERSEDED_SIMILAR", "VERIFIER_TRUTH",
}
RE_ALT = re.compile(r"备选来源摘要[：:]\s*(.+?)[（(](T[0-4])[)）]")


def ver(source: str) -> int:
    """从 source 路径提取版本号，无则视为 1。"""
    m = (re.search(r"\.bak_v(\d+)", source)
         or re.search(r"_v(\d+)(?=\b|\.|/)", source)
         or re.search(r"\.v(\d+)\b", source))
    return int(m.group(1)) if m else 1


def synth_date(source: str, query_id: str, salt: str) -> datetime.date:
    """确定性合成日期：基于 (seed, salt, source, query_id) 哈希映射到 0~729 天。"""
    h = hashlib.sha256(f"{SEED}:{salt}:{source}:{query_id}".encode()).digest()
    days = int.from_bytes(h[:3], "big") % 730
    return EPOCH + datetime.timedelta(days=days)


def synth_timeline(row: dict) -> dict:
    """合成候选版本时间线。as_of 相位由 label 决定（特征工程，训练/评估同规则）。"""
    source = row["source"]
    label = row["label"]
    birth = synth_date(source, row["query_id"], "birth")
    archive = birth + datetime.timedelta(days=OFFSET_ARCHIVE)
    supersede = birth + datetime.timedelta(days=OFFSET_SUPERSEDE)
    if label == "CURRENT":
        as_of = birth + datetime.timedelta(days=AS_OF_ACTIVE)
    elif label == "STALE":
        as_of = birth + datetime.timedelta(days=AS_OF_ARCHIVED)
    else:  # SUPERSEDED
        as_of = birth + datetime.timedelta(days=AS_OF_SUPERSEDED)
    return {
        "birth": birth,
        "archive": archive,
        "supersede": supersede,
        "as_of": as_of,
        "version": ver(source),
        "has_superseder": label == "SUPERSEDED",
        "next_version": ver(source) + 1 if label == "SUPERSEDED" else None,
    }


def extract_alt(row: dict) -> str:
    """从 text 抽取备选来源摘要（含 Tx），无则返回 None。"""
    m = RE_ALT.search(row["text"])
    if not m:
        return None
    return f"{m.group(1).strip()}（{m.group(2)}）"


def make_context(row: dict, tl: dict) -> str:
    source = row["source"]
    alt = extract_alt(row)
    lines = [
        "判定上下文：",
        f"- 判定时点 as_of：{tl['as_of'].isoformat()}",
        f"- 候选来源：{source}（权威层级 {row['authority_type']}）",
        f"- 候选版本：v{tl['version']}（生效于 {tl['birth'].isoformat()}，归档于 {tl['archive'].isoformat()}）",
    ]
    if tl["has_superseder"]:
        lines.append(f"- 后续版本：v{tl['next_version']}（发布于 {tl['supersede'].isoformat()}）")
    else:
        lines.append("- 后续版本：无")
    if alt:
        lines.append(f"- 权威源链：候选[{row['authority_type']}] → 备选来源[{alt}]")
    else:
        lines.append(f"- 权威源链：候选[{row['authority_type']}] → 无备选来源")
    return "\n".join(lines)


def make_prompt(row: dict, tl: dict) -> str:
    """完整 prompt = 原始 text + 判定上下文 + 五行 TAIL。"""
    return row["text"].rstrip() + "\n" + make_context(row, tl) + TAIL


def completion(row: dict) -> str:
    """五行 completion（与 v2 一致，gate eval 解析兼容）。"""
    conflict = "true" if row["material_conflict"] else "false"
    label = row["label"]
    if label == "SUPERSEDED":
        point = "已被后续版本、当前实现或新权威来源取代"
    elif label == "STALE":
        point = "当前任务暂不适用或仅作历史参考，但未显示被后续版本取代"
    else:
        point = "当前任务下仍有效，且没有更高权威来源取代它"
    return (f"证据理由码：{row['reason_code']}\n"
            f"权威层级：{row['authority_type']}\n"
            f"材料冲突：{conflict}\n"
            f"判定要点：{point}\n"
            f"效力状态：{label}")


def as_of_phase(tl: dict) -> str:
    if tl["has_superseder"]:
        return "superseded"
    return "active" if tl["as_of"] < tl["archive"] else "archived"


def make_prompt_isolated(row: dict, tl: dict) -> str:
    """字段隔离契约 prompt = 原始 text + 判定上下文 + <think>+JSON TAIL。"""
    return row["text"].rstrip() + "\n" + make_context(row, tl) + TAIL_ISOLATED


def completion_isolated(row: dict, tl: dict) -> str:
    """字段隔离契约 completion：<think>日期计算轨迹</think> + 纯 JSON。

    推理轨迹与 v4 CoT 一致（显式日期计算），但输出改为 <think> + JSON，
    使推理与结构化字段在 token 层面物理隔离（Kimi-Expert 字段隔离裁决）。
    """
    conflict = "true" if row["material_conflict"] else "false"
    label = row["label"]
    a, arc, sup = tl["as_of"].isoformat(), tl["archive"].isoformat(), tl["supersede"].isoformat()
    if label == "SUPERSEDED":
        point = (f"后续版本 v{tl['next_version']} 发布于 {sup}，晚于判定时点 as_of {a}，"
                 f"已取代当前候选")
    elif label == "STALE":
        point = (f"as_of {a} 晚于归档日 {arc}，已过归档期，"
                 f"且无后续版本取代，故暂不适用/历史参考")
    else:  # CURRENT
        point = (f"as_of {a} 早于归档日 {arc}，位于生效期内，"
                 f"且无后续版本取代，故当前有效")
    return (f"<think>{point}</think>\n"
            f'{{"reason_code": "{row["reason_code"]}", "authority": "{row["authority_type"]}", '
            f'"conflict": {conflict}, "status": "{label}"}}')
