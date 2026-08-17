#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small deterministic contract tests for qxen_v1_guard."""
from qxen_v1_guard import guard_v1

PROMPT = "来源：/Users/test/报告 2026.xlsx\n证据材料 END"
GOOD = '{"relevance":"high","key_evidence":[{"text":"x","source":"/Users/test/报告 2026.xlsx"}],"operative_status":"CURRENT"}'


def check(condition, name):
    if not condition:
        raise AssertionError(name)


def main():
    accepted = guard_v1(GOOD, PROMPT)
    check(accepted["guard_status"] == "ACCEPT", "valid capsule accepted")
    check(accepted["gpt_context"]["context_mode"] == "CAPSULE", "capsule sent to GPT")
    check("raw_model_output" not in accepted["gpt_context"], "accepted path saves tokens")

    illegal = GOOD.replace("CURRENT", "suspended")
    rejected = guard_v1(illegal, PROMPT)
    check(rejected["guard_status"] == "ACCEPT", "advisory status does not block capsule")

    for alias in ("provisional", "succeeded"):
        repaired = guard_v1(GOOD.replace("CURRENT", alias), PROMPT)
        check(repaired["guard_status"] == "ACCEPT", alias + " alias repaired")
        check(repaired["capsule"]["operative_status"] == "CURRENT", alias + " maps to CURRENT")
        check(repaired["capsule"]["guard_repairs"][0]["kind"] == "operative_status_alias", alias + " repair recorded")

    unknown_status = GOOD.replace("CURRENT", "tentative")
    check(guard_v1(unknown_status, PROMPT)["guard_status"] == "ACCEPT", "unknown advisory status accepted")
    check(rejected["gpt_context"]["capsule"]["operative_status"] == "SUSPENDED", "unknown status preserved for GPT review")

    spacing = GOOD.replace("/报告 2026.xlsx", "/报告2026.xlsx")
    fixed = guard_v1(spacing, PROMPT)
    check(fixed["guard_status"] == "ACCEPT", "harmless path spacing accepted")
    check(fixed["source_canonicalized"] == 1, "source canonicalized")

    page_citation = GOOD.replace(
        "/Users/test/报告 2026.xlsx",
        "/Users/test/报告 2026.xlsx（61 页）：第 1-10 页",
    )
    page_fixed = guard_v1(page_citation, PROMPT)
    check(page_fixed["guard_status"] == "ACCEPT", "page citation accepted")
    check(page_fixed["source_match"] == "page_suffix_canonicalized",
          "page citation canonicalized")

    unknown = GOOD.replace("/Users/test/报告 2026.xlsx", "/Users/other/unknown.xlsx")
    check(guard_v1(unknown, PROMPT)["guard_status"] == "FALLBACK", "unknown source rejected")
    similar = GOOD.replace("/Users/test/报告 2026.xlsx", "/Users/test/报告 2026-final.xlsx")
    similar_result = guard_v1(similar, PROMPT)
    check(similar_result["guard_status"] == "FALLBACK", "similar source not accepted")
    check(similar_result["fallback_reason"] == "source_similarity_candidate",
          "similar source routed to GPT review")
    check(similar_result["source_match"] == "similarity_candidate",
          "similarity is review-only")
    check(guard_v1('{"relevance":"high"}', PROMPT)["guard_status"] == "ACCEPT", "missing evidence is advisory-only")
    check(guard_v1('{"relevance":"high"', PROMPT)["guard_status"] == "FALLBACK", "truncation rejected")
    check(guard_v1(GOOD, "证据材料 BEGIN\n证据材料 END")["fallback_reason"] == "evidence_material_missing", "missing source manifest rejected")
    print("qxen_v1_guard: PASS")


if __name__ == "__main__":
    main()
