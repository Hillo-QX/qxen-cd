#!/usr/bin/env python3
"""Step A: 扩大 Validation Set（方案 B，扩展源文件范围）。

背景（2026-08-12）：
- 原 8 个源文件（build_real_dataset.py SOURCES）共 1670 行，chunk=18 仅 120 块，
  其中 115 块已被现有 train/eval/test 占用，剩余未覆盖行仅 92 行。
- 细粒度切割（chunk=9/6）会产生大量与 train 的内容重叠（14 条 100% 子串重叠），
  无法满足"validation 与 train 无重叠 + >=30 条"。
- Dispatcher 决策：扩展源文件范围到项目内其他未使用本地文件。

本脚本：
1. 保留现有 eval(12) 作为 validation 核心；
2. 从扩展源文件（logs/、测试/、scripts/、deepseek_dispatcher_mcp.py、PROGRESS.md 等）
   用 chunk=12 生成新候选；
3. 强制剔除所有与 train(92) 有内容重叠的候选（子串级检测）；
4. 按场景补齐（tool_use/format/reasoning/long_task/recovery/constraint 每场景>=3）；
5. 总数 >=30，输出 data/eval_set/valid.jsonl（prompt/completion 格式）。
"""
import json
import os
import re
import hashlib
import glob
from difflib import SequenceMatcher

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 扩展源文件范围：原 8 个 + 项目内其他未使用本地文件
SOURCES = [
    # 原 8 个（保留，若未完全覆盖仍可用）
    "调度状态/QWEN蒸馏上下文_codex_kimi.md",
    "调度状态/QWEN让渡清单.md",
    "调度状态/QWEN执行规则.md",
    "QXEN_distiller_training_SKILL.md",
    "README.md",
    "日志/dispatcher.log",
    "测试/end_to_end_test.py",
    "测试/mcp_dispatch.py",
    # 扩展：日志
    "logs/memory_monitor.log",
    "logs/train_safe_run.log",
    "logs/train_safe_launcher.log",
    "logs/lora_train_001.log",
    "logs/first_milestone_validation.log",
    "logs/first_milestone_evaluation.log",
    "logs/metrics_run.log",
    # 扩展：测试套件（真实 Agent 场景）
    "测试/test_dispatcher.py",
    "测试/stress/run_stress.py",
    "测试/stress/stress_dispatcher_mcp.py",
    "测试/stress/harness_lessons.py",
    "测试/stress/LESSONS_DISTILLED.md",
    "测试/stress/README.md",
    "测试/gate_test/gate_dispatcher_mcp.py",
    "测试/gate_test/run_gate_harness.py",
    "测试/min_frontier/run_frontier.py",
    "测试/min_frontier/frontier_tasks.py",
    "测试/qwen_selfwrite/compute.py",
    # 扩展：核心代码与进度
    "deepseek_dispatcher_mcp.py",
    "PROGRESS.md",
    # 扩展：scripts
    "scripts/split_data.py",
    "scripts/compute_metrics.py",
    "scripts/data_quality_check.py",
    "scripts/evaluate.py",
    "scripts/memory_monitor.sh",
    "scripts/train_safe.sh",
]

PROMPT_PREFIX = "上下文选择与状态蒸馏：给定以下 Agent 会话/文档片段，识别并保留关键信息（目标、约束、路径、验收标准、已验证事实）。\n\n"


def read_lines(path):
    """返回文件的非空行（行内容保留原样，不去首尾空白避免改变语义）。"""
    full = os.path.join(PROJECT_ROOT, path)
    if not os.path.isfile(full):
        return []
    with open(full, "r", encoding="utf-8", errors="replace") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip()]


def chunk_lines(lines, size):
    out = []
    for i in range(0, len(lines), size):
        out.append("\n".join(lines[i:i + size]))
    return out


def classify(resp):
    cats = set()
    if re.search(r'(TOOL|工具|bash|Run:|Edit:|Read:|命令|SIGTERM|kill|监测)', resp, re.I):
        cats.add('tool_use')
    if re.search(r'(`{3}|JSON|yaml|schema|结构化|"task_id"|"status"|\[.*\]|\{.*\})', resp, re.I):
        cats.add('format')
    if re.search(r'(因此|判定|结论|决定|优先级|权衡|因为|由于|若.*则|如果.*那么)', resp):
        cats.add('reasoning')
    if re.search(r'(多阶段|多步|持续|跨轮|迭代|阶段|Phase|长期|长任务)', resp):
        cats.add('long_task')
    if re.search(r'(失败|重试|escalate|降级|异常|恢复|retry|panic|崩溃)', resp, re.I):
        cats.add('recovery')
    if re.search(r'(禁止|不得|必须|红线|forbidden|constraint|约束|阈值|WARN)', resp, re.I):
        cats.add('constraint')
    return cats


def content_hash(text):
    return hashlib.md5(text.encode()).hexdigest()


def max_overlap_ratio(cand, train_responses):
    """返回候选与任一 train response 的最长公共子串比率（0.0~1.0）。"""
    c = cand.strip()
    if not c:
        return 0.0
    best = 0.0
    for t in train_responses:
        if not t:
            continue
        if c in t or t in c:
            return 1.0
        m = SequenceMatcher(None, c, t).find_longest_match(0, len(c), 0, len(t))
        ratio = m.size / len(c)
        if ratio > best:
            best = ratio
    return best


