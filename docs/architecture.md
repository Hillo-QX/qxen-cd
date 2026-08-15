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
