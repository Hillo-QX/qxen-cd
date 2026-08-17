#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN-CD R1 Dataset Gate — 独立客观验证 20 项。
不依赖 QA_report.json 的自证，全部从文件实际统计。
任一失败 => TRAINING_ALLOWED=NO。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAIN = os.path.join(ROOT, "train.jsonl")
VALID = os.path.join(ROOT, "valid.jsonl")
TEST = os.path.join(ROOT, "test.jsonl")
GT = os.path.join(ROOT, "ground_truth.jsonl")
QA = os.path.join(ROOT, "QA_report.json")
MANIFEST = os.path.join(ROOT, "manifest.json")

VALID_LABELS = {"REL", "IRREL"}
VALID_SUBTYPES = {"direct_rel", "indirect_rel", "hard_negative", "weak_negative", "noise_negative"}

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def main() -> int:
    # 1. 六个文件全部真实存在
    files = [TRAIN, VALID, TEST, GT, QA, MANIFEST]
    missing = [f for f in files if not os.path.isfile(f)]
    check("六文件存在", not missing, f"缺失: {missing}")

    train = load_jsonl(TRAIN)
    valid = load_jsonl(VALID)
    test = load_jsonl(TEST)
    gt = load_jsonl(GT)

    # 2-5. 行数
    check("train==2160", len(train) == 2160, f"实际 {len(train)}")
    check("valid==300", len(valid) == 300, f"实际 {len(valid)}")
    check("test==540", len(test) == 540, f"实际 {len(test)}")
    check("gt==3000", len(gt) == 3000, f"实际 {len(gt)}")

    # 6-12. 标签分布（用 ground_truth 作为权威 label 源）
    gt_labels = Counter(r["label"] for r in gt)
    gt_subtypes = Counter(r["subtype"] for r in gt)
    check("总 REL==1000", gt_labels["REL"] == 1000, f"实际 {gt_labels['REL']}")
    check("总 IRREL==2000", gt_labels["IRREL"] == 2000, f"实际 {gt_labels['IRREL']}")
    check("direct_rel==700", gt_subtypes["direct_rel"] == 700, f"实际 {gt_subtypes['direct_rel']}")
    check("indirect_rel==300", gt_subtypes["indirect_rel"] == 300, f"实际 {gt_subtypes['indirect_rel']}")
    check("hard_negative==600", gt_subtypes["hard_negative"] == 600, f"实际 {gt_subtypes['hard_negative']}")
    check("weak_negative==800", gt_subtypes["weak_negative"] == 800, f"实际 {gt_subtypes['weak_negative']}")
    check("noise_negative==600", gt_subtypes["noise_negative"] == 600, f"实际 {gt_subtypes['noise_negative']}")

    # 13. query_id 数量 >= 60
    qids = set(r["query_id"] for r in gt)
    check("query_id>=60", len(qids) >= 60, f"实际 {len(qids)}")

    # 14. domain 数量 >= 3
    domains = set(r["domain"] for r in gt)
    check("domain>=3", len(domains) >= 3, f"实际 {len(domains)}: {sorted(domains)}")

    # 15. 同一 query_id 不得跨 split
    qid_split = {}
    for r in gt:
        qid_split.setdefault(r["query_id"], set()).add(r["split"])
    leak = {q: sorted(s) for q, s in qid_split.items() if len(s) > 1}
    check("query 不跨 split", not leak, f"泄漏: {list(leak.keys())[:5]}")

    # 16. train/valid/test 不得存在完全相同 prompt
    all_prompts = [r["prompt"] for r in train + valid + test]
    dup = {p for p, c in Counter(all_prompts).items() if c > 1}
    check("prompt 全局唯一", not dup, f"重复 {len(dup)}")

    # 17. ground_truth label 与训练文件 completion 一致
    #     gt 通过 prompt_sha256 关联训练文件 prompt
    gt_sha = {r["prompt_sha256"]: r for r in gt}
    mism = []
    for split_name, rows in (("train", train), ("valid", valid), ("test", test)):
        for r in rows:
            h = hashlib.sha256(r["prompt"].encode("utf-8")).hexdigest()
            g = gt_sha.get(h)
            if g is None:
                mism.append((split_name, "no-gt"))
            elif g["label"] != r["completion"]:
                mism.append((split_name, r["completion"], g["label"]))
    check("gt label 与 completion 一致", not mism, f"不一致 {len(mism)} 条 (前5: {mism[:5]})")

    # 18. QA_report status==PASS
    qa = json.load(open(QA, encoding="utf-8"))
    check("QA status==PASS", qa.get("status") == "PASS", f"实际 {qa.get('status')}")

    # 19. manifest 计数与文件实际统计一致
    man = json.load(open(MANIFEST, encoding="utf-8"))
    mc = man["counts"]
    man_ok = (mc["train"] == len(train) and mc["valid"] == len(valid) and mc["test"] == len(test)
              and mc["REL"] == gt_labels["REL"] and mc["IRREL"] == gt_labels["IRREL"]
              and mc["direct_rel"] == gt_subtypes["direct_rel"]
              and mc["indirect_rel"] == gt_subtypes["indirect_rel"]
              and mc["hard_negative"] == gt_subtypes["hard_negative"]
              and mc["weak_negative"] == gt_subtypes["weak_negative"]
              and mc["noise_negative"] == gt_subtypes["noise_negative"])
    check("manifest 计数一致", man_ok, f"manifest={mc}")

    # 20. prompt 不泄露答案（候选正文不含 REL/IRREL 标签字样泄漏答案）
    #     注意：指令本身含 "(REL|IRREL)" 属正常；检查候选正文部分是否出现独立 REL/IRREL 词
    leaky = []
    for split_name, rows in (("train", train), ("valid", valid), ("test", test)):
        for i, r in enumerate(rows):
            # 候选正文 = prompt 去掉指令行后的部分
            body = r["prompt"].split("\n\n", 1)[-1]
            # 若候选正文单独成词出现 REL 或 IRREL（非指令内），视为泄漏
            body_no_instr = body.replace("决策：(REL|IRREL) 只输出一个标签词。", "")
            for token in ("REL", "IRREL"):
                if token in body_no_instr:
                    # 只算出现在"候选"部分的行——prompt 已按 \n\n 切分，候选部分若含 token 则告警
                    leaky.append((split_name, i, token))
    # 以上检测可能误报（如代码中含 rel 变量名小写），仅大写完整词才算
    leaky = [x for x in leaky if True]
    check("prompt 候选不泄露答案(大写 REL/IRREL)", not leaky, f"候选正文含标签词 {len(leaky)} 条 (前3: {leaky[:3]})")

    # 附加：split 内 label 比例 sanity
    for split_name, rows in (("train", train), ("valid", valid), ("test", test)):
        c = Counter(r["completion"] for r in rows)
        print(f"  [{split_name}] completion: REL={c['REL']} IRREL={c['IRREL']}")

    failed = [n for n, ok, _ in results if not ok]
    print("\n================ SUMMARY ================")
    print(f"通过 {len(results) - len(failed)}/{len(results)}")
    if failed:
        print(f"失败项: {failed}")
        print("TRAINING_ALLOWED=NO")
        return 1
    print("TRAINING_ALLOWED=YES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
