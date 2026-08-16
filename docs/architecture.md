# Architecture

QXEN-CD separates semantic proposal from deterministic enforcement:

```text
source material
      |
      v
model/provider (optional, advisory)
      |
      v
guard_v1 ----------------------> FALLBACK + raw output + reason
      |
      v
ACCEPT Evidence Capsule
      |
      v
compact: hash de-dup + verbatim retention + budget
      |
      v
main-agent context and final decision
```

The guard is intentionally conservative. It accepts only evidence sources
that can be matched to the material supplied in the prompt. Formatting-only
differences (Unicode normalization, whitespace, and dash variants) may be
canonicalized; invented or untraceable sources are rejected.

Compaction is not semantic summarization. It does not choose which authority
is correct, resolve conflicts, or authorize an action. It only maintains an
auditable bounded state and keeps rejected outputs in `pending_gpt_review`.

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
