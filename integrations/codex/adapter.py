"""Host-neutral Codex-style adapter for QXEN-CD.

The host supplies a provider callable. This module only enforces the guard
and compaction boundary; it does not know credentials, paths, or host hooks.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from qxen_cd import compact, guard_v1


def process(provider: Callable[[str], str], prompt: str,
            state: dict[str, Any] | None = None, **compact_options: Any) -> dict[str, Any]:
    """Call an advisory provider, guard its output, then compact the result."""
    raw = provider(prompt)
    checked = guard_v1(raw, prompt)
    return compact([checked], state, **compact_options)
