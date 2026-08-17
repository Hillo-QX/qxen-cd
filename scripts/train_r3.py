#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T349 — R3 Authority/Operativeness LoRA 训练脚本（静态校验版）。

阶段: PHASE 1 R3 — Authority (T0-T4) + Operativeness (CURRENT/STALE/SUPERSEDED)
依据: QXEN_R2_R7_LEGAL_ELEMENT_TRAINING_SKILL.md §7 (超参策略) / §8 (R3 目标)

数据: data/r3/{train,valid,fresh}/  24 个 jsonl（8 任务族 × 3 split）
      train 2160 / valid 300 / fresh 540, dataset_sha256=75c282dd…
模型: models/qwen3.5-9b-mlx-4bit（冻结）
输出: models/r3/（适配器路径按 T349 任务约定）

模式:
  --check-config   仅加载配置并校验（超参 §7 范围 / 数据条数 / sha256 / 内存守卫
                   阈值 / train∩valid 文本隔离），不加载模型、不训练。
                   ★ 本任务只执行此模式（静态校验）。
  （默认/无参数）   训练模式：校验内存守卫 → 构建 data/r3/mlx staging →
                   调用 mlx_lm lora。★ 由后续训练任务在通过全部门禁后运行，
                   本任务禁止实际训练。

运行:
  ./venv/bin/python scripts/train_r3.py --check-config
  ./venv/bin/python scripts/train_r3.py            # 训练模式（本任务不执行）
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

CONFIG_PATH = "configs/r3_training.yaml"
LOG_CHECK = "logs/r3_training_config_check.log"

# skill §7 超参约束（不变量）
RANK_REQ = 8
LR_MIN, LR_MAX = 3e-6, 5e-6
EPOCHS_REQ = 1
SEQ_MIN_R3, SEQ_MAX_R3 = 512, 768
MG_BUDGET_GB_REQ = 24
MG_WIRED_GB_REQ = 18
MG_FREE_MB_REQ = 500

# 数据约束
EXPECTED_SPLITS = {"train": 2160, "valid": 300, "fresh": 540}
EXPECTED_FILES_PER_SPLIT = 8
TOTAL_FILES = 24
LABELS_OK = {"CURRENT", "STALE", "SUPERSEDED"}
AUTHORITY_TYPES_OK = {"T0", "T1", "T2", "T3", "T4"}
DATASET_SHA = "75c282dd019844e6e53261f741b80e93c94af030889c6baa84f360f916d6cb47"

PASS = "PASS"
FAIL = "FAIL"
_results: list[dict] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"check": name, "status": PASS if ok else FAIL, "detail": detail})
    print(f"  [{'OK' if ok else 'FAIL'}] {name}: {detail}")


def sha256_concat(files: list[str]) -> str:
    h = hashlib.sha256()
    for p in files:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


def load_config() -> dict:
    import yaml
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def data_files_for(split: str) -> list[str]:
    return sorted(glob.glob(f"data/r3/{split}/*.jsonl"))


def load_rows(split: str) -> list[dict]:
    rows = []
    for p in data_files_for(split):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------- 内存守卫
def check_memory_guard(cfg: dict) -> tuple[float, float]:
    """macOS 可用内存探测。返回 (free_mb, wired_gb)。失败时返回 (-1, -1)。"""
    try:
        page_size = int(subprocess.run(
            ["sysctl", "-n", "vm.pagesize"], capture_output=True, text=True).stdout.strip())
        vms = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
        free_pages = wired_pages = -1
        for line in vms.splitlines():
            if "Pages free" in line:
                free_pages = int(line.split(":")[1].strip().rstrip("."))
            elif "Pages wired down" in line:
                wired_pages = int(line.split(":")[1].strip().rstrip("."))
        if free_pages < 0 or wired_pages < 0:
            return -1.0, -1.0
        free_mb = free_pages * page_size / (1024 * 1024)
        wired_gb = wired_pages * page_size / (1024 * 1024 * 1024)
        return free_mb, wired_gb
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] 内存探测失败（{exc}），跳过运行时内存检查", flush=True)
        return -1.0, -1.0


