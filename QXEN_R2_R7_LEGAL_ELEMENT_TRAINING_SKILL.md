---
name: qxen-r2-r7-legal-element-training
title: QXEN R2–R7 Rule–Element–Evidence Training Skill
version: 2.0
status: ACTIVE-REVISED-2026-08-14
project_root: /Users/hillo/Desktop/任务调度器
base_model: qwen3.5-9b-mlx-4bit
owner: QXEN
---

# QXEN R2–R7 Rule–Element–Evidence Training Skill

## 0. REVISION 2026-08-14 — Unified Route（本章节覆盖以下全部旧章节）

### 0.1 裁决来源
Kimi-Expert 复核（2026-08-14）+ R3A 合成主线冻结报告（reports/r3/r3a_synthetic_freeze.md）+ 用户授权架构重构（QXEN-CD 转 Evidence Capsule sub-agent）。

### 0.2 核心裁决

> **合并为一条主路线：A 是架构栈（数据如何进模型），B 是能力契约（Gate 考什么）。**
> 放弃「每阶段独立 adapter + 3000 条」范式，改走 MVP 小闭环。

依据：
- R3A 已证明**合成大数据 LoRA 对细粒度时序判别（STALE）负迁移**（0-shot 35% → LoRA 15.6%），合成路线失效。
- 20 条真实池离 3000/阶段差两个数量级，继续按旧 B 数据门槛执行必然造假数据。
- ec_v1 的 19/20 通过率是 40 iters 对 20 条的**过拟合假象**，不作能力证据；但 pool 与契约本身是有效资产。

### 0.3 统一路线（可执行）

```
架构栈 A（CD 实现顺序）        能力契约 B（Gate 维度）
──────────────────────        ──────────────────────
1. 证据胶囊生成（R3/R2 子集）   operative_status / authority /
                               evidence-element relation
2. 状态更新（R6 子集）          state patch（ADD/SUPERSEDE/REOPEN…）
3. 最小 agent 闭环              capsule → state → action
R4 / R5 / R7 ──► 规则兜底，不训练（schema 校验、superseded 链校验、冲突校验全部确定性代码）
```

### 0.4 下一阶段硬性要求（覆盖旧 §5.1 / §8 / §9 / §14）

1. **只训练一个 adapter**：`evidence_capsule + state_update` 联合 LoRA（不拆 R3A/R3B/R3C/R2/R6 多 adapter）。
2. **数据**：pool 20 条扩到 **100–200 条**（真实锚点优先，人工标注补充；**禁用 R3A 式纯合成时序**）；split 后 fresh test ≥ 20 条。数据扩充前取得用户授权。
3. **强验证器兜底**：契约 JSON schema 校验、SUPERSEDED 链校验、冲突对校验全部用确定性代码；模型只负责生成，规则负责拒绝。Gate 指标 = 模型正确率 + 规则拦截后端到端正确率。
4. **ec_v1 处置**：保留 pool 20、契约 evidence_capsule_v1、schema、评估脚本；`models/ec_v1` 标记为 baseline 快照归档，**不作种子、不 resume**。
5. **R3B/R3C 不独立 adapter**：并入统一胶囊契约作 schema 扩展字段，Gate 仍按 B 维度分别打分。
6. **UNCERTAIN 处理**：100–200 条是否足够过 Gate 无先验，先跑 100 条档验证再决定是否扩量；若 Gate 仍 FAIL，按 R3A 教训优先查数据真实性而非加量。

### 0.5 以下旧章节保留为参考基线，仅在被 0.3/0.4 覆盖处失效
- §5.1（独立 adapter 拓扑）→ 被 0.4-1 覆盖
- §8/§9/§10/§11/§12/§13（每阶段 2400–3200 条独立数据集）→ 被 0.4-2 覆盖
- §14（端到端 8 阶段顺序训练）→ 被 0.3 覆盖
- §15–§24 仍适用（Gate 维度、fresh-test 纪律、模型版本库、失败升级纪律等）



This skill governs QXEN R2–R7 architecture, dataset construction, LoRA training, validation, Shadow/Canary deployment, failure repair, and versioning after R1 Materiality is frozen.

Canonical method:

> **Rule–Element–Evidence Grounded Hierarchical Context Control**

Canonical transformation:

\[
TASK
ightarrow RULE
ightarrow MATERIAL\ ELEMENTS
ightarrow EVIDENCE
ightarrow AUTHORITY
ightarrow SUFFICIENCY
ightarrow FINDINGS
ightarrow ACTION
\]

