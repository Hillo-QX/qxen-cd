# Architecture

This document describes `QXEN-CD 0.2.0 — Codex Stable`, the recommended public
baseline for Codex host integrations.

QXEN-CD separates semantic proposal from deterministic enforcement:

```text
source material
      |
      v
model/provider (optional, advisory)
      |
      v
lightweight validation -------> ADVISORY long-text capsule
      |
      +------ high-risk evidence -> full guard_v1
                                      |
                                      v
                              ACCEPT or FALLBACK
      |
      v
compact: hash de-dup + verbatim retention + budget
      |
      v
Context Burden gate
      |
      +---- ratio < 1 and accepted_capsules > 0 -> minimal gpt_context_payload
      |
      +---- otherwise -> BYPASS_QXEN and source/targeted retrieval
      |
      v
main-agent context and final decision
```

The full Guard is intentionally conservative. It accepts only evidence sources
that can be matched to the material supplied in the prompt. Formatting-only
differences (Unicode normalization, whitespace, and dash variants) may be
canonicalized; invented or untraceable sources are rejected. Long-text
advisory capsules use a lightweight structural boundary: missing `key_evidence`
is allowed and must not cause a false fallback.

Compaction is not semantic summarization. It does not choose which authority
is correct, resolve conflicts, or authorize an action. It maintains an
auditable bounded state, keeps high-risk rejected outputs in
`pending_gpt_review`, and retains pointer-only degraded records for long-text
fallbacks so the host can recover the exact source when needed.

The production savings gate is deliberately stricter than “capsule size vs.
source size.” The measured unit is the final payload that the host would inject
into the main agent context. Full MCP envelopes, debug fields, preflight tables,
raw model output, and rolling `compact_state` are excluded from the default
payload. If the default `gpt_context_payload` is not smaller than the direct
source context, QXEN-CD returns `BYPASS_QXEN`; this is a successful no-injection
decision, not a Guard failure.

The MCP adapter is optional. The deterministic Python functions are the stable
integration boundary and can be embedded in a non-MCP host.

## P0/P1 capsule lifecycle

Long or reusable host responses may be placed in a `PENDING_QXEN` capsule. The
host claims it atomically and receives a unique lease token:

```text
PENDING_QXEN -> RUNNING_QXEN -> COMPLETED
                         \\-> PENDING_QXEN -> FAILED
```

Claims use an exclusive file lock, an atomic replace, and a bounded lease. A
stale `RUNNING_QXEN` capsule is recovered on the next claim; a late worker
cannot complete it because its token no longer matches. Repeated completion is
idempotent. This is deterministic host state, not a model decision.

P1 surfacing uses the latest active-turn prompt usage rather than cumulative
session totals. A pending capsule is surfaced only for the same session and a
strong task/keyword relation; pressure-based surfacing additionally requires a
fresh capsule and a weak keyword relation. Unrelated tasks remain isolated.

## Stable Codex boundary

The public Codex integration is host-neutral and provider-neutral. The host
must provide the MCP process, provider call, hook lifecycle, transcript access,
and final decision layer. QXEN-CD only provides deterministic primitives and
advisory capsule handling. Personal hook files, model caches, training assets,
private logs, and machine-specific paths are intentionally outside this
repository.