# ---------------------------------------------------------------- 静态校验
def run_check_config() -> int:
    print(f"[R3-check-config] 配置: {CONFIG_PATH}")
    cfg = load_config()
    t = cfg["training"]
    m = cfg["memory_guard"]
    d = cfg["data"]
    a = cfg["adapter"]

    # --- 超参 §7 范围 ---
    print("[超参 §7]")
    record("rank == 8", a["rank"] == RANK_REQ, f"rank={a['rank']}")
    record("lr ∈ [3e-6, 5e-6]",
           LR_MIN <= t["learning_rate"] <= LR_MAX,
           f"lr={t['learning_rate']:.2e}")
    record("epochs == 1", t["epochs"] == EPOCHS_REQ, f"epochs={t['epochs']}")
    record("batch_size == 1", t["batch_size"] == 1, f"batch_size={t['batch_size']}")
    record("max_seq_length ∈ [512,768] (R3)",
           SEQ_MIN_R3 <= t["max_seq_length"] <= SEQ_MAX_R3,
           f"max_seq_length={t['max_seq_length']}")
    record("adapter 输出路径 models/r3", a["output"] == "models/r3", a["output"])
    record("num_layers == 4 / scale == 20",
           a["num_layers"] == 4 and a["scale"] == 20,
           f"num_layers={a['num_layers']} scale={a['scale']}")

    # --- 内存守卫阈值 ---
    print("[内存守卫]")
    record("budget_gb == 24", m["budget_gb"] == MG_BUDGET_GB_REQ, f"budget={m['budget_gb']}GB")
    record("max_wired_gb == 18", m["max_wired_gb"] == MG_WIRED_GB_REQ, f"max_wired={m['max_wired_gb']}GB")
    record("min_free_mb == 500", m["min_free_mb"] == MG_FREE_MB_REQ, f"min_free={m['min_free_mb']}MB")
    free_mb, wired_gb = check_memory_guard(cfg)
    # 注: --check-config 只校验内存守卫【阈值配置】，不把实时内存作为 PASS/FAIL 判据
    # （实时内存是系统瞬态，属于训练模式的守卫职责，见 run_train()）。
    if free_mb >= 0:
        print(f"  [INFO] 当前 free={free_mb:.0f}MB / wired={wired_gb:.2f}GB "
              f"（训练模式守卫阈值 free>={m['min_free_mb']}MB wired<={m['max_wired_gb']}GB）")

    # --- 数据 ---
    print("[数据]")
    files_ok = True
    for split, expect_n in EXPECTED_SPLITS.items():
        fs = data_files_for(split)
        n = len(fs)
        if n != EXPECTED_FILES_PER_SPLIT:
            files_ok = False
        record(f"{split} 文件数 == 8", n == EXPECTED_FILES_PER_SPLIT, f"{n} 个")
    rows = {}
    for split in EXPECTED_SPLITS:
        rows[split] = load_rows(split)
    for split, expect_n in EXPECTED_SPLITS.items():
        record(f"{split} 行数 == {expect_n}",
               len(rows[split]) == expect_n, f"{len(rows[split])}")
    # 记录行级条目完整性
    bad_schema = 0
    bad_label = 0
    bad_auth = 0
    bad_split = 0
    for split, rl in rows.items():
        for r in rl:
            if not all(k in r for k in ("text", "label", "authority_type", "operativeness", "source", "task_group")):
                bad_schema += 1
            if r.get("label") not in LABELS_OK:
                bad_label += 1
            if r.get("authority_type") not in AUTHORITY_TYPES_OK:
                bad_auth += 1
            # fresh 目录内 split 字段的规范名是 test_fresh（与 r3_freeze_manifest 的
            # split_distribution 一致：{train, valid, test_fresh}）
            ok_split = r.get("split") == split or (
                split == "fresh" and r.get("split") == "test_fresh")
            if not ok_split:
                bad_split += 1
    record("行级字段完整", bad_schema == 0, f"缺字段 {bad_schema}")
    record("label ∈ {CURRENT,STALE,SUPERSEDED}", bad_label == 0, f"非法 {bad_label}")
    record("authority_type ∈ {T0..T4}", bad_auth == 0, f"非法 {bad_auth}")
    record("split 字段与目录一致", bad_split == 0, f"不一致 {bad_split}")

    # 全局文本唯一 + train/valid/fresh 行级隔离
    all_texts = {}
    dup = 0
    for split, rl in rows.items():
        for r in rl:
            key = r["text"]
            if key in all_texts:
                dup += 1
            else:
                all_texts[key] = split
    record("全局文本唯一", dup == 0, f"重复 {dup}")
    tr_t = {r["text"] for r in rows["train"]}
    va_t = {r["text"] for r in rows["valid"]}
    fr_t = {r["text"] for r in rows["fresh"]}
    cross = len(tr_t & va_t) + len(tr_t & fr_t) + len(va_t & fr_t)
    record("train∩valid∩fresh 文本隔离", cross == 0, f"跨 split 重叠 {cross}")

    # dataset sha256
    all_files = (data_files_for("train") + data_files_for("valid")
                 + data_files_for("fresh"))
    sha = sha256_concat(all_files)
    record("dataset_sha256 匹配冻结值",
           sha == DATASET_SHA == d["dataset_sha256"],
           f"{sha[:16]}…")

    # --- 汇总 ---
    n_pass = sum(1 for r in _results if r["status"] == PASS)
    n_fail = sum(1 for r in _results if r["status"] == FAIL)
    print(f"\n[R3-check-config] 结果: {n_pass} PASS / {n_fail} FAIL")
    if n_fail == 0:
        print("[R3-check-config] ALL PASS")
        return 0
    print("[R3-check-config] FAIL — 存在未通过项", flush=True)
    return 1


