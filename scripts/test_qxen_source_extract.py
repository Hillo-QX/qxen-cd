#!/usr/bin/env python3
"""Regression tests for deterministic source-type extraction."""
from __future__ import annotations

import tempfile
from pathlib import Path
from zipfile import ZipFile

from qxen_source_extract import extract_source_text

DOC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>DOCX正文</w:t></w:r></w:p></w:body>
</w:document>"""


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        docx = root / "a.docx"
        with ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", DOC_XML)
        text, kind = extract_source_text(docx)
        assert text == "DOCX正文" and kind == "docx_ooxml_text"
        for suffix, content, expected in (
            (".json", '{"b": 2, "a": 1}', "json_structured_text"),
            (".jsonl", '{"a": 1}\n{"b": 2}\n', "jsonl_structured_text"),
            (".csv", "name,value\nA,1\n", "csv_structured_text"),
            (".tsv", "name\tvalue\nA\t1\n", "tsv_structured_text"),
            (".yaml", "name: sample\n", "yaml_source_text"),
            (".py", "print('ok')\n", "python_source_text"),
        ):
            path = root / f"sample{suffix}"
            path.write_text(content, encoding="utf-8")
            _, kind = extract_source_text(path)
            assert kind == expected, (suffix, kind)
        toml_path = root / "sample.toml"
        toml_path.write_text("enabled = true\n", encoding="utf-8")
        _, kind = extract_source_text(toml_path)
        assert kind in {"toml_structured_text", "toml_source_text_no_parser"}
        xls = root / "legacy.xls"
        xls.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 40)
        text, kind = extract_source_text(xls)
        assert text == "" and kind == "xls_binary_structured_unsupported"
    print("test_qxen_source_extract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
