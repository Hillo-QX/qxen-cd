#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T046 — 训练格式校验脚本
逐项检查转换后数据：数量、chat 格式、单标签词、字段完整性、provenance、与源一致性。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAIN_DIR = os.path.join(ROOT, "outputs", "context_decision_training")
SRC = os.path.join(ROOT, "outputs", "context_decision_dataset", "context_decision_all.jsonl")
VALID = {"PIN", "KEEP", "VERBATIM", "COMPRESS", "DROP", "REFRESH", "RETRIEVE"}

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main():
    # 1. 目录与文件存在
    check("目录存在", os.path.isdir(TRAIN_DIR), TRAIN_DIR)
    for fn in ("train.jsonl", "train_messages.jsonl", "manifest.json", "README.md"):
        check(f"文件存在: {fn}", os.path.isfile(os.path.join(TRAIN_DIR, fn)))

    # 2. train.jsonl 数量与字段
    rows = []
    with open(os.path.join(TRAIN_DIR, "train.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                rows.append(json.loads(ln))
    check("train.jsonl 含 108 条", len(rows) == 108, f"实际 {len(rows)}")
    field_ok = all(set(r.keys()) == {"prompt", "completion"} for r in rows)
    check("字段为 prompt/completion", field_ok)

    # 3. completion 为单标签词（7 类合法值）
    bad = [r["completion"] for r in rows if r["completion"] not in VALID]
    check("completion 为合法单标签词", not bad, f"非法: {set(bad)}")

    # 4. prompt 含指令且指令仅一次
    instr = "决策：(PIN|KEEP|VERBATIM|COMPRESS|DROP|REFRESH|RETRIEVE) 只输出一个标签词。"
    dup = [i for i, r in enumerate(rows) if r["prompt"].count(instr) != 1]
    check("指令恰出现一次（无重复污染）", not dup, f"异常索引: {dup[:5]}")

    # 5. messages 视图与 train.jsonl 一致
    msg_rows = []
    with open(os.path.join(TRAIN_DIR, "train_messages.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                msg_rows.append(json.loads(ln))
    check("messages 视图 108 条", len(msg_rows) == 108, f"实际 {len(msg_rows)}")
    msg_ok = all(
        len(m["messages"]) == 3
        and m["messages"][0]["role"] == "system"
        and m["messages"][1]["role"] == "user"
        and m["messages"][2]["role"] == "assistant"
        and m["messages"][2]["content"] in VALID
        for m in msg_rows
    )
    check("messages 为 system/user/assistant 且 assistant 单标签", msg_ok)

    # 6. manifest 完整
    with open(os.path.join(TRAIN_DIR, "manifest.json"), encoding="utf-8") as f:
        man = json.load(f)
    man_ok = all(k in man for k in ("dataset_version", "source", "converter", "instruction",
                                    "input_count", "output_count", "dropped", "label_distribution"))
    check("manifest 字段完整", man_ok)
    check("manifest output_count == 108", man.get("output_count") == 108)

    # 7. 与源数据集标签一致
    src_rows = []
    with open(SRC, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                src_rows.append(json.loads(ln))
    src_labels = sorted(r["decision"] for r in src_rows)
    out_labels = sorted(r["completion"] for r in rows)
    check("转换后标签与源一致", src_labels == out_labels,
          f"源 {len(src_labels)} vs 输出 {len(out_labels)}")

    failed = [n for n, ok in results if not ok]
    print("\n================ SUMMARY ================")
    print(f"通过 {len(results) - len(failed)}/{len(results)}")
    if failed:
        print(f"失败项: {failed}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