# ---------------------------------------------------------------- 训练模式
def run_train(cfg: dict) -> int:
    m = cfg["memory_guard"]
    free_mb, wired_gb = check_memory_guard(cfg)
    print(f"[R3-train] 内存: free={free_mb:.0f}MB wired={wired_gb:.2f}GB "
          f"(阈值 free>={m['min_free_mb']}MB wired<={m['max_wired_gb']}GB)")
    if free_mb >= 0 and (free_mb < m["min_free_mb"] or wired_gb > m["max_wired_gb"]):
        print("[R3-train] FATAL: 内存守卫触发，安全终止（不启动训练）", flush=True)
        return 1

    staging = cfg["data"]["mlx_staging"]
    os.makedirs(staging, exist_ok=True)
    for split, fname in (("train", "train.jsonl"), ("valid", "valid.jsonl")):
        with open(os.path.join(staging, fname), "w", encoding="utf-8") as out:
            for r in load_rows(split):
                out.write(json.dumps({"text": r["text"], "label": r["label"]},
                                     ensure_ascii=False) + "\n")
    print(f"[R3-train] staging 写入 {staging}/{{train,valid}}.jsonl")

    cmd = [
        sys.executable, "-m", "mlx_lm", "lora",
        "--model", cfg["model"],
        "--train",
        "--data", staging,
        "--fine-tune-type", cfg["adapter"]["fine_tune_type"],
        "--num-layers", str(cfg["adapter"]["num_layers"]),
        "--batch-size", str(cfg["training"]["batch_size"]),
        "--iters", str(cfg["training"]["epochs"] * cfg["data"]["train_rows"]),
        "--learning-rate", f"{cfg['training']['learning_rate']:.1e}",
        "--adapter-path", cfg["adapter"]["output"],
        "--save-every", str(cfg["training"]["save_every"]),
        "--max-seq-length", str(cfg["training"]["max_seq_length"]),
        "--grad-checkpoint",
        "--grad-accumulation-steps", str(cfg["training"]["grad_accumulation_steps"]),
        "--seed", str(cfg["training"]["seed"]),
        "--steps-per-report", str(cfg["training"]["steps_per_report"]),
        "--val-batches", "0",
    ]
    print(f"[R3-train] 命令:\n  {' '.join(cmd)}\n", flush=True)
    t0 = time.time()
    log = "logs/r3/r3_train.log"
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    print(f"[R3-train] 退出码: {proc.returncode} | 耗时 {time.time()-t0:.0f}s", flush=True)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="QXEN R3 LoRA 训练/静态校验")
    ap.add_argument("--check-config", action="store_true",
                    help="仅校验配置与数据（不加载模型、不训练）")
    args = ap.parse_args()

    if args.check_config:
        code = run_check_config()
        os.makedirs(os.path.dirname(LOG_CHECK), exist_ok=True)
        with open(LOG_CHECK, "w", encoding="utf-8") as f:
            f.write("QXEN R3 config check — %s\n" %
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            for r in _results:
                f.write(f"[{r['status']}] {r['check']}: {r['detail']}\n")
            f.write(f"SUMMARY: {sum(1 for r in _results if r['status']==PASS)} PASS / "
                    f"{sum(1 for r in _results if r['status']==FAIL)} FAIL\n")
        print(f"[R3-check-config] 日志已写入 {LOG_CHECK}")
        return code

    cfg = load_config()
    print("[R3-train] 校验配置…")
    if run_check_config() != 0:
        print("[R3-train] FATAL: 配置校验未通过", flush=True)
        return 1
    return run_train(cfg)


if __name__ == "__main__":
    sys.exit(main())