Highest principle:

\[
oxed{Reliability > Compression}
\]

Second principle:

> **LoRA trains decisions, not labor.**

Do not use LoRA merely for file summaries, grep organization, log compression, JSON formatting, schema checks, diff narration, or other deterministic/mechanical work that base Qwen + narrow prompts + verifier can already perform.


# 1. Activation Gate

This skill is downstream of R1. Do not begin R2–R7 merely because this file exists.

Required prerequisites:

```yaml
R0_RULE_ELEMENT_COMPILER:
  status: AVAILABLE
  implementation: deterministic_first

R1_MATERIALITY:
  adapter_status: FROZEN
  inference_policy_status: FROZEN
  threshold_status: FROZEN
  shadow_status: PASS
  canary_status: PASS_OR_EXPLICITLY_WAIVED
```

If any prerequisite is missing:

```text
STOP
→ continue R1 stabilization
→ do not start R2/R3 training
```

R0 remains deterministic-first. If the Rule itself is ambiguous, novel, conflicting, or architecture-defining, escalate to DS/K3 instead of teaching Qwen to invent the Rule.


# 2. Legal / Evidentiary Mapping

| Legal reasoning | QXEN |
|---|---|
| Issue | TASK |
| Rule | RULE_SPEC |
| Elements | MATERIAL ELEMENT MAP |
| Evidence | CANDIDATES |
| Relevance / materiality | R1 |
| Authority / admissibility / operative force | R3 |
| Evidentiary record | R2 |
| Preservation / exhibit status | R4 |
| Burden / standard of proof | R5 |
| Findings of fact | R6 |
| Jurisdiction / appeal | R7 |

Core proposition:

> A Candidate is not inherently REL or IRREL. Its materiality is conditional on Task, Rule, and Element.

\[
Materiality=f(Task,Rule,Element,Candidate)
\]

Critical distinctions:

\[
Evidence 
eq Finding 
eq Rule
\]

\[
Latest 
eq Authoritative
\]


# 3. Canonical Architecture

Logical numbering remains R2–R7, but the recommended **training and execution order** is:

```text
R0 Rule / Element Compiler
        ↓
R1 Materiality Gate
        ↓
R3 Authority + Operativeness
        ↓
R2 Evidence-to-Element Selection
        ↓
R4 Role / Retention / Fidelity
        ↓
R5 Element Sufficiency
        ↓
R6 Finding / State Update
        ↓
R7 Intelligence Jurisdiction
```

## Why R3 precedes R2

Do not first select evidence and only later discover that it is stale, superseded, archived, low-authority, or unusable.

Correct:

\[
Materiality ightarrow Authority ightarrow Selection
\]

Not:

\[
Materiality ightarrow Selection ightarrow Authority
\]


# 4. Common Data Objects

## 4.1 RULE_SPEC

```yaml
RULE_SPEC:
  issue: 修复 MCP 注册失败
  elements:
    - id: E1
      rule: MCP server 必须实际启动
      type: THRESHOLD
    - id: E2
      rule: active config 必须指向正确 server
      type: MANDATORY
    - id: E3
      rule: tool schema 必须成功注册
      type: MANDATORY
  prohibitions:
    - 不得破坏现有 Dispatcher
  exceptions:
    - id: X1
      rule: runtime verifier 已证明当前状态时，旧日志不得推翻当前 finding
  stop_conditions:
    - threshold element E1 明确失败且 bounded repair 已耗尽
  authority_policy:
    - T0: runtime verifier / actual executed result
    - T1: active schema / active config / executed source
    - T2: current project specification
    - T3: human or agent summary
    - T4: historical log / archived note
```

Element types:

```text
THRESHOLD
MANDATORY
ALTERNATIVE
EXCEPTION
PROHIBITION
```

## 4.2 EVIDENCE_ITEM

```json
{
  "candidate_id": "C17",
  "related_elements": ["E2", "E3"],
  "relation": "SUPPORT",
  "source_type": "active_config",
  "operative_status": "CURRENT",
  "authority": "T1",
  "materiality": "CRITICAL",
  "token_cost": 184
}
```

Evidence relations:

```text
SUPPORT
CONTRADICT
CONSTRAINT
PROCEDURAL
NONE
```

Materiality levels:

```text
DISPOSITIVE
CRITICAL
MATERIAL
SUPPORTIVE
```

## 4.3 ELEMENT_LEDGER

