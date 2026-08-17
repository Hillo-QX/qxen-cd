#!/usr/bin/env python3
"""最终独立验证 valid.jsonl：与 train 无重叠、内部无重复、场景覆盖。"""
import json
import hashlib
import re
from difflib import SequenceMatcher


def load(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


train = load('data/eval_set/train/train.jsonl')
valid = load('data/eval_set/valid.jsonl')
test = load('data/eval_set/test/test.jsonl')
train_resp = [d['response'] for d in train]

print('=== 最终验证 ===')
print(f'valid 条数: {len(valid)}')
print(f'train 条数: {len(train)}')
print(f'test 条数: {len(test)}')

worst = 0.0
worst_detail = None
for i, d in enumerate(valid):
    c = d['completion']
    for t in train_resp:
        m = SequenceMatcher(None, c, t).find_longest_match(0, len(c), 0, len(t))
        ratio = m.size / max(len(c), 1)
        if ratio > worst:
            worst = ratio
            worst_detail = (i, m.size, len(c))
print(f'valid 与 train 最大公共子串比率: {worst:.1%}  (idx={worst_detail})')

seen = set()
dup = 0
for d in valid:
    h = hashlib.md5(d['completion'].encode()).hexdigest()
    if h in seen:
        dup += 1
    seen.add(h)
print(f'valid 内部重复: {dup}')

t_hashes = {hashlib.md5((t['instruction'] + t['response']).encode()).hexdigest() for t in test}
v_hashes = {hashlib.md5((v['prompt'] + v['completion']).encode()).hexdigest() for v in valid}
print(f'valid ∩ test (全文哈希): {len(v_hashes & t_hashes)}')

cats = {'tool_use': 0, 'format': 0, 'reasoning': 0, 'long_task': 0, 'recovery': 0, 'constraint': 0}
for d in valid:
    r = d['completion']
    if re.search(r'(TOOL|工具|bash|Run:|Edit:|Read:)', r, re.I):
        cats['tool_use'] += 1
    if re.search(r'(`{3}|JSON|yaml|schema|结构化)', r, re.I):
        cats['format'] += 1
    if re.search(r'(因此|判定|结论|决定|优先级)', r):
        cats['reasoning'] += 1
    if re.search(r'(多阶段|多步|持续|跨轮|迭代|阶段|Phase)', r):
        cats['long_task'] += 1
    if re.search(r'(失败|重试|escalate|降级|恢复)', r, re.I):
        cats['recovery'] += 1
    if re.search(r'(禁止|不得|必须|红线|forbidden)', r, re.I):
        cats['constraint'] += 1
print('独立场景统计:', cats)
print('覆盖场景数:', sum(1 for v in cats.values() if v >= 3))
