# QXEN-CD 0.2.0 — Codex Stable

This is the current stable public release for Codex integrations. It exposes
the auditable deterministic boundary of QXEN-CD and is suitable as the public
baseline for schema, Guard, compaction, and capsule-lifecycle evaluation.

It is not a bundled model release and does not claim that any particular model
has passed a production Gate. The host application must supply a provider,
authorization policy, domain validation, host hooks, and final-agent review.

## Stable architecture

- Long-text processing uses an advisory capsule path with lightweight
  validation; `key_evidence` is optional for this path.
- High-risk evidence processing retains full deterministic Guard checks for
  schema, enums, source matching, truncation, and raw fallback preservation.
- `compact` accepts advisory capsules, de-duplicates by hash, preserves source
  pointers and verbatim evidence, and applies bounded context budgets.
- P0 capsule state uses atomic claim leases, stale-worker recovery, late-token
  rejection, and idempotent completion.
- P1 surfacing uses same-session relevance and latest active-turn context
  pressure; unrelated pending capsules remain isolated.
- Audit records separate baseline estimates, fallback replay, capsule use, and
  audit-only overhead.

## Earlier architecture updates

- Extended the deterministic audit ledger with `process`, `ingest`, `compact`,
  `bootstrap`, and `audit_assistant` pipeline attribution.
- Added explicit raw-source/payload character accounting and conservative
  token savings calculations.
- Fallback replay cost is recorded separately and does not inflate savings.
- Capsule generation and capsule usage are reported separately; unused
  capsules are not presented as confirmed context savings.
- Added public tests covering pipeline separation and confirmed capsule use.
# 0.2.0 — P0/P1 capsule reliability and client integration

- Add deterministic capsule leases, atomic claims, stale-worker rejection, and
  idempotent completion in `qxen_cd.capsule_state`.
- Add active-turn context pressure estimation and same-session P1 surfacing
  rules with freshness and relevance guards.
- Add concurrency, lease-recovery, idempotency, pressure, and isolation tests.
- Document the host integration boundary: QXEN remains advisory; Guard and the
  main agent retain final authority.
