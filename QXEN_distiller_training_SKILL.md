# QXEN Distiller Training

## Purpose

Train a Qwen3.5 9B–based local Agent system to perform high-quality context distillation and bounded tool execution.

The goal is **not** to train a general-purpose frontier model.

The goal is to make Qwen3.5 9B highly specialized at:

- selecting relevant context;
- removing stale, redundant, or irrelevant context;
- preserving exact constraints and verified facts;
- maintaining compact structured working state;
- executing bounded tasks from that working state;
- learning from deterministic verification;
- reducing DeepSeek token consumption without materially reducing downstream task quality.

Target production architecture:

```text
Raw Project / Agent History
          │
          ▼
Qwen3.5 9B
Context Distiller LoRA
          │
          ▼
WORKING_STATE
          │
     ┌────┴────┐
     │         │
     ▼         ▼
DeepSeek     Qwen3.5 9B
Dispatcher   Executor LoRA
     │         │
     └────┬────┘
          ▼
       Verifier
          │
          ▼
Updated WORKING_STATE
```

---

## 1. Core Training Philosophy

Do not train Qwen to imitate a large model's prose.

Do not train Qwen to summarize everything.

Do not optimize primarily for compression ratio.

Train it to learn:

> What information must remain available so that the downstream Agent can still complete the task correctly?

Primary optimization objective:

```text
Task Success
÷
Context Tokens
```

Subject to:

```text
Critical constraints preserved
Verified facts remain correct
No false completion
No stale state treated as current state
```

Task success must always have higher priority than context compression.

---

## 2. Model Architecture

Use one common Qwen3.5 9B base model with two separate adapters.

```text
Qwen3.5 9B Base
│
├── Context LoRA
│   │
│   ├── RAW CONTEXT
│   ├── Context Selection
│   ├── KEEP / DROP / PIN
│   ├── State Distillation
│   └── WORKING_STATE
│
└── Executor LoRA
    │
    ├── WORKING_STATE
    ├── CURRENT TASK
    ├── TOOL ACTION
    ├── TOOL RESULT
    ├── VERIFICATION
    └── TASK_COMPLETE / ESCALATE
```

Do not merge the adapters during the initial training stages.

Evaluate them independently first.

---

## 3. Context LoRA Role

The Context LoRA acts as a high-level information filter and state manager.

Its primary responsibilities are:

```text
RAW HISTORY
    ↓
SELECT
    ↓
FILTER
    ↓
PRESERVE EXACT DATA
    ↓
COMPRESS
    ↓
STRUCTURED STATE
```

It must learn the following context actions:

```text
PIN
KEEP
VERBATIM
COMPRESS
DROP
REFRESH
RETRIEVE
```

### PIN

Information that must persist across Agent turns.

Typical examples:

- overall goal;
- user hard requirements;
- architecture invariants;
- security restrictions;
- forbidden paths;
- immutable project rules.

### KEEP

Information currently required for task execution.

Examples:

- current failure;
- current task;
- relevant decisions;
- recent verified result.

### VERBATIM

Information that must remain exact and must not be paraphrased.

Examples:

- paths;
- function signatures;
- API schemas;
- exact error messages;
- acceptance criteria;
- test expected values;
- hashes;
- versions;
- numerical values.

### COMPRESS

Important information whose full raw form is no longer required.

Example:

```text
300-line successful pytest output
```

may become:

```text
test_dispatcher.py::test_done_transition = PASS
```

### DROP

Context that no longer has sufficient downstream value.

Examples:

- old resolved traceback;
- repetitive ls output;
- obsolete planning;
- completed-task shell logs;
- repeated README content.

### REFRESH

Information that was useful but may have become stale after a file or state change.

The Agent should re-read the authoritative source.

### RETRIEVE

Important information is missing from the working state and must be fetched again from source.

---

## 4. Working State Schema

Context LoRA should not primarily produce natural-language summaries.

Its main output must be structured state.

Recommended schema:

```yaml
overall_goal:
  ...

current_phase:
  ...

constraints:
  - ...

architecture_invariants:
  - ...

verified_facts:
  - fact: ...
    evidence: ...
    status: VERIFIED

completed_tasks:
  - task_id: T001
    status: PASS

current_task:
  task_id: ...
  goal: ...
  acceptance_criteria:
    - ...

current_failure:
  present: true
  error: ...
  evidence: ...

relevant_files:
  - path: ...
    reason: ...
    needs_refresh: false

decisions:
  - ...

stale_information:
  - ...

next_required_evidence:
  - ...

escalation_required: false
```

Never place speculation in `verified_facts`.

---

## 5. Executor LoRA Role

Executor LoRA receives:

```text
SYSTEM RULES
+
WORKING_STATE
+
CURRENT TASK
+
MINIMUM NECESSARY RAW EVIDENCE
```

It must not depend on full Agent history.

Its job is:

```text
Read
↓
Edit
↓
Run
↓
Verify
↓
Report
```

The Executor should preferably perform one bounded task at a time.

Recommended escalation policy:

```text
Attempt 1 fails
    ↓
Local diagnosis and retry

Attempt 2 fails
    ↓
ESCALATE TO DEEPSEEK
```

Do not escalate deterministic, easily fixable failures immediately.

---

## 6. Teacher Data Sources

Primary training sources:

```text
1. Local Kimi Code trajectories
2. Successful local Agent trajectories
3. Human-corrected trajectories
4. Synthetic hard-negative contexts
5. Deterministic verifier feedback
```

DeepSeek acts primarily as:

```text
Context Curator
Label Generator
State Distillation Teacher
Failure Analyst
Quality Reviewer
```

DeepSeek should not become the high-token executor.

---

## 7. Teacher Label Quality

The largest training risk is incorrect teacher labeling.

Example:

```text
Actually critical information
        ↓
Teacher labels DROP
        ↓
Student learns wrong behavior
```

or:

```text
Stale information
        ↓
Teacher labels KEEP
        ↓
Student propagates obsolete state
```

Therefore no DeepSeek-generated context label is automatically considered ground truth.

Mandatory pipeline:

```text
Teacher
   ↓
Distilled Context
   ↓
Executor Replay
   ↓
Verifier
   ↓
PASS / FAIL
```

Only downstream-validated samples become high-confidence positive examples.

---

## 8. Training Data Validation Rule

A distilled context may only become a preferred positive sample when:

```text
Task executes successfully
AND
Acceptance criteria pass
AND
No forbidden action occurs
AND
No required constraint is lost
AND
No false verified fact is introduced
```

A fluent summary is not sufficient.

---

## 9. Avoid Extreme Compression

Never train the model with:

```text
shorter context = always better
```

This leads to pathological over-compression.

The desired hierarchy is:

```text
Task Success
>>
Constraint Preservation
>>
Verified Fact Accuracy
>>
Context Compression
```

Operational principle:

> Keeping 2,000 additional useful tokens is preferable to dropping one critical constraint.

A 3K state that causes failure is worse than an 8K state that succeeds.

---

## 10. Hard Negative Training

Hard negatives are mandatory.

A 9B model must learn to distinguish semantically similar but operationally different context.

Generate examples such as:

```text
dispatcher.py
dispatcher_old.py
dispatcher_backup.py
```

```text
ERROR from current run
ERROR from yesterday
ERROR already resolved
```

```text
current checkpoint
old checkpoint
deprecated checkpoint
```

```text
current API schema
previous API schema
example schema
```

```text
current acceptance criteria
completed-task acceptance criteria
```

```text
current config
backup config
broken config
```

The training target must correctly identify the authoritative current source.

For every high-quality positive training example, generate approximately 1–3 hard-negative variants.

Possible corruption strategies:

```text
Inject stale file version
Inject obsolete error
Inject old task
Inject duplicate tool logs
Inject deprecated architecture decision
Inject misleading filename
Remove one critical constraint
Replace current state with old checkpoint
Add irrelevant but semantically similar documentation
```

Every corrupted variant must record exactly what was altered.

---

## 11. Dataset Structure

Recommended project structure:

```text
dataset/
├── 01_context_selection.jsonl
├── 02_state_distillation.jsonl
├── 03_state_update.jsonl
├── 04_executor_sft.jsonl
├── 05_context_preferences.jsonl
├── 06_hard_negatives.jsonl
├── 07_verifier_rollouts.jsonl
└── dataset_manifest.json
```

### Dataset 01 — Context Selection

Input:

```text
Goal
Current Task
Raw Context Chunks
```

Target:

```text
PIN
KEEP
VERBATIM
COMPRESS
DROP
REFRESH
RETRIEVE
```

### Dataset 02 — State Distillation

Input:

```text
Selected Context
```

Target:

```text
WORKING_STATE
```

### Dataset 03 — State Update

Input:

```text
OLD_STATE
+
NEW_TOOL_EVENTS
+
VERIFICATION_RESULT
```

Output:

```text
NEW_STATE
```

### Dataset 04 — Executor SFT

Input:

```text
WORKING_STATE
+
CURRENT_TASK
```

Target:

```text
NEXT TOOL ACTION
```

### Dataset 05 — Preference Training

Construct chosen/rejected context pairs.

Example:

```text
Chosen:
5.4K tokens
all constraints preserved
task PASS

Rejected:
3.1K tokens
one constraint removed
task FAIL
```

Preference:

