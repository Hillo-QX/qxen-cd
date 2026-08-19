#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared MLX inference backend for QXEN-CD and LocalQwen.

The MCP servers remain separate processes, so Python model objects cannot be
shared directly.  This module shares the loading policy, Metal profile and a
cross-process inference lock; each process keeps its own warm model cache.
"""
from __future__ import annotations

import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
BASE_MODEL = Path(os.environ.get(
    "QXEN_BASE_MODEL", str(ROOT / "models" / "qwen3.5-9b-mlx-4bit")
))
ADAPTER = Path(os.environ.get(
    "QXEN_ADAPTER", str(ROOT / "models" / "qxen_joint_v1_clean_full")
))
MEMORY_LIMIT_GB = float(os.environ.get("QXEN_METAL_MEMORY_LIMIT_GB", "22"))
CACHE_LIMIT_GB = float(os.environ.get("QXEN_METAL_CACHE_LIMIT_GB", "1.5"))
LOCK_PATH = Path(os.environ.get(
    "QXEN_MLX_INFERENCE_LOCK", str(ROOT / "调度状态" / "qxen_mlx_inference.lock")
))

_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()
_METAL_APPLIED = False


def _signature() -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        for path in (BASE_MODEL, ADAPTER)
    )


def configure_metal() -> dict[str, Any]:
    global _METAL_APPLIED
    if _METAL_APPLIED:
        return {"applied": False, "reason": "already_applied"}
    import mlx.core as mx
    if not mx.metal.is_available():
        return {"applied": False, "reason": "metal_unavailable"}
    set_memory = getattr(mx, "set_memory_limit", mx.metal.set_memory_limit)
    set_cache = getattr(mx, "set_cache_limit", mx.metal.set_cache_limit)
    previous_memory = set_memory(max(1, int(MEMORY_LIMIT_GB * 1024**3)))
    previous_cache = set_cache(max(0, int(CACHE_LIMIT_GB * 1024**3)))
    _METAL_APPLIED = True
    return {
        "applied": True,
        "memory_limit_gb": MEMORY_LIMIT_GB,
        "cache_limit_gb": CACHE_LIMIT_GB,
        "previous_memory_limit": previous_memory,
        "previous_cache_limit": previous_cache,
        "device": str(mx.default_device()),
    }


def load_cached() -> tuple[Any, Any, bool, float, dict[str, Any]]:
    """Load the adapter once per MCP process and return cache telemetry."""
    from mlx_lm import load

    signature = _signature()
    key = f"{BASE_MODEL}:{ADAPTER}"
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached["signature"] == signature:
            return cached["model"], cached["tokenizer"], True, 0.0, cached["metal"]
        started = time.perf_counter()
        metal = configure_metal()
        model, tokenizer = load(str(BASE_MODEL), adapter_path=str(ADAPTER))
        latency = time.perf_counter() - started
        _CACHE.clear()
        _CACHE[key] = {
            "signature": signature, "model": model, "tokenizer": tokenizer,
            "metal": metal,
        }
        return model, tokenizer, False, latency, metal


@contextmanager
def inference_lock():
    """Serialize MLX work across QXEN and LocalQwen MCP processes."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def generate(prompt: str, max_tokens: int) -> dict[str, Any]:
    from mlx_lm import generate as mlx_generate

    with inference_lock():
        model, tokenizer, cache_hit, load_latency, metal = load_cached()
        started = time.perf_counter()
        text = mlx_generate(
            model, tokenizer, prompt=prompt,
            max_tokens=max(1, min(int(max_tokens), 4096)),
            verbose=False,
        )
    return {
        "text": str(text).strip(),
        "cache_hit": cache_hit,
        "model_load_latency_s": round(load_latency, 4),
        "model_latency_s": round(time.perf_counter() - started, 4),
        "metal": metal,
        "backend": "mlx-shared",
        "model": str(BASE_MODEL),
        "adapter": str(ADAPTER),
    }


def health() -> dict[str, Any]:
    ok = BASE_MODEL.is_dir() and ADAPTER.is_dir()
    return {
        "status": "OK" if ok else "ERROR",
        "backend": "mlx-shared",
        "model_assets_present": ok,
        "model": str(BASE_MODEL),
        "adapter": str(ADAPTER),
        "ollama": False,
    }
