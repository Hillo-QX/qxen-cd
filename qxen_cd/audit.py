"""Small, deterministic audit ledger for comparable baseline observations."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def estimate_tokens(chars: int | None) -> int | None:
    return None if chars is None else max(0, math.ceil(int(chars) / 4))


def append(path: str | Path, event: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def record_usage(path: str | Path, work_item_id: str, usage_id: str,
                 baseline_gpt_tokens: int | None, qxen_gpt_tokens: int | None,
                 gpt_review_tokens: int | None = None,
                 fallback_replay_gpt_tokens: int | None = None,
                 outcome: str = "success", estimated: bool = False) -> None:
    """Record a pair; do not infer savings when a side is missing."""
    append(path, {"event_type": "usage_observation", "work_item_id": work_item_id,
                  "usage_id": usage_id, "baseline_gpt_tokens": baseline_gpt_tokens,
                  "qxen_gpt_tokens": qxen_gpt_tokens, "gpt_review_tokens": gpt_review_tokens,
                  "fallback_replay_gpt_tokens": fallback_replay_gpt_tokens,
                  "outcome": outcome, "estimated": estimated})
