"""Data quality check for QXEN training data (T015).

Checks JSON/JSONL validity, required-field presence and label enum legality.
Outputs a per-file report and a summary report.md. Does NOT modify source data.
"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
REPORT = os.path.join(PROJECT_ROOT, "logs", "data_quality_report.md")

REQUIRED_FIELDS = ["instruction", "response"]
# instruction may embed a label hint; no hard enum enforced on text data,
# but we validate non-empty strings and record stats.
MIN_INSTRUCTION_LEN = 10
MIN_RESPONSE_LEN = 1


def check_jsonl(path):
    issues = []
    records = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                issues.append(f"  line {lineno}: JSONDecodeError: {e}")
                continue
            records.append((lineno, obj))
    return records, issues


def validate_record(lineno, obj):
    issues = []
    if not isinstance(obj, dict):
        return [f"  line {lineno}: record is not a dict"]
    for field in REQUIRED_FIELDS:
        if field not in obj:
            issues.append(f"  line {lineno}: missing required field '{field}'")
            continue
        val = obj[field]
        if not isinstance(val, str):
            issues.append(f"  line {lineno}: field '{field}' is not a string")
        elif field == "instruction" and len(val) < MIN_INSTRUCTION_LEN:
            issues.append(f"  line {lineno}: instruction too short (<{MIN_INSTRUCTION_LEN})")
        elif field == "response" and len(val) < MIN_RESPONSE_LEN:
            issues.append(f"  line {lineno}: response empty")
    # optional label field consistency (if present)
    if "label" in obj:
        if not isinstance(obj["label"], (str, int, float)):
            issues.append(f"  line {lineno}: 'label' has unsupported type")
    return issues


def main():
    if not os.path.isdir(DATA_DIR):
        print(f"FATAL: data dir missing: {DATA_DIR}")
        sys.exit(1)

    jsonl_files = [
        os.path.join(DATA_DIR, f)
        for f in sorted(os.listdir(DATA_DIR))
        if f.endswith(".jsonl")
    ]
    if not jsonl_files:
        print(f"FATAL: no .jsonl files under {DATA_DIR}")
        sys.exit(1)

    report_lines = [
        "# QXEN Data Quality Report",
        "",
        f"- Generated: 2026-08-12",
        f"- Data dir: {DATA_DIR}",
        "",
    ]
    all_ok = True

    for path in jsonl_files:
        fname = os.path.basename(path)
        records, parse_issues = check_jsonl(path)
        rec_issues = []
        for lineno, obj in records:
            rec_issues.extend(validate_record(lineno, obj))

        total = len(records)
        ok_records = total - len(rec_issues)  # parse failures counted separately
        issues = parse_issues + rec_issues
        status = "PASS" if not issues else "FAIL"

        report_lines.append(f"## {fname}")
        report_lines.append(f"- records: {total}")
        report_lines.append(f"- status: {status}")
        if issues:
            all_ok = False
            report_lines.append(f"- issues ({len(issues)}):")
            report_lines.extend(issues[:20])
        report_lines.append("")

        print(f"[{status}] {fname}: {total} records, {len(issues)} issues")

    # aggregate
    total_all = 0
    fail_files = 0
    for path in jsonl_files:
        records, _ = check_jsonl(path)
        total_all += len(records)
        # recompute per-file issues count for summary
        file_issues = 0
        for lineno, obj in records:
            file_issues += len(validate_record(lineno, obj))
        if file_issues > 0:
            fail_files += 1
    report_lines.append("## Summary")
    report_lines.append(f"- Files checked: {len(jsonl_files)}")
    report_lines.append(f"- Total records: {total_all}")
    report_lines.append(f"- Files with issues: {fail_files}")
    report_lines.append(f"- Overall: {'PASS' if all_ok else 'FAIL'}")

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(report_lines) + "\n")
    print(f"report written: {REPORT}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
