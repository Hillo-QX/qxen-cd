import json

from qxen_cd import compact, guard_v1


PROMPT = "来源：report/a.txt\n来源：report/b.txt"


def capsule(source="report/a.txt"):
    return json.dumps({
        "capsule_id": "EC-001",
        "source_type": "report",
        "relevance": "high",
        "key_evidence": [{"text": "不可改写日期：2026-08-16", "source": source,
                           "preserve_verbatim": True}],
        "sufficiency": "sufficient",
        "timeline": ["事件：2026-08-16"],
    }, ensure_ascii=False)


def test_accepts_and_canonicalizes_source_whitespace():
    result = guard_v1(capsule(" report/a.txt "), PROMPT)
    assert result["guard_status"] == "ACCEPT"
    assert result["capsule"]["key_evidence"][0]["source"] == "report/a.txt"
    assert result["source_canonicalized"] == 1


def test_accepts_page_citation_suffix_without_widening_source():
    result = guard_v1(capsule("report/a.txt（61 页）：第 1-10 页"), PROMPT)
    assert result["guard_status"] == "ACCEPT"
    assert result["capsule"]["key_evidence"][0]["source"] == "report/a.txt"
    assert result["source_match"] == "page_suffix_canonicalized"


def test_similar_source_is_review_only():
    result = guard_v1(capsule("report/a-final.txt"), PROMPT)
    assert result["guard_status"] == "FALLBACK"
    assert result["fallback_reason"] == "source_similarity_candidate"
    assert result["source_match"] == "similarity_candidate"


def test_missing_source_manifest_falls_back_before_acceptance():
    result = guard_v1(capsule(), "证据材料 BEGIN\n证据材料 END")
    assert result["guard_status"] == "FALLBACK"
    assert result["fallback_reason"] == "evidence_material_missing"


def test_illegal_status_falls_back_with_raw_preserved():
    raw = capsule().replace('"sufficient"', '"sufficient"').replace(
        '"source_type": "report"', '"source_type": "report", "operative_status": "planned"')
    result = guard_v1(raw, PROMPT)
    assert result["guard_status"] == "FALLBACK"
    assert result["fallback_reason"].startswith("illegal_operative_status")
    assert result["gpt_context"]["raw_model_output"] == raw


def test_parse_failure_falls_back():
    result = guard_v1('{"capsule_id":"EC-001"', PROMPT)
    assert result["guard_status"] == "FALLBACK"
    assert result["fallback_reason"] == "parse_error_or_truncation"


def test_compact_deduplicates_and_isolates_fallback():
    accepted = guard_v1(capsule(), PROMPT)
    fallback = guard_v1("not json", PROMPT)
    state = compact([accepted, accepted, fallback], max_items=10, max_chars=10000)
    assert len(state["accepted_capsules"]) == 1
    assert len(state["pending_gpt_review"]) == 1
    assert state["dropped_summary"]["duplicate_capsules"] == 1
    assert len(state["verbatim_evidence"]) == 1


def test_compact_longtext_fallback_keeps_pointer_capsule():
    state = compact([{
        "guard_status": "FALLBACK",
        "task": "qxen_longtext_distill",
        "source": "report.pdf#chunk01",
        "fallback_reason": "parse_error",
    }], max_chars=10000)
    assert len(state["accepted_capsules"]) == 1
    assert state["pending_gpt_review"] == []
    assert state["accepted_capsules"][0]["raw_pointer"] == "report.pdf#chunk01"
