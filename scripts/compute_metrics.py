"""Compute CIR/CPR metrics over train/eval/test subsets (T017).

Uses the authoritative QXEN definitions (QXEN_distiller_training_SKILL.md §14):
  CIR = critical information preserved / critical information required   (target >= 0.98)
  CPR = correct hard constraints preserved / total hard constraints      (target >= 0.99)

Also reports compression ratio and record counts per subset. Outputs a
structured JSON result file. Read-only on source data.
"""
import json
import os
import re
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

EVAL_DIR = os.path.join(PROJECT_ROOT, "data", "eval_set")
SUBSETS = ["train", "eval", "test"]
OUT_JSON = os.path.join(PROJECT_ROOT, "logs", "metrics_report.json")
OUT_LOG = os.path.join(PROJECT_ROOT, "logs", "metrics_run.log")

CRITICAL_TOKEN_RE = re.compile(
    r"(?:\/[A-Za-z0-9_\-\u4e00-\u9fff\.]+){2,}"
    r"|(?:\b[a-zA-Z0-9_]+\.(?:py|json|jsonl|md|yaml|yml|txt|sh)\b)"
    r"|(?:\b\d{3,}\b)"
    r"|(?:\b[A-Za-z0-9_]{12,}\b)"
)
CONSTRAINT_KW = ("必须", "禁止", "不得", "不允许", "绝不", "only", "must",
                 "never", "forbidden", "do not")


def load_records(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def extract_critical(text):
    return set(t.lower() for t in CRITICAL_TOKEN_RE.findall(text))


def extract_constraints(text):
    hits = set()
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in CONSTRAINT_KW):
            hits.add(line.strip()[:60])
    return hits


def compute_metrics(records):
    crit_req = crit_pres = 0
    con_req = con_pres = 0
    char_in = char_out = 0
    for rec in records:
        inst = rec.get("instruction", "")
        resp = rec.get("response", "")
        c_req = extract_critical(inst)
        crit_req += len(c_req)
        crit_pres += len(extract_critical(resp) & c_req)
        co_req = extract_constraints(inst)
        con_req += len(co_req)
        con_pres += len(extract_constraints(resp) & co_req)
        char_in += len(inst)
        char_out += len(resp)
    cir = crit_pres / crit_req if crit_req else float("nan")
    cpr = con_pres / con_req if con_req else float("nan")
    compression = char_out / char_in if char_in else float("nan")
    return {
        "records": len(records),
        "critical_required": crit_req,
        "critical_preserved": crit_pres,
        "CIR": cir,
        "constraints_required": con_req,
        "constraints_preserved": con_pres,
        "CPR": cpr,
        "compression_ratio": compression,
    }


def main():
    t0 = time.time()
    results = {}
    ok = True
    log_lines = []
    expected_counts = {"train": 92, "eval": 12, "test": 11}

    for name in SUBSETS:
        path = os.path.join(EVAL_DIR, name, f"{name}.jsonl")
        if not os.path.isfile(path):
            log_lines.append(f"FAIL {name}: missing {path}")
            ok = False
            continue
        records = load_records(path)
        m = compute_metrics(records)
        m["subset"] = name
        m["timestamp"] = "2026-08-12"
        results[name] = m

        # validation
        n = len(records)
        if n != expected_counts[name]:
            log_lines.append(f"FAIL {name}: count {n} != expected {expected_counts[name]}")
            ok = False
        if not (m["CIR"] == m["CIR"] and m["CIR"] >= 0.98):
            log_lines.append(f"FAIL {name}: CIR={m['CIR']}")
            ok = False
        if not (m["CPR"] == m["CPR"] and m["CPR"] >= 0.99):
            log_lines.append(f"FAIL {name}: CPR={m['CPR']}")
            ok = False
        log_lines.append(
            f"OK {name}: n={n} CIR={m['CIR']:.4f} CPR={m['CPR']:.4f} "
            f"compression={m['compression_ratio']:.4f}"
        )

    summary = {
        "script": "compute_metrics.py",
        "generated": "2026-08-12",
        "definitions": "QXEN SKILL §14 (CIR=critical info recall, CPR=constraint preservation)",
        "subsets": results,
        "overall": "PASS" if ok else "FAIL",
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    elapsed = time.time() - t0
    log_lines.append(f"elapsed: {elapsed:.2f}s")
    log_lines.append(f"overall: {'PASS' if ok else 'FAIL'}")
    with open(OUT_LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log_lines) + "\n")

    print("\n".join(log_lines))
    print(f"json: {OUT_JSON}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