```yaml
ELEMENT_LEDGER:
  E1:
    status: SATISFIED
    support: [C01]
    contradict: []
  E2:
    status: CONFLICTED
    support: [C04]
    contradict: [C09]
  E3:
    status: UNRESOLVED
    support: []
    contradict: []
```

Statuses:

```text
SATISFIED
NOT_SATISFIED
UNRESOLVED
CONFLICTED
```

## 4.4 WORKING_STATE

```yaml
RULES:
  - R01:
      text: frozen test must never enter training
      status: ACTIVE
FINDINGS:
  - F01:
      claim: selected adapter is current
      status: VERIFIED
      evidence: [C27, C31]
OPEN_ISSUES:
  - E04:
      status: UNRESOLVED
EVIDENCE:
  - C27:
      source: gate_report.json
      authority: T0
      operative_status: CURRENT
```

## 4.5 DECISION_PACKET

```yaml
DECISION_PACKET:
  task: ...
  rule_status: SETTLED | AMBIGUOUS | CONFLICTED
  unresolved_elements: [E3]
  material_conflicts: [E2]
  verifier_failures: 2
  impact: LOW | MEDIUM | HIGH
  reversible: true
  local_confidence: 0.71
  state_digest: ...
```

R7 and DS/K3 receive compact packets, not raw history.


# 5. Training Topology

## 5.1 Separate adapters by stage

Do **not** recreate the failed flat multi-label design.

```text
Qwen3.5-9B Base
├── R1 adapter: Materiality
├── R3 adapter: Authority / Operativeness
├── R2 adapter: Evidence Selection
├── R4 adapter: Retention / Fidelity
├── R5 adapter: Sufficiency
├── R6 adapter: State Update
└── R7 adapter: Intelligence Jurisdiction
```

Default rule:

> **Do not sequentially continue-fine-tune R3 → R2 → R4 → R5 → R6 → R7 into one adapter.**

Each stage consumes structured outputs from prior stages.

Reasons:

- reduces objective interference;
- allows independent rollback;
- allows stage-specific thresholds;
- preserves auditability;
- makes ablation possible;
- prevents repair in one stage from damaging another.

A continued-finetune transfer experiment is allowed only after an independent-adapter baseline exists.

## 5.2 Deterministic scaffolding first

Before training each stage, create:

1. input schema;
2. output schema;
3. schema validator;
4. deterministic verifier;
5. evaluation script;
6. freeze/hash mechanism;
7. leakage/duplicate audit;
8. stage-specific failure report.

No training before these exist.


# 6. Global Training Discipline

## 6.1 Split policy

Split by task/query group, never individual row.

Recommended:

```text
72% train
10% valid
18% fresh untouched test
```

All Candidates from the same Task / Rule / Element family stay in one split.

Prohibited:

- exact prompt overlap;
- candidate-content overlap;
- near-duplicate cross split;
- same synthetic template with trivial label substitutions;
- frozen-test reuse in later training;
- using fresh test to choose threshold/checkpoint.

## 6.2 Lexical Shortcut Audit

Every dataset must check for label-predictive artifacts such as:

```text
当前目标直接对应
必要依赖
明显相关
归档副本
无调用关系
旧版本
仅历史
REL
IRREL
```

If a weak lexical baseline can classify suspiciously well from Candidate surface wording alone:

```text
DATASET FAIL
→ redesign contrast pairs
```

## 6.3 Twin-case requirement

Every stage must include same-surface / different-rule pairs.

Example:

```text
Candidate: test_fresh.jsonl

Task A:
构建 training replay
Rule:
frozen evaluation data cannot enter training
→ prohibited / not admissible for this action

Task B:
执行 final evaluation
Rule:
designated frozen test must be used
→ required
```

Goal:

\[
Decision 
eq f(CandidateWords)
\]

\[
Decision=f(Task,Rule,Element,Candidate,Authority)
\]

## 6.4 Counter-evidence requirement

Every material hypothesis family must include:

```text
SUPPORT
CONTRADICT
EXCEPTION
HIGHER_AUTHORITY_OVERRIDE
```

If a material conflict exists:

\[
MaterialConflict(E_i)\Rightarrow SupportEvidence+CounterEvidence
\]

## 6.5 One experiment = one major intervention

Do not simultaneously change dataset distribution, rank, learning rate, prompt structure, threshold, and epoch depth.


# 7. Common Hyperparameter Policy

Initial default for Qwen3.5-9B MLX LoRA:

```yaml
rank: 8
learning_rate: 3e-6_to_5e-6
epochs_first_run: 1
max_epochs_without_new_evidence: 2
checkpoint_frequency: 10_to_15_percent_of_epoch
```

Sequence length:

```text
R3 / R4 / R7:
  start around 512–768

R2 / R5 / R6:
  start around 768–1024
```

Do not raise rank merely because valid accuracy is imperfect.

Raise rank only if:

1. train and valid both plateau;
2. errors occur across several subtypes;
3. data QA is clean;
4. schema is stable;
5. targeted repair failed;
6. evidence supports under-capacity.

Preferred repair order:

```text
data / contrast design
→ sampling
→ threshold / policy calibration
→ targeted replay
→ checkpoint choice
→ only then consider rank
```

24GB Mac memory guard:

```text
wired memory > 18GB
OR
free memory < 500MB
→ terminate safely
```

Always record exit code, peak memory, WARN count, training log, adapter hash, config hash, and dataset hash.


# 8. R3 — Authority + Operativeness

## Objective

R3 asks:

> “This Candidate may be material, but does it currently have authority to influence the decision?”

### R3A Operative Status

```text
CURRENT
STALE
SUPERSEDED
```

### R3B Authority

```text
T0 runtime verifier / executed truth
T1 active config / active schema / executed source
T2 current project specification
T3 summary / agent report
T4 historical note / archived log
```

If two high-authority sources materially conflict:

```text
PRESERVE CONFLICT
→ do not overwrite
→ R5 = CONFLICTED
→ R7 may escalate
```

## Input

```text
TASK
+ RULE_SPEC
+ MATERIAL ELEMENT MAP
+ CANDIDATE
+ SOURCE METADATA
+ OPTIONAL COMPETING SOURCE DIGEST
```

## Output

```json
{
  "candidate_id": "C17",
  "operative_status": "CURRENT",
  "authority": "T1",
  "material_conflict": false,
  "reason_code": "ACTIVE_CONFIG"
}
```

## Initial dataset

```text
Total: 3000
Train: 2160
Valid: 300
Fresh: 540
First run: ~1 effective epoch
```

Why ~3000: authority/status needs archived-vs-active, runtime-vs-summary, latest-vs-authoritative, and conflict pairs. 500 is too shallow; >5000 is unjustified before clean failure evidence.

Required families:

```text
active config vs archived config
runtime result vs historical log
current schema vs deprecated schema
executed code vs README statement
project spec vs agent summary
new low-authority note vs older high-authority verifier
two T0/T1 sources in material conflict
superseded evidence with similar wording
```

## Gate

```text
Operative Status Accuracy        >= 0.90
Authority Ranking Accuracy       >= 0.90
Superseded Rejection             >= 0.95
Material Conflict Recall         >= 0.95
Wrong-Authority Preference Rate  <= 0.03
Critical T0/T1 Miss              ~= 0
Invalid Output                   = 0
```

## Failure path

If model prefers newer over authoritative:

```text
add latest-vs-authoritative contrasts
do not add epochs first
```

If status is good but authority collapses:

```text
split R3A and R3B adapters
```


# 9. R2 — Evidence-to-Element Selection

## Objective

R2 asks:

> “Given authoritative material candidates, what is the smallest evidence set that covers every material element without deleting material counter-evidence?”

\[
\min_{S\subseteq C} Tokens(S)+\lambda Redundancy(S)
\]

subject to:

\[
Coverage(E_i,S)\ge	au_i
\]

for every material element, plus counter-evidence coverage.

## Recommended decomposition

### R2A Candidate-to-Element Relation

```text
SUPPORT
CONTRADICT
CONSTRAINT
PROCEDURAL
NONE
```

### R2B Minimal Set Selection

Use R2A + R3 annotations to choose the smallest sufficient set.

If R2B fails, check R2A before increasing rank.

## Input

```text
TASK
+ RULE_SPEC
+ MATERIAL ELEMENTS
+ 6–12 R1-REL candidates
+ R3 authority/status
+ token cost
```

## Output

```json
{
  "selected": ["C02", "C05", "C09"],
  "coverage": {
    "E1": ["C02"],
    "E2": ["C05", "C09"],
    "E3": ["C05"]
  },
  "counter_evidence": ["C09"]
}
```

## Initial dataset

```text
Total set-level tasks: 3000
Train: 2160
Valid: 300
Fresh: 540
Candidates/task: 6–12
Elements/task: 2–6
First run: 1 effective epoch
```

Why: set selection is combinatorial and must learn redundancy, contrary evidence, authority overrides, and dispositive elements.

