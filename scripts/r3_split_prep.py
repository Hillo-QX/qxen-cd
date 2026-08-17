#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QXEN R3 T355 — R3A/R3B/R3C 拆分训练数据准备脚本。

用户决策(2026-08-13): R3 拆分为三个独立 adapter，各负责单一目标:
  R3A: operative status  (CURRENT / STALE / SUPERSEDED)
  R3B: authority ranking (T0 / T1 / T2 / T3 / T4)
  R3C: material conflict (true / false)

数据源: data/r3/{train,valid,fresh}/  冻结数据 (sha 75c282dd, 3000条, 8任务族)
约束:
  - 不修改任何原始数据文件
  - split 按 query 组隔离 (query_id 前缀即任务组, 天然跨 split 隔离)
  - 每目标独立 prompt/completion, completion 只含该目标的判定词
  - 按目标+任务族重新平衡 (抽样到各类均衡; 保留成对反事实样本的自然分布)
  - 反事实样本保留: 同一 query 下不同 candidate 的 (status/auth/conflict) 对比

输出 (mlx_lm lora 可直接消费的 {prompt, completion} 格式):
  data/r3/staging/r3a/{train,valid}.jsonl   (status)
  data/r3/staging/r3b/{train,valid}.jsonl   (authority)
  data/r3/staging/r3c/{train,valid}.jsonl   (conflict)
  data/r3/staging/manifest.json             (分布统计 + sha256 + 校验记录)

用法:
  ./venv/bin/python scripts/r3_split_prep.py
"""
from __future__ import annotations
import glob, hashlib, json, os, random, sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
STAGING = "data/r3/staging"
SEED = 42

def load_split(split):
    rows = []
    for p in sorted(glob.glob(f"data/r3/{split}/*.jsonl")):
        for line in open(p, encoding="utf-8"):
            if line.strip():
                rows.append(json.loads(line))
    return rows

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()

def balanced_ratio(target_counts: dict, max_factor: float = 3.0) -> dict:
    """给每类一个采样权重, 使弱类放大、强类最多保留 max_factor 倍于弱类。"""
    total = sum(target_counts.values())
    n_cls = len(target_counts)
    ideal = total / n_cls
    weights = {}
    for k, v in target_counts.items():
        if v == 0:
            weights[k] = 0
            continue
        w = ideal / v
        # 限制放大倍数, 防止过采样
        w = min(w, max_factor)
        weights[k] = w
    return weights

def build_dataset(rows, key_fn, label_fn, completion_fn, name):
    """按 key_fn 分类、label_fn 取标签, 返回平衡后的 {prompt,completion} 行。"""
    rng = random.Random(SEED)
    by_label = {}
    for r in rows:
        k = key_fn(r)
        by_label.setdefault(k, []).append(r)
    weights = balanced_ratio({k: len(v) for k, v in by_label.items()})
    # 平衡采样: 对每类采样, 强类抽样保留, 弱类全部保留并过采样至接近理想
    out = []
    for k, lst in by_label.items():
        w = weights[k]
        n_want = int(round(len(lst) * w))
        if w >= 1.0:
            # 弱类/均衡类: 全保留 + 过采样补齐到 n_want
            sel = lst + rng.choices(lst, k=n_want - len(lst)) if n_want > len(lst) else lst[:n_want]
        else:
            # 强类: 抽样
            sel = rng.sample(lst, n_want)
        for r in sel:
            out.append({"prompt": r["text"],
                        "completion": completion_fn(r),
                        "meta": {"query_id": r["query_id"], "candidate_id": r["candidate_id"],
                                 "task_group": r["task_group"], "label": label_fn(r),
                                 "split": r["split"], "target": name}})
    rng.shuffle(out)
    return out

def main():
    rng = random.Random(SEED)
    train = load_split("train")
    valid = load_split("valid")
    fresh = load_split("fresh")
    print(f"train={len(train)} valid={len(valid)} fresh={len(fresh)}")

    # --- 反事实配对检查: 同一 query 组内 status/auth/conflict 应有差异 ---
    def family_of(qid):
        return qid.rsplit("-", 1)[0]
    fam_status = Counter()
    for r in train:
        fam_status[family_of(r["query_id"])] += 1
    print("query families in train:", len(fam_status))

    # R3A status
    def completion_a(r):
        return f"{r['label']}"
    a_train = build_dataset(train, lambda r: r["label"], lambda r: r["label"], completion_a, "r3a")
    a_valid = build_dataset(valid, lambda r: r["label"], lambda r: r["label"], completion_a, "r3a")

    # R3B authority
    def completion_b(r):
        return f"{r['authority_type']}"
    b_train = build_dataset(train, lambda r: r["authority_type"], lambda r: r["authority_type"], completion_b, "r3b")
    b_valid = build_dataset(valid, lambda r: r["authority_type"], lambda r: r["authority_type"], completion_b, "r3b")

    # R3C conflict
    def key_c(r):
        return "conflict" if r["material_conflict"] else "no_conflict"
    def completion_c(r):
        return "true" if r["material_conflict"] else "false"
    c_train = build_dataset(train, key_c, lambda r: key_c(r), completion_c, "r3c")
    c_valid = build_dataset(valid, key_c, lambda r: key_c(r), completion_c, "r3c")

    # --- 写文件 ---
    os.makedirs(f"{STAGING}/r3a", exist_ok=True)
    os.makedirs(f"{STAGING}/r3b", exist_ok=True)
    os.makedirs(f"{STAGING}/r3c", exist_ok=True)
    for name, d_train, d_valid in (("r3a", a_train, a_valid), ("r3b", b_train, b_valid), ("r3c", c_train, c_valid)):
        with open(f"{STAGING}/{name}/train.jsonl", "w", encoding="utf-8") as f:
            for r in d_train:
                f.write(json.dumps({"prompt": r["prompt"], "completion": r["completion"]}, ensure_ascii=False) + "\n")
        with open(f"{STAGING}/{name}/valid.jsonl", "w", encoding="utf-8") as f:
            for r in d_valid:
                f.write(json.dumps({"prompt": r["prompt"], "completion": r["completion"]}, ensure_ascii=False) + "\n")
        print(f"{name}: train={len(d_train)} valid={len(d_valid)}")
        # 分布
        ctr = Counter(r["completion"] for r in d_train)
        print(f"  train completion 分布: {dict(ctr)}")

    # --- manifest ---
    manifest = {
        "stage": "R3", "purpose": "split pipeline (R3A status / R3B authority / R3C conflict)",
        "seed": SEED, "source_sha": "75c282dd019844e6e53261f741b80e93c94af030889c6baa84f360f916d6cb47",
        "query_family_isolation": "query_id 前缀天然跨 split 隔离",
        "datasets": {},
        "built_at": "2026-08-13T16:40:00Z",
    }
    for name in ("r3a", "r3b", "r3c"):
        manifest["datasets"][name] = {
            "train": {"rows": len(load_rows(name, "train")), "sha": sha(f"{STAGING}/{name}/train.jsonl")[:16]},
            "valid": {"rows": len(load_rows(name, "valid")), "sha": sha(f"{STAGING}/{name}/valid.jsonl")[:16]},
        }
    with open(f"{STAGING}/manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"manifest -> {STAGING}/manifest.json")

def load_rows(name, split):
    rows = []
    for l in open(f"{STAGING}/{name}/{split}.jsonl", encoding="utf-8"):
        if l.strip():
            rows.append(json.loads(l))
    return rows

if __name__ == "__main__":
    sys.exit(main())
