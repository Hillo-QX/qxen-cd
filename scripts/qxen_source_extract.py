"""Deterministic source extraction shared by QXEN longtext and source_slice."""
from __future__ import annotations

import csv
import io
import json
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
XLSX_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _docx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    blocks = []
    body = root.find("w:body", WORD_NS)
    for block in body or []:
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            value = "".join(node.text or "" for node in block.findall(".//w:t", WORD_NS)).strip()
            if value:
                blocks.append(value)
        elif tag == "tbl":
            for row in block.findall("./w:tr", WORD_NS):
                cells = []
                for cell in row.findall("./w:tc", WORD_NS):
                    cells.append(" ".join(
                        node.text or "" for node in cell.findall(".//w:t", WORD_NS)
                    ).strip())
                value = " | ".join(cell for cell in cells if cell)
                if value:
                    blocks.append(value)
    return "\n".join(blocks)


def _pptx_text(path: Path) -> str:
    blocks = []
    with ZipFile(path) as archive:
        names = sorted(name for name in archive.namelist()
                       if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
        for name in names:
            root = ET.fromstring(archive.read(name))
            values = [node.text or "" for node in root.iter()
                      if node.tag.rsplit("}", 1)[-1] == "t"]
            text = " ".join(value.strip() for value in values if value.strip())
            if text:
                blocks.append(text)
    return "\n".join(blocks)


def _xlsx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.iter()
                              if node.tag.rsplit("}", 1)[-1] == "t")
                      for item in root]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {
            item.attrib.get("Id"): item.attrib.get("Target", "")
            for item in rels
        }
        rows = []
        for sheet in workbook.findall(".//main:sheet", XLSX_NS):
            rid = sheet.attrib.get("{%s}id" % XLSX_NS["rel"])
            target = rel_map.get(rid, "")
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            if target not in archive.namelist():
                continue
            root = ET.fromstring(archive.read(target))
            for row in root.findall(".//main:sheetData/main:row", XLSX_NS):
                values = []
                for cell in row.findall("main:c", XLSX_NS):
                    value = cell.find("main:v", XLSX_NS)
                    text = "" if value is None else (value.text or "")
                    if cell.attrib.get("t") == "s" and text.isdigit():
                        text = shared[int(text)] if int(text) < len(shared) else text
                    values.append(text)
                if any(values):
                    rows.append(" | ".join(values))
    return "\n".join(rows)


def _structured_text(path: Path, raw: bytes) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".xls":
        return "", "xls_binary_structured_unsupported"
    if suffix == ".docx":
        return _docx_text(path), "docx_ooxml_text"
    if suffix == ".pptx":
        return _pptx_text(path), "pptx_ooxml_text"
    if suffix == ".xlsx":
        return _xlsx_text(path), "xlsx_ooxml_table_text"
    text = raw.decode("utf-8", errors="replace")
    if suffix == ".json":
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, indent=2), "json_structured_text"
        except json.JSONDecodeError:
            return text, "json_utf8_text_invalid"
    if suffix == ".jsonl":
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.dumps(json.loads(line), ensure_ascii=False, sort_keys=True))
            except json.JSONDecodeError:
                rows.append(line)
        return "\n".join(rows), "jsonl_structured_text"
    if suffix in {".csv", ".tsv"}:
        dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
        rows = [" | ".join(row) for row in csv.reader(io.StringIO(text), dialect=dialect)]
        return "\n".join(rows), f"{suffix[1:]}_structured_text"
    if suffix == ".toml":
        if tomllib is None:
            return text, "toml_source_text_no_parser"
        try:
            return json.dumps(tomllib.loads(text), ensure_ascii=False, indent=2), "toml_structured_text"
        except tomllib.TOMLDecodeError:
            return text, "toml_utf8_text_invalid"
    if suffix in {".yaml", ".yml"}:
        # Keep YAML source text; unlike JSON/TOML, no stdlib parser is assumed.
        return text, "yaml_source_text"
    if suffix == ".py":
        return text, "python_source_text"
    return text, "utf8_text"


def extract_source_text(path: str | Path) -> tuple[str, str]:
    source = Path(path).expanduser().resolve()
    raw = source.read_bytes()
    return _structured_text(source, raw)