## Gate

```text
Element Coverage Recall          >= 0.95
Dispositive Element Recall       >= 0.97
Counter-Evidence Recall          >= 0.95
Critical Candidate Recall        >= 0.95
Precision@Selected               >= 0.80
Context Reduction Ratio          >= 0.25 initially
Downstream Task Success Delta    >= -0.02
Invalid Output                   = 0
```

Reliability dominates reduction.

## Failure path

Everything selected:

```text
add matched redundant candidates
train minimal-set contrast pairs
```

Critical misses:

```text
Presumption of Retention
increase dispositive/critical replay
raise burden for exclusion
```


# 10. R4 — Role / Retention / Fidelity

## Objective

R4 decides what selected context is, how long it lives, and whether it may be compressed.

Role:

```text
RULE
FINDING
EVIDENCE
```

Persistence:

```text
PIN
SESSION
EPHEMERAL
```

Fidelity:

```text
VERBATIM
COMPRESSIBLE
```

Typical lifecycle:

```text
RULE → often PIN
VERIFIED FINDING → SESSION or PIN
RAW EVIDENCE → often EPHEMERAL after provenance is preserved
```

## Input

```text
TASK
+ RULE_SPEC
+ SELECTED ITEM
+ SOURCE TYPE
+ AUTHORITY
+ CURRENT STATE ROLE
```

## Output

```json
{
  "candidate_id": "C05",
  "role": "EVIDENCE",
  "persistence": "EPHEMERAL",
  "fidelity": "VERBATIM"
}
```

## Initial dataset

```text
Total: 2400
Train: 1728
Valid: 240
Fresh: 432
First run: 1 effective epoch
```

## Gate

```text
Role Accuracy                         >= 0.95
Critical Rule PIN Recall              >= 0.98
VERBATIM Requirement Recall           >= 0.98
Wrongful Critical Compression         = 0
Ephemeral Evidence Classification     >= 0.90
Invalid Output                        = 0
```

## Failure path

If one axis improves while another collapses:

```text
objective interference
→ split Role from Retention/Fidelity
```

Never return to flat 7-way labels.


# 11. R5 — Element Sufficiency / Burden of Proof

## Objective

R5 asks:

> “Has each material element been proven enough to act, disproven enough to stop, or is more evidence required?”

Per-element:

```text
SATISFIED
NOT_SATISFIED
UNRESOLVED
CONFLICTED
```

Global:

```text
ENOUGH_TO_ACT
ENOUGH_TO_STOP
NEED_MORE
```

## Two-phase adjudication

Phase A — Prima Facie:

```text
Do current authoritative materials support the elements?
```

Phase B — Challenge:

```text
Any material exception?
Contrary evidence?
Higher-authority conflict?
Unresolved mandatory element?
Failed prohibition?
```

Only if both pass:

```text
ENOUGH_TO_ACT
```

## Asymmetric burdens

\[
Burden(DROP)>Burden(KEEP)
\]

\[
Burden(REPLACE)>Burden(ADD)
\]

\[
Burden(ACT)>Burden(RETRIEVE)
\]

False `ENOUGH_TO_ACT` is severe.

## Dispositive short-circuit

If a THRESHOLD element fails:

```text
ENOUGH_TO_STOP
```

may be correct without investigating later elements.

## Input

```text
RULE_SPEC
+ ELEMENT_LEDGER
+ SELECTED EVIDENCE
+ AUTHORITY/STATUS
+ EXCEPTIONS
+ PROHIBITIONS
```

## Output

```json
{
  "elements": {
    "E1": "SATISFIED",
    "E2": "CONFLICTED",
    "E3": "UNRESOLVED"
  },
  "global": "NEED_MORE",
  "missing": ["E3"],
  "conflicts": ["E2"]
}
```

## Initial dataset

```text
Total: 3200
Train: 2304
Valid: 320
Fresh: 576
First run: 1 effective epoch
```

Why larger: four element states, three global states, threshold elements, exceptions, conflict patterns, and asymmetric error costs.

## Gate

```text
Element Status Macro F1               >= 0.90
Material Element Miss                 <= 0.03
Exception Miss                        <= 0.02
Dispositive Short-Circuit Accuracy    >= 0.95
False ENOUGH_TO_ACT                   <= 0.01 overall
False ENOUGH_TO_ACT high-impact       = 0
Invalid Output                        = 0
```

## Failure path

If False `ENOUGH_TO_ACT` appears:

