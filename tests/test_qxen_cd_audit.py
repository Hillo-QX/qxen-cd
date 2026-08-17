#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from qxen_cd_audit import (load, record_capsule_use, record_path_distill,
                           record_processing, record_source_retrieval, record_usage,
                           register_work_item, summarize, summarize_local_qwen)


def main() -> int:
    path = ROOT / "测试" / "临时工作区" / "qxen_cd_audit_smoke.jsonl"
    path.unlink(missing_ok=True)
    register_work_item("W1", "用户原始任务", origin="user", baseline_required=True,
                       baseline_mode="direct_gpt", path=path)
    record_processing(work_item_id="W1", task="evidence_compression", source_chars=4000,
                      capsule_id="C1", pipeline="process", baseline_scope="source_plus_evidence",
                      path=path)
    record_processing(work_item_id="W1", task="rolling_context_compact", source_chars=1000,
                      pipeline="compact", baseline_scope="processed_records",
                      path=path)
    record_usage("W1", "U1", baseline_mode="direct_gpt", eval_window="week-1",
                 outcome="success", baseline_gpt_tokens=1000, qxen_gpt_tokens=300,
                 qxen_local_tokens=900, gpt_review_tokens=100,
                 fallback_replay_gpt_tokens=50, source_chars=4000, payload_chars=1200,
                 pipeline="process", baseline_scope="source_plus_evidence", capsule_id="C1",
                 path=path)
    record_capsule_use("C1", "W1", used_by="gpt", outcome="success", path=path)
    record_path_distill("/tmp/source.txt", "hash-1", source_chars=4000,
                        returned_chars=1000, work_item_id="W1", capsule_id="C1", path=path)
    record_source_retrieval("/tmp/source.txt", "hash-1", returned_chars=200,
                            work_item_id="W1", capsule_id="C1", path=path)
    record_usage("W1", "U1", baseline_mode="direct_gpt", eval_window="week-1",
                 outcome="success", baseline_gpt_tokens=1000, qxen_gpt_tokens=300, path=path)
    register_work_item("W2", "QXEN新增的审计任务", origin="qxen_cd_generated",
                       baseline_required=False, baseline_mode="none", path=path)
    record_processing(work_item_id="W2", task="audit_only", source_chars=500,
                      pipeline="audit_assistant", baseline_scope="audit_log", path=path)
    result = summarize(load(path))
    assert result["business_work_items"] == 2
    assert result["qxen_processing_events"] == 3
    assert result["business_work_items_by_category"]["baseline_required"] == 1
    assert result["business_work_items_by_category"]["qxen_added"] == 1
    assert result["token_accounting"]["gross_gpt_tokens_saved"] == 700
    assert result["token_accounting"]["net_gpt_tokens_saved"] == 550
    assert result["token_accounting"]["saving_rate"] == 0.55
    assert result["token_accounting"]["raw_source_chars"] == 4000
    assert result["token_accounting"]["qxen_payload_chars"] == 1200
    assert result["token_accounting"]["payload_chars_observations"] == 1
    assert result["token_accounting"]["confirmed_capsule_use_pairs"] == 1
    assert result["token_accounting"]["actual_used_net_gpt_tokens_saved"] == 550
    assert result["pipeline_accounting"]["by_pipeline"]["process"]["events"] == 1
    assert result["pipeline_accounting"]["by_pipeline"]["compact"]["events"] == 1
    assert result["pipeline_accounting"]["by_pipeline"]["audit_assistant"]["events"] == 1
    assert result["data_quality"]["duplicate_usage_rows_ignored"] == 1
    observable = result["observable_path_accounting"]
    assert observable["path_distill_calls"] == 1
    assert observable["reread_events"] == 1
    assert observable["net_avoided_chars"] == 2800
    assert observable["net_avoided_tokens_est"] == 700
    local = summarize_local_qwen([
        {"status": "OK", "tool": "local_extract_failure", "input_mode": "local_path",
         "input_chars": 1000, "output_chars": 200},
    ])
    assert local["observable_path_accounting"]["net_avoided_chars_before_reread"] == 800
    assert result["report_status"] == "descriptive_only_need_50_pairs"
    print("QXEN-CD audit smoke: PASS")
    path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