def has_substr_overlap(cand, train_responses, threshold_chars=60, ratio_threshold=0.20):
    """检测候选 completion 是否与任一 train response 有内容重叠。

    两个判据任一命中即视为重叠：
    1) 存在 >=threshold_chars 的公共子串；
    2) 最长公共子串占 candidate 长度的比例 >= ratio_threshold（捕获短样本高重叠）。
    """
    c = cand.strip()
    if not c:
        return False
    if max_overlap_ratio(c, train_responses) >= ratio_threshold:
        return True
    if len(c) < threshold_chars:
        # 短文本直接全文比较
        return any(c in t for t in train_responses)
    # 前缀/后缀 + 分段检测
    for t in train_responses:
        if not t:
            continue
        # 完整包含
        if c in t or t in c:
            return True
        # 分段（前/中/后各取 threshold_chars 窗口）
        for window in (c[:threshold_chars], c[len(c)//2:len(c)//2+threshold_chars], c[-threshold_chars:]):
            if window in t:
                return True
    return False


def main():
    # 1. train 的 response 列表（用于重叠检测）
    train_path = os.path.join(PROJECT_ROOT, 'data/eval_set/train/train.jsonl')
    train_responses = []
    train_hashes = set()
    with open(train_path, encoding='utf-8') as f:
        for line in f:
            d = json.loads(line)
            train_responses.append(d['response'])
            train_hashes.add(content_hash(d['instruction'] + d['response']))

    # 2. 现有 eval 12 条作为核心（已验证与 train 无重叠）
    eval_path = os.path.join(PROJECT_ROOT, 'data/eval_set/eval/eval.jsonl')
    core = []
    with open(eval_path, encoding='utf-8') as f:
        for line in f:
            d = json.loads(line)
            core.append({
                'prompt': d['instruction'],
                'completion': d['response'],
                'cats': classify(d['response']),
                'hash': content_hash(d['instruction'] + d['response']),
            })

    # 3. 从扩展源文件生成新候选，剔除与 train 重叠的
    candidates = []
    seen = set()
    for src in SOURCES:
        lines = read_lines(src)
        if not lines:
            continue
        for size in (12, 18):
            for c in chunk_lines(lines, size):
                if len(c) < 30:
                    continue
                h = content_hash(c)
                if h in seen:
                    continue
                seen.add(h)
                if has_substr_overlap(c, train_responses):
                    continue
                candidates.append({
                    'prompt': PROMPT_PREFIX + c,
                    'completion': c,
                    'cats': classify(c),
                    'hash': h,
                })

    print(f'核心(eval12): {len(core)} 条')
    print(f'新候选(与train无重叠): {len(candidates)} 条')

    # 4. 场景补齐
    TARGET_CATS = ['tool_use', 'format', 'reasoning', 'long_task', 'recovery', 'constraint']
    MIN_PER_CAT = 3
    REQUIRED = 30

    selected = list(core)
    selected_hashes = {c['hash'] for c in selected}

    def cat_count(sel):
        cnt = {c: 0 for c in TARGET_CATS}
        for s in sel:
            for c in s['cats']:
                cnt[c] += 1
        return cnt

    # 先按场景配额补足
    for cat in TARGET_CATS:
        cnt = cat_count(selected)
        need = MIN_PER_CAT - cnt[cat]
        if need <= 0:
            continue
        picked = [c for c in candidates if cat in c['cats'] and c['hash'] not in selected_hashes]
        for c in picked[:need]:
            selected.append(c)
            selected_hashes.add(c['hash'])

    # 再补足到 30 条
    remaining = [c for c in candidates if c['hash'] not in selected_hashes]
    remaining.sort(key=lambda c: (c['hash']))
    idx = 0
    while len(selected) < REQUIRED and idx < len(remaining):
        c = remaining[idx]
        selected.append(c)
        selected_hashes.add(c['hash'])
        idx += 1

    # 5. 剔除与 train 仍有重叠的样本，从候选池替换
    final_overlap = 0
    replaced = 0
    clean = []
    for s in selected:
        if has_substr_overlap(s['completion'], train_responses):
            # 找替代候选（场景相似、无重叠、未选中）
            replacement = None
            for c in candidates:
                if c['hash'] in selected_hashes:
                    continue
                if has_substr_overlap(c['completion'], train_responses):
                    continue
                if s['cats'] & c['cats']:
                    replacement = c
                    break
            if replacement is None:
                for c in candidates:
                    if c['hash'] in selected_hashes:
                        continue
                    if not has_substr_overlap(c['completion'], train_responses):
                        replacement = c
                        break
            if replacement:
                selected_hashes.add(replacement['hash'])
                clean.append(replacement)
                replaced += 1
            else:
                final_overlap += 1
        else:
            clean.append(s)

    # 6. 写入 valid.jsonl
    out_path = os.path.join(PROJECT_ROOT, 'data/eval_set/valid.jsonl')
    with open(out_path, 'w', encoding='utf-8') as f:
        for s in clean:
            f.write(json.dumps({'prompt': s['prompt'], 'completion': s['completion']}, ensure_ascii=False) + '\n')

    cnt = cat_count(clean)
    print(f'\n=== 最终 validation set: {len(clean)} 条 ===')
    for c in TARGET_CATS:
        mark = 'OK' if cnt[c] >= MIN_PER_CAT else 'LOW'
        print(f'  {c:12s}: {cnt[c]:3d}  [{mark}]')
    print(f'替换重叠样本: {replaced} 条')
    print(f'仍与 train 子串重叠: {final_overlap} 条')
    print(f'输出: {out_path}')


if __name__ == '__main__':
    main()