```text
STOP promotion
→ mine counter-evidence / exception cases
→ strengthen Challenge phase
→ add deterministic safety guard if needed
```

Do not make everything `NEED_MORE`.


# 12. R6 — Finding / State Update

## Objective

New evidence must never directly overwrite Working State.

Correct path:

```text
New Evidence
→ R3 Authority Check
→ R5 Element Evaluation
→ Finding Update
→ State Patch
```

Output is a patch, never a full state rewrite.

Allowed patch operations:

```text
ADD_EVIDENCE
SUPERSEDE_EVIDENCE
UPDATE_ELEMENT
ADD_FINDING
REOPEN_FINDING
PRESERVE_RULE
RESOLVE_CONFLICT
DEDUP
```

`REOPEN_FINDING` is mandatory. Stronger contrary evidence must reopen a previously VERIFIED finding rather than silently overwrite it.

## Input

```text
CURRENT WORKING_STATE
+ NEW VERIFIED EVIDENCE
+ R3 AUTHORITY/STATUS
+ R5 ELEMENT RESULT
```

## Output

```json
{
  "add_evidence": ["C41"],
  "supersede_evidence": ["C12"],
  "update_element": [
    {"element_id": "E2", "status": "CONFLICTED"}
  ],
  "add_finding": [],
  "reopen_finding": ["F03"],
  "preserve_rule": ["R01"],
  "resolve_conflict": [],
  "dedup": ["C19"]
}
```

A deterministic State Manager validates and applies the patch.

## Initial dataset

```text
Total: 3200
Train: 2304
Valid: 320
Fresh: 576
First run: 1 effective epoch
```

Required families:

```text
new supporting evidence
higher-authority contrary evidence
stale evidence supersession
duplicate evidence
rule preservation
finding reopen
conflict creation
conflict resolution
wrong-source attempted overwrite
```

## Gate

```text
Patch Schema Validity                 = 1.00
Critical Rule Preservation            = 1.00
Provenance Retention                  = 1.00
Wrongful Finding Overwrite            <= 0.01
Correct REOPEN Recall                 >= 0.95
Stale Evidence Removal                >= 0.90
Verified Finding Preservation         >= 0.98
```

## Failure path

If model hallucinates full-state rewrites:

```text
reduce model authority
→ predict patch ops only
→ deterministic State Manager applies
```

Any critical rule deletion:

```text
FAIL
```


# 13. R7 — Intelligence Jurisdiction

## Objective

R7 decides who has jurisdiction to reason next.

Routes:

```text
LOCAL
DS
K3
ABSTAIN
```

R7 asks:

> “Given Rule status, evidence state, unresolved elements, conflict, risk, and verifier history, what is the minimum sufficient intelligence tier authorized to continue?”

## Route definitions

### LOCAL

```text
Rule settled
Elements explicit
Evidence sufficient
Low-risk bounded task
Verifier deterministic
No material authority conflict
```

### DS

```text
Rule mostly settled
Application needs multi-candidate reasoning
Ordinary factual ambiguity
Non-architectural replanning
```

### K3

```text
RULE_AMBIGUITY
MATERIAL_CONFLICT
AUTHORITY_CONFLICT
architecture change
elements require redefinition
repeated verifier failure
high-impact broad consequence
```

### ABSTAIN

```text
material element unresolved
reliable retrieval impossible
evidence insufficient
out of scope
```

Reason codes:

```text
RULE_AMBIGUITY
MATERIAL_CONFLICT
ELEMENT_UNRESOLVED
AUTHORITY_CONFLICT
REPEATED_VERIFIER_FAIL
HIGH_IMPACT
OUT_OF_SCOPE
INSUFFICIENT_EVIDENCE
```

## Input

Only compact `DECISION_PACKET`, never raw logs/full history.

## Output

```json
{
  "route": "K3",
  "reason_code": "AUTHORITY_CONFLICT"
}
```

## Initial dataset

```text
Total: 2800
Train: 2016
Valid: 280
Fresh: 504
First run: 1 effective epoch
```

Required contrasts:

```text
same task, low vs high impact
same ambiguity, reversible vs irreversible
one verifier fail vs repeated verifier fail
ordinary factual conflict vs authoritative conflict
settled rule vs rule ambiguity
retrievable missing evidence vs unavailable evidence
DS-worthy application vs K3-worthy architecture decision
```

## Gate

