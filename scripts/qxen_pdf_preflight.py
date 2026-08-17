"""Deterministic PDF layout/table/value preflight for QXEN long-text input."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import pdfplumber
except ImportError:  # pragma: no cover - runtime dependency check
    pdfplumber = None

NUMBER_RE = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?")
UNIT_RE = re.compile(r"%|百分点|bp|亿元|万亿|万元|元|万人|户")


def _numeric_tokens(text: str) -> list[str]:
    return NUMBER_RE.findall(text)


def extract_pdf_text(path: str | Path, max_pages: int = 200) -> str:
    """Extract page-marked PDF text outside the GPT context."""
    pdf_path = Path(str(path).split("#", 1)[0])
    if pdfplumber is None:
        raise RuntimeError("pdfplumber_unavailable")
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages[:max_pages], 1):
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            if text.strip():
                pages.append(f"[PAGE {page_no}]\n{text.strip()}")
    return "\n\n".join(pages)


def preflight_pdf(path: str | Path, max_pages: int = 80) -> dict[str, Any]:
    pdf_path = Path(str(path).split("#", 1)[0])
    result: dict[str, Any] = {
        "source": str(pdf_path),
        "backend": "pdfplumber",
        "pages": 0,
        "page_layout": [],
        "table_candidates": [],
        "numeric_continuity": {"status": "NOT_CHECKED", "warnings": []},
    }
    if pdfplumber is None:
        result["status"] = "UNAVAILABLE"
        result["warnings"] = ["pdfplumber_unavailable"]
        return result
    if not pdf_path.is_file():
        result["status"] = "NOT_APPLICABLE"
        result["warnings"] = ["pdf_source_not_local"]
        return result

    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages[:max_pages]
        result["pages"] = len(pages)
        row_counts: list[int] = []
        unit_sets: list[set[str]] = []
        for page_no, page in enumerate(pages, 1):
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            rows: list[list[dict[str, Any]]] = []
            for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
                if not rows or abs(float(word["top"]) - float(rows[-1][0]["top"])) > 3:
                    rows.append([])
                rows[-1].append({
                    "text": word["text"], "x0": round(float(word["x0"]), 2),
                    "x1": round(float(word["x1"]), 2), "top": round(float(word["top"]), 2),
                    "bottom": round(float(word["bottom"]), 2),
                })
            numeric_rows = []
            for row in rows:
                text = " ".join(item["text"] for item in row)
                nums = _numeric_tokens(text)
                if nums:
                    numeric_rows.append({"text": text, "numbers": nums,
                                         "columns": len(row), "bbox": {
                                             "x0": row[0]["x0"], "x1": row[-1]["x1"],
                                             "top": row[0]["top"], "bottom": row[-1]["bottom"]}})
                    unit_sets.append(set(UNIT_RE.findall(text)))
            row_counts.append(len(numeric_rows))
            result["page_layout"].append({"page": page_no, "width": page.width,
                                           "height": page.height, "word_count": len(words),
                                           "row_count": len(rows), "numeric_row_count": len(numeric_rows)})
            if len(numeric_rows) >= 2:
                # Keep only compact structural evidence; full bbox rows stay out of GPT context.
                result["table_candidates"].append({
                    "page": page_no,
                    "row_count": len(numeric_rows),
                    "column_counts": sorted({row["columns"] for row in numeric_rows}),
                    "sample_rows": [
                        {"text": row["text"][:120], "numbers": row["numbers"][:12]}
                        for row in numeric_rows[:2]
                    ],
                })

    warnings: list[str] = []
    nonzero = [count for count in row_counts if count]
    if len(nonzero) >= 3 and max(nonzero) > 2 * max(1, min(nonzero)):
        warnings.append("numeric_row_count_discontinuity")
    units = {unit for group in unit_sets for unit in group}
    if len(units) > 1:
        warnings.append("mixed_units_require_review")
    result["numeric_continuity"] = {
        "status": "WARNING" if warnings else "PASS",
        "numeric_rows_by_page": row_counts,
        "units": sorted(units),
        "warnings": warnings,
    }
    result["table_count"] = len(result["table_candidates"])
    result["table_candidates"] = result["table_candidates"][:8]
    result["sample_rows"] = [
        sample for table in result["table_candidates"]
        for sample in table.get("sample_rows", [])
    ][:3]
    result["status"] = "WARNING" if warnings else "PASS"
    return result
