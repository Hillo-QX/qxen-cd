# QXEN-CD 正式运行契约

## 角色

QXEN-CD 是 GPT 主 Agent 的证据处理 sub-agent，不是最终裁决器。

## 交给 QXEN-CD 的任务

- 判断材料与当前任务的初步相关性；
- 选择关键证据并保留原文与来源；
- 压缩长材料；
- 给出初步充分性信号；
- 提取时间线和证据关系。
- 提取冲突候选对，不负责冲突裁决；
- 维护滚动上下文的候选胶囊、不可改写证据和待复核队列。

运行时任务名：`capsule`、`relevance_screening`、`key_evidence_selection`、
`evidence_compression`、`source_preservation`、`preliminary_sufficiency`、
`timeline_extraction`、`relation_extraction`、`conflict_candidate_extraction`、
`rolling_context_compact`。

## 只作为建议的任务

`operative_status`、`authority`、`conflicts`、`next_step`、`uncertainty` 必须由 GPT 主 Agent复核。

## 系统强制护栏

- 高风险证据任务出现 JSON 解析失败、截断或缺少关键证据：`FALLBACK`；longtext
  缺少 `key_evidence` 不得回退；
- 高风险证据任务的非法状态、非法相关性/充分性枚举：`FALLBACK`（合法枚举：`operative_status` ∈
  CURRENT/STALE/SUPERSEDED，`relevance` ∈ high/medium/low，`sufficiency` ∈
  sufficient/insufficient；提示词已显式约束模型取值，纯大小写/空白差异自动归一化，
  同义词或语义错位值如 provisional/succeeded 不做映射、直接拦截）；
- 来源只允许与输入材料一致；可证明的空格、Unicode、连字符差异自动规范化；
- 无法证明的来源直接 `FALLBACK`；
- 正常路径只传校验后的 capsule；回退路径传完整 raw 和失败原因；
- longtext 默认 `requires_gpt_review=false`、`review_policy=conditional`；高风险回退路径
  标记 `requires_gpt_review=true`。关键字段不由 QXEN 单独生效。

## 滚动上下文

使用 `scripts/qxen_cd_compact.py` 合并已通过护栏的胶囊。该程序只做确定性去重、
保真保留和字符预算裁剪；`GPT_REVIEW` 的 raw 只能进入 `pending_gpt_review`，
不得直接进入 `accepted_capsules`。

`qxen_cd_process` 和 `qxen_cd_ingest` 已从公开 MCP 删除。生产流程为
`qxen_cd_longtext_distill(source_path=...) → qxen_cd_compact`，不接管 Codex 客户端原生上下文压缩。
`qxen_cd_longtext_distill` 默认只返回可直接消费的最小 `gpt_context_payload`，不内嵌
`compact_state`、完整 preflight、raw model output 或调试记录；这些只能在显式
`include_raw_longtext=true` 时进入 `debug_only`。默认注入口径不是“完整 capsule / 原文”，而是
`Context Burden Ratio = final_gpt_payload_chars / direct_source_chars`。只有
`accepted_capsules > 0` 且该 ratio < 1 时返回 `status=INJECT_QXEN`；否则返回
`status=BYPASS_QXEN`、`guard_status=BYPASS`，不注入 QXEN 胶囊，也不把降级指针记入
`accepted_capsules`。只有需要滚动状态时才显式调用 `qxen_cd_compact`，避免同一摘要重复返回。

可观测 token 口径为：MCP 路径读入字符 − 最终进入 GPT 的 payload 字符 − 后续
`qxen_cd_source_slice` 回源字符。该值由 `observable_path_accounting` 报告；
绕过 MCP 的直接 shell/Read 属于明示盲区，不得默认为未回源。

审计工具：`qxen_cd_audit_register` 登记业务工作项；`qxen_cd_audit_usage`
记录带 usage_id、评估窗口和结果的 baseline/QXEN 成对 token；
`qxen_cd_audit_capsule_use` 记录胶囊实际是否被后续流程引用；`qxen_cd_audit_summary` 汇总节省率、利用率、
fallback 和新增任务。处理事件不计为业务任务，QXEN 新增任务不进入节省率分母。
配对观测少于 50 条时只输出描述性结果，不输出上线增益结论；输入材料字符数与
QXEN 额外 overhead 分开记录。
每条处理事件必须带 `pipeline`：`process`、`ingest`、`compact`、`bootstrap` 或
`audit_assistant`。只有 `process` 的业务配对进入节省率；`ingest`/`compact` 不得重复计入，
`bootstrap` 只进入系统级统计，LocalQwen 的 `audit_assistant` 只进入辅助开销统计。
`source_chars` 的范围必须由 `baseline_scope` 标明；业务 process 默认使用
`source_plus_evidence`。`payload_chars` 只统计实际可能传给 GPT 的 capsule/context，
不包含 MCP 包装和运行时元数据。成功胶囊必须保留 `capsule_id`，后续使用通过
`qxen_cd_audit_capsule_use` 单独登记；没有使用登记时不得宣称“已节省上下文”。

