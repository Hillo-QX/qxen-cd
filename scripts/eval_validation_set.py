#!/usr/bin/env python3
"""Step A: 用扩大后的 30 条 validation set 重新评估现有 checkpoint。

目的：判断 val≈1.56 是真实平台还是小验证集(12条)的测量噪声。
评估 iter450 / iter500 / iter550 三个 checkpoint，在全 30 条 valid 上计算 loss。
只读评估，不训练、不修改任何代码/配置/产物。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from mlx_lm import load
from mlx_lm.tuner.utils import load_adapters
from mlx_lm.tuner.datasets import CompletionsDataset, CacheDataset
from mlx_lm.tuner.trainer import evaluate

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(PROJECT_ROOT, 'models/qwen3.5-9b-mlx-4bit')
VALID = os.path.join(PROJECT_ROOT, 'data/eval_set/valid.jsonl')
EVAL_ORIG = os.path.join(PROJECT_ROOT, 'data/eval_set/eval/eval.jsonl')
ADAPTER_DIR = os.path.join(PROJECT_ROOT, 'outputs/lora_adapters_safe')
MAX_SEQ = 512


def load_data_file(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def load_valid_dataset(data_path=None):
    data = load_data_file(data_path or VALID)
    # CompletionsDataset 需要 tokenizer
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
    ds = CompletionsDataset(data, tokenizer, 'prompt', 'completion', False)
    return CacheDataset(ds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adapters', nargs='+', default=[
        '0000450_adapters.safetensors',
        '0000500_adapters.safetensors',
        '0000550_adapters.safetensors',
    ], help='adapter 文件名（相对 ADAPTER_DIR）')
    parser.add_argument('--num-batches', type=int, default=-1,
                        help='评估 batch 数，-1 用整个 validation set')
    parser.add_argument('--data', type=str, default=None,
                        help='数据文件路径（默认 data/eval_set/valid.jsonl）；'
                             '可用 eval.jsonl 复现原始 12 条评估')
    parser.add_argument('--adapter-dir', type=str, default=None,
                        help='adapter 目录（默认 outputs/lora_adapters_safe）')
    args = parser.parse_args()

    data_path = args.data or VALID
    adapter_dir = args.adapter_dir or ADAPTER_DIR
    print(f'模型: {MODEL}')
    print(f'数据: {data_path}')
    print(f'adapter 目录: {adapter_dir}')
    print()

    results = {}
    for ad in args.adapters:
        ad_path = os.path.join(adapter_dir, ad)
        if not os.path.isfile(ad_path):
            print(f'  [SKIP] 不存在: {ad_path}')
            continue
        print(f'\n=== 评估 {ad} ===')
        # 构造临时 adapter 目录（官方 load_adapters 需要目录结构）
        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix='adapter_')
        import shutil
        shutil.copy(os.path.join(adapter_dir, 'adapter_config.json'), os.path.join(tmp_dir, 'adapter_config.json'))
        shutil.copy(ad_path, os.path.join(tmp_dir, 'adapters.safetensors'))
        # 重新加载 base model 并加载 adapter
        model2, _ = load(MODEL, tokenizer_config={'trust_remote_code': True})
        load_adapters(model2, tmp_dir)
        valid_ds = load_valid_dataset(data_path)
        t0 = time.time()
        loss = evaluate(
            model=model2,
            dataset=valid_ds,
            batch_size=1,
            num_batches=args.num_batches,
            max_seq_length=MAX_SEQ,
        )
        dt = time.time() - t0
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f'  loss = {loss:.4f}  ({dt:.1f}s, {len(valid_ds)} batches)')
        results[ad] = {'loss': loss, 'batches': len(valid_ds)}

    print('\n=== 结果汇总 ===')
    for ad, r in results.items():
        print(f'  {ad}: loss={r["loss"]:.4f}  batches={r["batches"]}')


if __name__ == '__main__':
    main()
