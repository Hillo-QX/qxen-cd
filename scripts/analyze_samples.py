#!/usr/bin/env python3
"""分析 real_samples.jsonl 的场景分类，为 Step A 扩大 validation set 提供依据。"""
import json
import re
import collections
import hashlib
import sys

path = 'data/real_samples.jsonl'
samples = []
with open(path) as f:
    for line in f:
        if line.strip():
            samples.append(json.loads(line))


def classify(s):
    resp = s['response']
    cats = ['context_distill']
    if re.search(r'(TOOL|tool call|工具|动作|bash|执行命令|Run:|Edit:|Read:|\bRUN\b)', resp, re.I):
        cats.append('tool_use')
    if re.search(r'(```|\bJSON\b|\byaml\b|"task_id"|"status"|"acceptance_criteria"|结构化|schema)', resp, re.I):
        cats.append('format')
    if re.search(r'(因此|判定|结论|决定|优先级|因为|由于|权衡)', resp):
        cats.append('reasoning')
    if re.search(r'(多阶段|多步|持续|跨轮|迭代|阶段|Phase|长期|保持)', resp):
        cats.append('long_task')
    if re.search(r'(失败|重试|escalate|ESCALATE|降级|异常|恢复)', resp, re.I):
        cats.append('recovery')
    if re.search(r'(禁止|不得|必须|红线|forbidden|constraint)', resp, re.I):
        cats.append('constraint')
    return cats


cats_all = collections.Counter()
samples_info = []
for i, s in enumerate(samples):
    c = classify(s)
    for k in c:
        cats_all[k] += 1
    h = hashlib.md5((s['instruction'] + s['response']).encode()).hexdigest()[:8]
    samples_info.append((i, c, h))

print('=== 分类统计 (基于response结构) ===')
for k, v in cats_all.most_common():
    print(f'{v:3d}  {k}')

# 各场景候选
for target in ['tool_use', 'format', 'reasoning', 'long_task', 'recovery', 'constraint']:
    idxs = [i for i, c, h in samples_info if target in c]
    print(f'\n=== 场景 [{target}] 候选 {len(idxs)} 条: idx={idxs[:20]}')
