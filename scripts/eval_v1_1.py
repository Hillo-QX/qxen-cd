#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1 Base/LoRA 同口径评估（T006）。

在 Frozen Fresh 集 data/v1.1/fresh/fresh.jsonl 上，对基础模型与
每个候选 LoRA checkpoint（100-1200 + final）计算 test loss，
输出对比报告到 data/v1.1/eval/eval_report.json。

用法：./venv/bin/python scripts/eval_v1_1.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL = "models/qwen3.5-9b-mlx-4bit"
TEST_DATA = ROOT / "data/v1.1/eval"   # 含 test.jsonl
CKPT_DIR = ROOT / "data/v1.1/checkpoints"
OUT = ROOT / "data/v1.1/eval/eval_report.json"
BATCH_SIZE = 1
MAX_SEQ = 512
TEST_BATCHES = -1  # 全量


def main() -> int:
    from mlx_lm import load
    from mlx_lm.tuner.datasets import load_dataset
    from mlx_lm.tuner.trainer import evaluate
    from mlx_lm.tuner.utils import load_adapters

    import types

    args = types.SimpleNamespace(
        data=str(TEST_DATA), hf_dataset=False, test=True, train=False,
    )
    print("Loading pretrained model")
    model, tokenizer = load(MODEL, tokenizer_config={"trust_remote_code": True})
    print("Loading datasets")
    train_set, valid_set, test_set = load_dataset(args, tokenizer)
    print(f"Test set size: {len(test_set)}")

    from mlx_lm.tuner.datasets import CacheDataset

    results = []

    def run_eval(name, adapter_path=None):
        # 重新加载原始模型（adapter 会修改 in-place，需重载）
        m, _ = load(MODEL, tokenizer_config={"trust_remote_code": True})
        if adapter_path:
            load_adapters(m, str(adapter_path))
        t0 = time.time()
        loss = evaluate(
            model=m,
            dataset=CacheDataset(test_set),
            batch_size=BATCH_SIZE,
            num_batches=TEST_BATCHES,
            max_seq_length=MAX_SEQ,
        )
        ppl = math.exp(loss)
        elapsed = round(time.time() - t0, 1)
        results.append({"name": name, "adapter_path": str(adapter_path) if adapter_path else None,
                        "test_loss": round(loss, 4), "test_ppl": round(ppl, 3), "elapsed_s": elapsed})
        print(f"[eval] {name}: loss={loss:.4f} ppl={ppl:.3f} ({elapsed}s)")
        return loss

    # 1. Base
    run_eval("base")
    # 2. 每100iter checkpoint
    ckpts = sorted(CKPT_DIR.glob("000*_adapters.safetensors"),
                   key=lambda p: int(p.name[:7]))
    for ck in ckpts:
        run_eval(f"iter{int(ck.name[:7])}", ckpt_dir_for(ck))
    # 3. final
    final = CKPT_DIR / "adapters.safetensors"
    if final.is_file():
        run_eval("final", ckpt_dir_for(final))

    # 排序输出（按 test loss 升序 = 越好）
    results_sorted = sorted(results, key=lambda r: r["test_loss"])
    report = {
        "task": "T006",
        "test_set": str(TEST_DATA / "test.jsonl"),
        "test_rows": len(test_set),
        "batch_size": BATCH_SIZE,
        "max_seq_length": MAX_SEQ,
        "best_checkpoint": results_sorted[0],
        "ranking": results_sorted,
        "base_loss": next(r["test_loss"] for r in results if r["name"] == "base"),
        "generated_at": "2026-08-15",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[eval_v1_1] report -> {OUT}")
    print("TOP3:")
    for r in results_sorted[:3]:
        print(f"  {r['name']}: loss={r['test_loss']} ppl={r['test_ppl']}")
    return 0


def ckpt_dir_for(safetensors_file: Path) -> Path:
    """为单个 safetensors checkpoint 构造临时 adapter 目录（复用根 adapter_config.json）。"""
    tmp = CKPT_DIR / f"_tmp_eval_{safetensors_file.stem}"
    tmp.mkdir(parents=True, exist_ok=True)
    cfg = CKPT_DIR / "adapter_config.json"
    import shutil
    if not (tmp / "adapter_config.json").exists():
        shutil.copy(cfg, tmp / "adapter_config.json")
    # 用当前 checkpoint 权重覆盖
    dst = tmp / "adapters.safetensors"
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    import os
    os.symlink(safetensors_file.resolve(), dst)
    return tmp


if __name__ == "__main__":
    sys.exit(main())
