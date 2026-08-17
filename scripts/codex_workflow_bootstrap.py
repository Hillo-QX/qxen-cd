#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Global Codex workflow bootstrap discovery.

This module only discovers inputs and protects training resources. Semantic
distillation is performed by the MCP layer after discovery; deterministic
metrics remain the responsibility of project Python engines.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox",
             "models", ".mypy_cache", ".pytest_cache"}
LOG_SUFFIXES = {".log", ".jsonl", ".out", ".err"}
ENGINE_MARKERS = ("backtest", "factor", "ic", "pit", "dedup", "unique",
                  "cost", "ingest", "validate", "metric")
TRAIN_PATTERNS = (
    "mlx_lm.lora", "mlx_lm\u0020lora", "qxen_joint_train", "qwen.*train",
    "python.*train.*\\.py", "train.*\\.py.*mlx",
)


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def training_processes() -> list[str]:
    """Return matching process command lines without loading any model."""
    try:
        proc = subprocess.run(["ps", "-axo", "pid=,command="],
                              capture_output=True, text=True, timeout=3,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in proc.stdout.splitlines():
        lower = line.lower()
        if "codex_workflow_bootstrap" in lower:
            continue
        if any(re.search(pattern, lower) for pattern in TRAIN_PATTERNS):
            found.append(line.strip())
    return found[:10]


def discover(workspace: str, explicit_paths: list[str] | None = None,
             max_files: int = 16) -> dict:
    root = Path(workspace or ".").expanduser().resolve()
    if not root.is_dir():
        return {"status": "ERROR", "reason": "workspace_not_found",
                "workspace": str(root), "materials": [], "engine_candidates": []}

    candidates: list[tuple[int, Path, str]] = []
    seen: set[str] = set()

    def add(path: Path, category: str, priority: int) -> None:
        try:
            path = path.resolve()
            if not path.is_file() or _is_skipped(path) or str(path) in seen:
                return
            seen.add(str(path))
            candidates.append((priority, path, category))
        except OSError:
            return

    for raw in explicit_paths or []:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = root / p
        add(p, "explicit", 0)

    try:
        for p in root.rglob("*"):
            if not p.is_file() or _is_skipped(p):
                continue
            name = p.name.lower()
            rel = str(p.relative_to(root)).lower()
            if name == "agents.md" or any(x in name or x in rel for x in
                                          ("handoff", "交接", "蒸馏上下文", "context")):
                add(p, "handoff", 1)
            elif ("checkpoint" in name or name in {"state.json", "summary_view.json"}
                  or "progress" in name):
                add(p, "checkpoint", 2)
            elif p.suffix.lower() in LOG_SUFFIXES and p.stat().st_size > 2000:
                add(p, "log", 3)
    except OSError:
        pass

    candidates.sort(key=lambda item: (item[0], -item[1].stat().st_mtime
                                      if item[1].exists() else 0))
    selected = candidates[:max(1, min(max_files, 50))]
    materials = []
    for _, path, category in selected:
        try:
            stat = path.stat()
            materials.append({"path": str(path), "category": category,
                              "size": stat.st_size,
                              "modified": stat.st_mtime})
        except OSError:
            materials.append({"path": str(path), "category": category,
                              "status": "ERROR"})

    engines = []
    try:
        for p in root.rglob("*.py"):
            if _is_skipped(p):
                continue
            name = p.name.lower()
            if any(marker in name for marker in ENGINE_MARKERS):
                engines.append(str(p.resolve()))
    except OSError:
        pass
    engines = sorted(set(engines))[:30]
    processes = training_processes()
    return {
        "status": "OK",
        "workspace": str(root),
        "materials": materials,
        "engine_candidates": engines,
        "training_protected": bool(processes),
        "training_processes": processes,
    }