```text
Chosen > Rejected
```

Another valid pair:

```text
Chosen:
5.4K tokens
task PASS

Rejected:
21K tokens
task PASS
contains redundant logs
```

Preference:

```text
Chosen > Rejected
```

The model therefore learns both:

```text
Do not over-compress
AND
Do not retain unnecessary context
```

---

## 12. Training Curriculum

Training must follow this order.

### Phase 0 — Infrastructure

Before model training:

```text
Collect trajectories
Normalize data
Build provenance
Build verifier
Build replay environment
Validate dataset quality
```

No LoRA training starts before this phase passes.

### Phase 1 — Context Selection SFT

Train:

```text
RAW CHUNKS
↓
CONTEXT ACTION LABELS
```

Primary skill:

```text
Relevant vs irrelevant vs stale vs exact-preserve
```

### Phase 2 — State Distillation SFT

Train:

```text
SELECTED CONTEXT
↓
STRUCTURED WORKING_STATE
```

Primary skill:

```text
Minimum sufficient operational state
```

### Phase 3 — State Update SFT

Train:

```text
OLD STATE
+
NEW EVENTS
↓
UPDATED STATE
```

Primary skill:

```text
Persistent Agent memory without full history
```

### Phase 4 — Executor SFT

Train:

```text
STATE + TASK
↓
TOOL ACTION
```

Primary skill:

```text
Bounded execution
```

### Phase 5 — DPO / Preference Optimization

Train:

```text
GOOD CONTEXT
>
BAD CONTEXT
```

Optimize simultaneously for:

```text
Sufficiency
Accuracy
Compactness
```

### Phase 6 — Verifier-Based Optimization

Only begin after SFT and preference training are stable.

Pipeline:

```text
Raw Context
↓
Context LoRA
↓
Working State
↓
Frozen/Stable Executor
↓
Verifier
↓
Reward
```

---

## 13. Reward Function

Reward must prioritize successful downstream execution.

Recommended initial structure:

```text
Task Success                     +10
All Acceptance Criteria PASS      +5
All Hard Constraints Preserved    +3
Verified State Correct            +3

Every extra 1000 context tokens  -0.15
Duplicate context retained        -1
Stale state retained              -2
Critical constraint omitted       -5
Incorrect verified fact           -8
False completion                 -10
Task failure                     -10
Forbidden operation              -15
```

The exact weights may later be calibrated from rollout data.

---

## 14. Evaluation Metrics

Do not use summary similarity as the primary benchmark.

Track:

### Critical Information Recall

```text
CIR =
critical information preserved
/
critical information required
```

Target:

```text
>= 98%
```

### Constraint Preservation Rate

```text
CPR =
correct hard constraints preserved
/
total hard constraints
```

Target:

```text
>= 99%
```

### Verified Fact Accuracy

Target:

```text
>= 99%
```

### Downstream Task Success

Measure actual task completion after context distillation.

### Context Compression Ratio

```text
distilled tokens
/
raw context tokens
```

Use only as a secondary metric.

### Task Success per Context Token

This is the primary long-term optimization metric.

---

## 15. DeepSeek Integration

DeepSeek is the strategic intelligence layer.

DeepSeek should receive:

```text
Overall Goal
Current State
Verified Facts
Current Failure
Hard Constraints
Relevant Code Excerpts
Decision Required
```

It should not normally receive:

```text
Full Agent conversation
Full Bash history
Full repository
All old tasks
All historical test logs
Repeated source reads
```

The Context Distiller exists specifically to prevent these high-token inputs from reaching DeepSeek.

Recommended escalation policy:

```text
architecture decision required
OR
task ambiguity is high
OR
Qwen fails twice
OR
high-risk modification required
OR
current state conflicts
OR
replanning is required
```

Do not call DeepSeek after every deterministic subtask.

Target long-run workload split:

```text
Qwen Local Processing:
80–90%

DeepSeek Strategic Intervention:
10–20%

Deterministic Verification:
whenever possible
```

---

## 16. Training Data Provenance

Every sample must record provenance.

Minimum fields:

```json
{
  "source": "kimi_local",
  "run_id": "...",
  "teacher": "deepseek",
  "teacher_version": "...",
  "verified": true,
  "training_allowed": true,
  "generated_at": "...",
  "dataset_version": "..."
}
```

Never mix unknown-origin data into the high-confidence dataset.

Maintain at least three quality tiers:

```text
GOLD
Verifier-confirmed successful trajectory

SILVER
Teacher-generated and partially verified

REJECTED
Failed replay, constraint loss, stale state,
false completion, or other quality failure
```

Only GOLD should dominate final SFT and preference datasets.

---

## 17. Failure Mining

Training is iterative.

Every trained model rollout must be logged.

