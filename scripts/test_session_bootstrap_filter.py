#!/usr/bin/env python3
"""Deterministic regression tests for task-scoped handoff filtering."""
from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import os
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import session_bootstrap as sb  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        handoff = Path(tmp) / "handoff.md"
        handoff.write_text("\n".join([
            "2026-08-15 金融策略回测：夏普 0.70，MaxDD 7.2%，当前待核对交易成本。",
            "2026-08-15 QXEN LoRA 训练：Val loss nan，Gate 待修复。",
            "2025-01-01 旧版策略已 archived，不作为当前结论。",
        ]), encoding="utf-8")
        target = Path("/Users/hillo/Desktop/金融模型及数据")
        lines = sb.filtered_handoff(target, handoff,
                                    "金融数据分析 回测 三策略")
        body = "\n".join(lines)
        assert "夏普" in body and "MaxDD" in body
        assert "Val loss nan" not in body
        assert "archived" not in body
        print("PASS finance task filter")

        generic = "\n".join(sb.filtered_handoff(target, handoff, ""))
        assert "filter=off" in generic and "reason=no_task" in generic
        assert "handoff_available=true" in generic
        assert "夏普" not in generic and "Val loss" not in generic
        print("PASS generic explicit downgrade")

        workspace = Path(tmp)
        (workspace / "调度状态").mkdir()
        local_handoff = workspace / "调度状态" / "handoff.md"
        local_handoff.write_text(handoff.read_text(encoding="utf-8"), encoding="utf-8")
        cfg = {**sb.DEFAULTS, "handoff_doc": "调度状态/handoff.md"}
        old_health = sb.ollama_status
        sb.ollama_status = lambda: (_ for _ in ()).throw(AssertionError("no-task must not probe Ollama"))
        try:
            minimal = sb.build_capsule(workspace, cfg, "session-no-task", "", "")
        finally:
            sb.ollama_status = old_health
        assert "handoff_available=true" in minimal
        assert "Ollama:" not in minimal and "夏普" not in minimal and "Val loss" not in minimal
        print("PASS no-task minimal bootstrap")

        marker = Path(tmp) / "marker"
        marker.write_text("x", encoding="utf-8")
        assert sb.marker_is_fresh(marker)
        old = time.time() - sb.BOOTSTRAP_MARKER_MAX_AGE_SECONDS - 1
        os.utime(marker, (old, old))
        assert not sb.marker_is_fresh(marker)
        print("PASS stale marker expiry")

        prior = sb._task_terms("继续检查 QXEN compacting 架构断点")
        followup = sb._task_terms("继续修复 QXEN compacting 的断点")
        unrelated = sb._task_terms("只查看最近 Git 提交时间")
        assert len(prior & followup) >= 2
        assert len(prior & unrelated) < 2
        print("PASS task-context overlap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
