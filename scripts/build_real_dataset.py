"""Build a small REAL training dataset for the First Training Milestone.

Sources are local project text files (no network / no external download):
  - 调度状态/*.md            (distilled context, handover list, exec rules)
  - QXEN_distiller_training_SKILL.md
  - README.md
  - 日志/dispatcher.log
  - 测试/end_to_end_test.py, 测试/mcp_dispatch.py

Each JSONL record is:
    {"instruction": "<context-selection/state-distillation prompt + raw chunk>",
     "response": "<raw chunk verbatim>"}

Verbatim response mirrors the KEEP/VERBATIM context action so the sample is
usable for later context-selection SFT. Deterministic: fixed seed and order.
"""
import json
import os
import random

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCES = [
    "调度状态/QWEN蒸馏上下文_codex_kimi.md",
    "调度状态/QWEN让渡清单.md",
    "调度状态/QWEN执行规则.md",
    "QXEN_distiller_training_SKILL.md",
    "README.md",
    "日志/dispatcher.log",
    "测试/end_to_end_test.py",
    "测试/mcp_dispatch.py",
]

OUT_PATH = os.path.join(PROJECT_ROOT, "data", "real_samples.jsonl")
CHUNK_LINES = 18  # non-empty lines per chunk
SEED = 42


def read_nonempty_lines(path):
    """Return list of non-empty stripped lines from a file."""
    full = os.path.join(PROJECT_ROOT, path)
    if not os.path.isfile(full):
        return []
    with open(full, "r", encoding="utf-8", errors="replace") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def build_chunks(sources, chunk_lines=CHUNK_LINES):
    chunks = []
    for src in sources:
        lines = read_nonempty_lines(src)
        if not lines:
            continue
        for i in range(0, len(lines), chunk_lines):
            chunks.append("\n".join(lines[i : i + chunk_lines]))
    return chunks


def make_record(chunk):
    instruction = (
        "上下文选择与状态蒸馏：给定以下 Agent 会话/文档片段，"
        "识别并保留关键信息（目标、约束、路径、验收标准、已验证事实）。\n\n"
        f"{chunk}"
    )
    return {"instruction": instruction, "response": chunk}


def main():
    chunks = build_chunks(SOURCES)
    rng = random.Random(SEED)
    rng.shuffle(chunks)
    # keep within the 100..200 limit
    records = [make_record(c) for c in chunks[:200]]

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    total_lines = sum(len(read_nonempty_lines(s)) for s in SOURCES)
    print(f"source_files_used: {[s for s in SOURCES if read_nonempty_lines(s)]}")
    print(f"source_total_lines: {total_lines}")
    print(f"chunks_generated: {len(chunks)}")
    print(f"records_written: {len(records)}")
    print(f"out: {OUT_PATH}")


if __name__ == "__main__":
    main()
