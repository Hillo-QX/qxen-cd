#!/usr/bin/env python3
"""Step A: 分类 eval/test 现有样本，评估 validation 扩大可行性。"""
import json
import re

def cls(s):
    r = s['response']
    c = []
    if re.search(r'(TOOL|工具|bash|Run:|Edit:|Read:)', r, re.I):
        c.append('tool_use')
    if re.search(r'(`{3}|JSON|yaml|schema|结构化)', r, re.I):
        c.append('format')
    if re.search(r'(因此|判定|结论|决定|优先级)', r):
        c.append('reasoning')
    if re.search(r'(多阶段|多步|持续|跨轮|迭代|阶段|Phase)', r):
        c.append('long_task')
    if re.search(r'(失败|重试|escalate|降级|恢复)', r, re.I):
        c.append('recovery')
    if re.search(r'(禁止|不得|必须|红线|forbidden)', r, re.I):
        c.append('constraint')
    return c

for split in ['eval', 'test']:
    path = f'data/eval_set/{split}/{split}.jsonl'
    print(f'==== {split} ====')
    with open(path) as f:
        for i, line in enumerate(f):
            d = json.loads(line)
            print(i, cls(d))
