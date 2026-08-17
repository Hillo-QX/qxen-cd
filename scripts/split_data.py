"""Split real_samples.jsonl into train/eval/test subsets (T016).

Deterministic split with fixed seed. Writes JSONL files into
data/eval_set/{train,eval,test}/ and prints verification stats.
Does NOT modify the source data file.
"""
import json
import os
import random
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

SRC = os.path.join(PROJECT_ROOT, "data", "real_samples.jsonl")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "eval_set")

SEED = 42
TRAIN_RATIO = 0.80
EVAL_RATIO = 0.10
# test = remainder (0.10)
TOLERANCE = 2  # records, allowed deviation from expected counts


def load_records(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def save_records(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def record_id(rec):
    """Unique identity for dedup check: instruction+response content."""
    return rec.get("instruction", "") + "\u0000" + rec.get("response", "")


def main():
    records = load_records(SRC)
    n = len(records)
    if n == 0:
        print("FATAL: no records")
        return 1

    rng = random.Random(SEED)
    shuffled = list(records)
    rng.shuffle(shuffled)

    n_train = round(n * TRAIN_RATIO)
    n_eval = round(n * EVAL_RATIO)
    n_test = n - n_train - n_eval

    train = shuffled[:n_train]
    eval_ = shuffled[n_train : n_train + n_eval]
    test = shuffled[n_train + n_eval :]

    save_records(os.path.join(OUT_DIR, "train", "train.jsonl"), train)
    save_records(os.path.join(OUT_DIR, "eval", "eval.jsonl"), eval_)
    save_records(os.path.join(OUT_DIR, "test", "test.jsonl"), test)

    # --- verification ---
    ok = True

    # 1. sum == n
    total = len(train) + len(eval_) + len(test)
    if total != n:
        ok = False
        print(f"FAIL: sum {total} != source {n}")
    else:
        print(f"sum check OK: {total} == {n}")

    # 2. ratios within tolerance
    expected = {
        "train": round(n * TRAIN_RATIO),
        "eval": round(n * EVAL_RATIO),
        "test": round(n * (1 - TRAIN_RATIO - EVAL_RATIO)),
    }
    actual = {"train": len(train), "eval": len(eval_), "test": len(test)}
    for name in ("train", "eval", "test"):
        dev = abs(actual[name] - expected[name])
        status = "OK" if dev <= TOLERANCE else "FAIL"
        if dev > TOLERANCE:
            ok = False
        print(f"ratio check [{name}]: actual={actual[name]} expected~{expected[name]} dev={dev} {status}")

    # 3. exclusivity (no duplicates across subsets)
    seen = {}
    dup = []
    for name, subset in (("train", train), ("eval", eval_), ("test", test)):
        for rec in subset:
            rid = record_id(rec)
            if rid in seen:
                dup.append((seen[rid], name))
            else:
                seen[rid] = name
    if dup:
        ok = False
        print(f"FAIL: duplicate records across subsets: {dup[:5]}")
    else:
        print(f"exclusivity check OK: {len(seen)} unique records across 3 subsets")

    print(f"train={actual['train']} eval={actual['eval']} test={actual['test']}")
    print(f"OVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
