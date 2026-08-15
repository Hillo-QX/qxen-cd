from qxen_cd.audit import estimate_tokens, record_usage


def test_estimate_tokens_is_explicitly_approximate():
    assert estimate_tokens(0) == 0
    assert estimate_tokens(401) == 101


def test_usage_record_is_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    record_usage(path, "task-1", "usage-1", 100, 40, gpt_review_tokens=10)
    assert '"baseline_gpt_tokens": 100' in path.read_text()
