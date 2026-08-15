"""Optional LocalQwen adapter boundary.

The provider is injected by the host. This keeps Ollama/model paths out of the
public package and makes the adapter auditable and easy to replace.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from qxen_cd import compact, guard_v1


def distill(provider: Callable[[str], str], prompt: str,
           state: dict[str, Any] | None = None, **compact_options: Any) -> dict[str, Any]:
    """Run LocalQwen as advisory provider; output still crosses the guard."""
    raw = provider(prompt)
    checked = guard_v1(raw, prompt)
    return compact([checked], state, **compact_options)