审计语义辅助可通过 QXEN-CD MCP 下放给 LocalQwen：
`qxen_cd_audit_distill`、`qxen_cd_audit_failure_extract`、`qxen_cd_audit_cluster`、
`qxen_cd_audit_classify`。这些工具只产生 advisory 摘要/聚类/保留建议，必须标记
`backend=local-qwen`，不允许修改账本或改变任何统计分母；调用成本计入 audit-only
overhead，不计为业务增益。

节省率公式：

```text
净 GPT 节省 = baseline_gpt - qxen_gpt - gpt_review - fallback_replay_gpt
净节省率 = 净 GPT 节省 / baseline_gpt
```

每个业务工作项默认只取首条有效 usage 配对；重复或后续观测只记录为数据质量项，
不重复累加。业务任务分三层：L1 原本必须完成且有 baseline，L2 系统必要处理，
L3 QXEN-CD 新增/审计任务。只有 L1 进入节省率分母，L2/L3 只报告数量和开销。

## 检测任务

检测任务契约位于 `configs/qxen_detection_tasks_v1.json`，MCP 入口为：

- `qxen_cd_detection_tasks`：读取检测任务规范；
- `qxen_cd_detection_plan`：根据任务类型生成只读计划。

检测结果必须区分 `CANDIDATE`、`CONFIRMED_BY_RULE`、`REJECTED_BY_RULE` 和
`NEEDS_GPT_REVIEW`；QXEN-CD 只负责候选发现和证据整理，确定性代码负责精确检查，
GPT 主 Agent 负责最终解释与行动。
# Long-text distill contract

- MCP entrypoint: `qxen_cd_longtext_distill`
- Local-file entry: pass `source_path` with empty `evidence`; the MCP reads and chunks the source outside GPT context. Structured inputs use deterministic type-aware extraction: `.docx`/`.pptx`/`.xlsx` use OOXML paragraph/slide/table extraction, `.json`/`.jsonl`/`.csv`/`.tsv`/`.toml` use structured parsing or row formatting, `.yaml/.yml` preserve source text, `.pdf` uses page-marked extraction, and plain text uses UTF-8. `.xls` is binary and must use a dedicated spreadsheet reader; it is never decoded as UTF-8. Inline `evidence` remains a compatibility path.
- 2,000–4,000 Chinese characters: safe operating zone.
- 4,000–6,000: allowed upper zone; record `chunk_chars`.
- Over 6,000: deterministic paragraph-aware chunking is mandatory; each chunk is processed independently.
- Under 2,000: prefer deterministic extraction; LocalQwen is not the general long-text backend.
- Long-text output is `ADVISORY`, never a final fact or Gate decision.
- `key_evidence` is optional for long-text/advisory outputs; its absence must not produce `key_evidence_missing_or_invalid` or a hard fallback.
- Guard mode is task-scoped: long-text uses `lightweight_json`; high-risk evidence uses `full_deterministic`.
- Required audit fields: `pipeline=longtext_distill`, `chunk_count`, `chunk_chars`, `authority=advisory_only`, `requires_gpt_review=false`, `review_policy=conditional`, and `context_burden.ratio`.
- Required source contract: default payload keeps `raw_pointer` and `source_locator.sha256`; full `consumption_policy.mode=capsule_first_targeted_retrieval` is contract-level/debug metadata, not repeated in every default GPT payload. Capsules are task-scoped functional summaries, never source-equivalent replacements; exact values/quotes, code edits, conflicts, missing evidence, and high-risk decisions require targeted source retrieval.
- Injection gate: `accepted_capsules > 0` and `final_gpt_payload_chars / direct_source_chars < 1`; otherwise return `BYPASS_QXEN` and do not retry the same capsule as a model failure.
- Targeted source retrieval uses deterministic `qxen_cd_source_slice` with either a line range or query plus optional SHA-256 verification; it returns only a bounded verbatim excerpt and never loads a model.
- `qxen_cd_source_slice` uses the same file-type extraction contract as longtext for all supported structured formats; it must not decode OOXML or legacy `.xls` binaries as UTF-8.
- PDF/table/numeric preflight is local/default-hidden evidence. It may be returned only as compact debug metadata or when the task explicitly asks for table/numeric QA; full coordinate rows remain local evidence only.
- Cross-chunk merge/deduplication/budget trimming belongs to `qxen_cd_compact`.
- Codex response capsules require more than 4,096 UTF-8 bytes before QXEN routing;
  reusable keywords alone never override this minimum. Non-tabular prose must not
  include deterministic preflight metadata in the model evidence body.
- `operative_status` is advisory-only and must not cause a hard fallback; GPT reviews unknown values.
- `qxen_cd_process` and `qxen_cd_ingest` are removed from the public MCP surface.
