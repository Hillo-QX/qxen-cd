#!/usr/bin/env python3
"""量化检查：8 个源文件内容是否已被现有 115 条样本完全覆盖。"""
import json
import os

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


def read_nonempty_lines(path):
    full = os.path.join(PROJECT_ROOT, path)
    if not os.path.isfile(full):
        return []
    with open(full, "r", encoding="utf-8", errors="replace") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def main():
    # 8 个源文件的总行数与 chunk=18 块数
    total_lines = 0
    chunk18_total = 0
    per_file = []
    for src in SOURCES:
        lines = read_nonempty_lines(src)
        n = len(lines)
        nchunk = (n + 17) // 18
        total_lines += n
        chunk18_total += nchunk
        per_file.append((src, n, nchunk))
        print(f'{src}: {n} 行 -> chunk18 {nchunk} 块')

    print(f'\n源文件总行数: {total_lines}')
    print(f'chunk=18 总块数: {chunk18_total}')

    # 现有 115 条 = 92 train + 12 eval + 11 test
    print(f'\n现有样本: 115 条 (train 92 + eval 12 + test 11)')
    print(f'覆盖率: chunk18 总块数 {chunk18_total} vs 现有 115 条')

    # 检查现有 115 条 response 是否覆盖源文件全部内容（按行 hash）
    # 现有样本的 response 是 verbatim chunk，反查源文件行覆盖
    all_src_lines = []
    for src in SOURCES:
        all_src_lines.extend(read_nonempty_lines(src))
    src_line_set = set(all_src_lines)
    print(f'\n源文件非空行去重后: {len(src_line_set)} 行')

    # 现有115条 response 中包含的源文件行
    covered = set()
    for split in ['train', 'eval', 'test']:
        p = os.path.join(PROJECT_ROOT, f'data/eval_set/{split}/{split}.jsonl')
        if not os.path.isfile(p):
            continue
        with open(p, encoding='utf-8') as f:
            for line in f:
                d = json.loads(line)
                resp = d.get('response', '')
                for ln in resp.split('\n'):
                    if ln.strip() and ln.strip() in src_line_set:
                        covered.add(ln.strip())

    print(f'现有 115 条已覆盖的源文件行: {len(covered)} / {len(src_line_set)}')
    print(f'未覆盖行: {len(src_line_set) - len(covered)}')


if __name__ == '__main__':
    main()