```text
Unsafe LOCAL Rate overall             <= 0.01
Unsafe LOCAL Rate high-impact         = 0
Correct Escalation Recall             >= 0.95
K3 Recall architecture/high-impact    = 1.00 target
Reason-Code Accuracy                  >= 0.90
Over-Escalation Rate                  <= 0.15 initially
Invalid Output                        = 0
```

\[
Cost(UnsafeLocal)\gg Cost(OverEscalation)
\]

## Deterministic safety override

Example:

```text
if HIGH_IMPACT and AUTHORITY_CONFLICT:
    minimum_route = K3
```

The learned policy operates inside this safety envelope.


# 14. Recommended End-to-End Training Path

```text
PHASE 0
Freeze R1 adapter + threshold + inference contract
        ↓
PHASE 1
R3 Authority / Operativeness
        ↓
Fresh Test
        ↓
Shadow
        ↓
Freeze R3
        ↓
PHASE 2
R2A Candidate→Element relation
        ↓
R2B Minimal Evidence Set
        ↓
Fresh Test
        ↓
Shadow + token reduction measurement
        ↓
Freeze R2
        ↓
PHASE 3
R4 Role / Retention / Fidelity
        ↓
Fresh Test
        ↓
State-lifecycle Shadow
        ↓
Freeze R4
        ↓
PHASE 4
R5 Sufficiency
        ↓
Fresh Test
        ↓
Adversarial exception/conflict Shadow
        ↓
Freeze R5
        ↓
PHASE 5
R6 State Patch
        ↓
Fresh Test
        ↓
Historical trajectory replay
        ↓
State invariant verifier
        ↓
Freeze R6
        ↓
PHASE 6
R7 Intelligence Jurisdiction
        ↓
Fresh Test
        ↓
Shadow routing
        ↓
Fail-open Canary
        ↓
Freeze R7
        ↓
PHASE 7
Full QXEN end-to-end system test
```


# 15. Promotion Ladder

Never:

```text
Train → Production
```

Always:

```text
Train
↓
Valid selection
↓
Freeze checkpoint
↓
Fresh untouched test
↓
Shadow
↓
Fail-open Canary
↓
Production
```

Shadow logs:

```text
run_id
task_id
input hash
adapter hash
prediction
margin / score
verifier outcome
human/cloud override
task success
token impact
failure class
```

Canary starts small, e.g. ~10% of eligible decisions.

Fail-open:

```text
uncertain
→ retain context / escalate
not
→ silently drop / act
```

Critical-miss increase:

```text
ROLL BACK TO SHADOW
```


# 16. Fresh-Test Discipline

A fresh test ceases to be fresh once used for:

- checkpoint choice;
- threshold selection;
- prompt redesign;
- label correction;
- targeted training-data generation.

Then mark:

```text
DIAGNOSTIC_ONLY
```

and create a new untouched test.

Never move frozen test examples into replay/training.

This prohibition should itself be represented in RULE_SPEC.


# 17. Downstream Impact Metrics

A stage can have high classification accuracy and still harm the Agent.

R3:

```text
wrong source chosen
stale source accepted
conflict hidden
```

R2:

```text
task success
token reduction
critical evidence miss
counter-evidence miss
```

R4:

```text
constraint persistence
wrong compression
state bloat
```

R5:

```text
unsafe act
unnecessary retrieve
missed stop condition
```

R6:

```text
state correctness
wrong overwrite
lost provenance
```

R7:

```text
unsafe local execution
unnecessary DS calls
unnecessary K3 calls
false abstention
```


# 18. Global Objective

True QXEN objective is not minimum CE loss.

\[
\max_{\pi}
rac{
TaskSuccess(\pi)
\cdot CriticalEvidenceRecall(\pi)
\cdot ConstraintRetention(\pi)
\cdot StateCorrectness(\pi)
}{
CloudContextTokens(\pi)
+\lambda_1 RawLeakage(\pi)
+\lambda_2 DuplicateContext(\pi)
+\lambda_3 UnnecessaryRetrieval(\pi)
+\lambda_4 UnnecessaryEscalation(\pi)
}
\]

subject to:

\[
Reliability\ge R_{min}
\]

Then minimize cost.

Agent capability:

\[
AgentCapability
=
ModelCapability
	imes ContextQuality
	imes ToolReliability
	imes StateCorrectness
\]

QXEN mainly optimizes ContextQuality, StateCorrectness, and routing efficiency.


# 19. Directory Convention

Avoid dotted directory names such as `r2.1`.

