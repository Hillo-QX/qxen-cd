from qxen_cd.audit import (estimate_tokens, load, record_capsule_use,
                           record_processing, record_usage, register_work_item, summarize)


def test_estimate_tokens_is_explicitly_approximate():
    assert estimate_tokens(0) == 0
    assert estimate_tokens(401) == 101


def test_usage_record_is_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    record_usage(path, "task-1", "usage-1", 100, 40, gpt_review_tokens=10)
    assert '"baseline_gpt_tokens": 100' in path.read_text()


def test_pipeline_accounting_and_confirmed_capsule_savings(tmp_path):
    path = tmp_path / "audit.jsonl"
    register_work_item(path, "task-1", "public task", origin="user",
                       baseline_required=True, baseline_mode="direct_gpt")
    record_processing(path, work_item_id="task-1", task="evidence_compression",
                      pipeline="process", baseline_scope="source_plus_evidence",
                      source_chars=4000, qxen_output_chars=1200, capsule_id="EC-1")
    record_processing(path, work_item_id="task-1", task="rolling_context_compact",
                      pipeline="compact", baseline_scope="processed_records",
                      source_chars=1200, qxen_output_chars=800)
    record_usage(path, "task-1", "usage-1", 1000, 300, gpt_review_tokens=100,
                 fallback_replay_gpt_tokens=50, source_chars=4000, payload_chars=1200,
                 pipeline="process", baseline_scope="source_plus_evidence",
                 capsule_id="EC-1")
    record_capsule_use(path, "EC-1", "task-1")
    result = summarize(load(path))
    assert result["token_accounting"]["net_gpt_tokens_saved"] == 550
    assert result["token_accounting"]["actual_used_net_gpt_tokens_saved"] == 550
    assert result["token_accounting"]["chars_avoided"] == 2800
    assert result["pipeline_accounting"]["by_pipeline"]["compact"]["events"] == 1