When Qwen fails:

```text
Failure
↓
Identify context decision that caused failure
↓
Generate corrected context
↓
Replay
↓
Verifier
↓
GOOD/BAD preference pair
↓
Return to training dataset
```

Examples:

```text
Dropped required path
→ constraint-loss sample

Kept old config
→ stale-state sample

Re-read same source five times
→ redundancy sample

Claimed PASS without test
→ false-completion sample

Failed after two attempts
→ escalation-policy sample
```

The system must continuously turn real failures into future training data.

---

## 18. Kimi Context Learning Path

Use local Kimi Code data as a primary trajectory source.

Pipeline:

```text
Local Kimi Session
↓
Raw Trace
↓
Chunking
↓
DeepSeek Cleaning
↓
Context Labels
↓
Structured STATE
↓
Executor Replay
↓
Verifier
↓
Training Dataset
```

Do not blindly imitate Kimi prose.

Extract operational skills:

```text
what it read
what it ignored
what it retained
when it refreshed context
which tool it called
how it reacted to tool results
what evidence led to completion
```

---

## 19. Codex Reference Policy

Codex may be used for benchmark comparisons and system-level observations.

Do not directly use Codex output as Qwen imitation targets.

Permitted benchmark metadata includes:

```text
task success
tool-call count
files read
context-token usage
test pass/fail
elapsed Agent steps
retry count
```

The QXEN training target should remain based primarily on local/authorized trajectories plus verifier-backed data.

---

## 20. First Training Milestone

Do not attempt thousands of trajectories immediately.

First target:

```text
20–50 normalized real runs
```

They must include:

```text
successful runs
failed runs
retries
stale-context cases
constraint-sensitive tasks
simple code edits
multi-step tasks
tool failures
```

Then verify the complete pipeline.

Do not start LoRA training until:

```text
DATA_INVENTORY               PASS
TRAJECTORY_PARSE             PASS
TRACE_NORMALIZATION          PASS
CONTEXT_CHUNKING             PASS
CONTEXT_LABELING             PASS
STATE_DISTILLATION           PASS
STATE_REPLAY                 PASS
VERIFIER                     PASS
PROVENANCE                   PASS

CIR                         >= 0.98
CPR                         >= 0.99
FALSE_VERIFIED_FACT_RATE    <= 0.01
```

---

## 21. Scale-Up Path

After MVP passes:

```text
50 trajectories
↓
200
↓
500
↓
1,000
↓
2,000+
```

At each stage:

```text
Train
↓
Run held-out Agent tasks
↓
Mine failures
↓
Generate hard negatives
↓
Replay
↓
Update dataset
```

Dataset quality is more important than raw size.

---

## 22. Versioning

Maintain:

```text
TRAINING_CHANGELOG.md
DATASET_MANIFEST.json
MODEL_REGISTRY.json
EVALUATION_HISTORY.jsonl
```

Every model version should record:

```text
base model
adapter
dataset version
training config
date
evaluation scores
known failure modes
DeepSeek escalation rate
average context compression
task success rate
```

Suggested progression:

```text
QXEN-CD v0.1
Context Selection

QXEN-CD v0.2
State Distillation

QXEN-CD v0.3
Incremental State Update

QXEN-EX v0.1
Executor SFT

QXEN-CD v0.4
Hard-Negative Training

QXEN-CD v0.5
Preference Optimization

QXEN-Agent v0.8
Distiller + Executor + Verifier

QXEN-Agent v0.9
DeepSeek Conditional Routing

QXEN-Agent v1.0
Production validated
```

---

## 23. Final Success Definition

QXEN distiller training is considered successful when Qwen3.5 9B can reliably transform:

```text
large, noisy, multi-turn Agent history
```

into:

```text
small, accurate, sufficient WORKING_STATE
```

such that:

```text
Qwen Executor continues correctly,
DeepSeek sees only high-value context,
Verifier confirms task completion,
and overall task success does not materially decline.
```

The core learned capability is:

> Select what matters, preserve what must remain exact, discard what is stale, refresh what may have changed, and maintain the minimum sufficient context required for successful execution.

The final objective is:

```text
Minimum Sufficient Context
+
Maximum Reliable Task Completion
+
Minimum DeepSeek Token Consumption
```

---

## 24. Mandatory Principles

Whenever compression quality conflicts with downstream task reliability:

```text
Reliability wins.
```

Whenever model confidence conflicts with deterministic evidence:

```text
Evidence wins.
```

Whenever an old memory conflicts with the authoritative current source:

```text
Current source wins.
```

Whenever Qwen reaches the defined escalation threshold:

```text
DeepSeek takes over the high-level reasoning.
```

These principles govern the entire QXEN distiller training system.