```text
data/
  r3_authority_operativeness/
  r2_element_selection/
  r4_retention_fidelity/
  r5_sufficiency/
  r6_state_update/
  r7_escalation/

scripts/
  r3/
  r2/
  r4/
  r5/
  r6/
  r7/

outputs/
  qxen_r3_authority/
  qxen_r2_selection/
  qxen_r4_retention/
  qxen_r5_sufficiency/
  qxen_r6_state/
  qxen_r7_jurisdiction/

reports/
  R3/
  R2/
  R4/
  R5/
  R6/
  R7/
```


# 20. Required Artifacts Per Stage

Every stage must produce:

```text
METHOD.md
train.jsonl
valid.jsonl
test_fresh.jsonl
ground_truth.jsonl
manifest.json
QA_report.json
dataset sha256
training config
training script
training log
memory monitor log
checkpoint comparison report
selected adapter
adapter sha256
fresh test report
shadow report
canary report
failure mining report
version-library entry
```


# 21. Model Version Library Update

After every training, repair, threshold change, or promotion decision append:

```yaml
timestamp:
stage:
version:
files_changed:
dataset_hash:
adapter_hash:
config_hash:
reason:
hypothesis:
change:
metrics_before:
metrics_after:
impact:
known_weaknesses:
verification:
promotion_status:
rollback_target:
```

Never delete failed experiments; freeze them as baselines.


# 22. Failure Escalation Discipline

```text
Attempt #1 FAIL
→ diagnose
→ bounded repair

Attempt #2 FAIL
→ stop blind repair
→ escalate
```

Escalate to DS when:

```text
Rule is mostly clear
but application/data design needs deeper reasoning
```

Escalate to K3 when:

```text
Rule itself is ambiguous
architecture must change
high-authority evidence conflicts
stage boundaries require redefinition
two bounded repairs fail
high-impact systemic consequence exists
```


# 23. What Must Never Be Merged Again

Do not return to one flat label space mixing:

```text
REL / IRREL
PIN / SESSION
VERBATIM / COMPRESSIBLE
CURRENT / STALE
REFRESH / RETRIEVE
LOCAL / DS / K3
```

These are different decision axes.

The legal-element architecture exists specifically to factor them.


# 24. Final Canonical System

```text
RAW ENVIRONMENT
        │
        ▼
R0 — RULE / ELEMENT COMPILER
        │
        ▼
R1 — MATERIALITY
        │
        ▼
R3 — AUTHORITY / OPERATIVENESS
        │
        ▼
R2 — EVIDENCE-TO-ELEMENT SELECTION
        │
        ▼
R4 — ROLE / RETENTION / FIDELITY
        │
        ▼
R5 — ELEMENT SUFFICIENCY
        │
        ├── ENOUGH_TO_STOP
        ├── NEED_MORE → RETRIEVE
        └── ENOUGH_TO_ACT
                         │
                         ▼
R6 — FINDING / STATE UPDATE
                         │
                         ▼
R7 — INTELLIGENCE JURISDICTION
              LOCAL / DS / K3 / ABSTAIN
                         │
                         ▼
                     VERIFIER
                         │
                  PASS / FAIL / REOPEN
```

Canonical definition:

> **QXEN treats context management as an evidentiary decision process. A task is decomposed into material elements; candidate context is judged for materiality, authority, operative force, and evidentiary relation; the system chooses a minimal sufficient evidentiary set, applies asymmetric burdens of proof, converts verified evidence into findings, maintains state through auditable patches, and assigns unresolved questions to the minimum sufficient intelligence tier.**

中文：

> **QXEN 将上下文管理视为“证据—要件判断过程”：先将任务拆成实质要件，再判断候选上下文与要件的证明关系、权威性和时间效力，在满足证明标准的前提下选取最小充分证据集，形成经验证的 Findings，通过可审计 State Patch 更新 Working State，并将尚未解决的问题升级到合适层级的智能模型。**


# 25. Execution Rule for the Agent

When this skill is invoked:

1. **优先执行 §0 统一路线**（REVISION 2026-08-14），以下旧章节仅在被 §0 覆盖处外的细节仍适用；
2. inspect current stage and frozen artifacts;
3. never assume the previous stage passed;
4. verify hashes and gate reports;
5. execute only one bounded training/validation batch at a time;
6. preserve fresh-test isolation;
7. treat verifier outputs as source of truth;
8. update model version library after every accepted change;
9. do not advance until the current stage passes Offline + Shadow + required Canary gates;
10. if uncertain, preserve evidence rather than delete it;
11. if Rule/authority conflict remains unresolved, escalate rather than invent a conclusion.

End of skill.
