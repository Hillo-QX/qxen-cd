#!/usr/bin/env python3
"""Path-first regression tests for QXEN audit wrappers."""

import asyncio

import qxen_cd_mcp as qxen


def test_audit_text_input_reads_local_path(tmp_path):
    source = tmp_path / "audit.log"
    source.write_text("本地日志内容", encoding="utf-8")

    text, meta = qxen._audit_text_input("", str(source))

    assert text == "本地日志内容"
    assert meta["input_mode"] == "local_path"
    assert meta["source_path"] == str(source.resolve())
    assert meta["source_sha256"]


def test_audit_distill_accepts_advisory_without_local_fallback(tmp_path, monkeypatch):
    source = tmp_path / "audit.log"
    source.write_text("需要蒸馏的审计日志", encoding="utf-8")
    captured = {}

    async def fake_longtext(**kwargs):
        captured.update(kwargs)
        return {"guard_status": "ADVISORY", "summary": "短胶囊"}

    async def forbidden_local(*args, **kwargs):
        raise AssertionError("ADVISORY must not fall back to LocalQwen")

    monkeypatch.setattr(qxen, "qxen_cd_longtext_distill", fake_longtext)
    monkeypatch.setattr(qxen.local_qwen, "local_distill", forbidden_local)
    monkeypatch.setattr(qxen, "record_processing", lambda **kwargs: None)

    result = asyncio.run(qxen.qxen_cd_audit_distill(log_path=str(source)))

    assert result["status"] == "OK"
    assert result["backend"] == "qxen-cd"
    assert result["input_mode"] == "local_path"
    assert captured["source_path"] == str(source)
    assert captured["evidence"] == ""
