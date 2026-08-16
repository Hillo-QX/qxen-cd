# QXEN-CD 0.1.0

This first public cut exposes the auditable deterministic boundary of QXEN-CD.
It is suitable for integration experiments and schema/guard evaluation.

It is not a bundled model release and does not claim that any particular model
has passed a production Gate. The host application must supply a provider,
authorization policy, domain validation, and final-agent review.

## Unreleased architecture update

- Extended the deterministic audit ledger with `process`, `ingest`, `compact`,
  `bootstrap`, and `audit_assistant` pipeline attribution.
- Added explicit raw-source/payload character accounting and conservative
  token savings calculations.
- Fallback replay cost is recorded separately and does not inflate savings.
- Capsule generation and capsule usage are reported separately; unused
  capsules are not presented as confirmed context savings.
- Added public tests covering pipeline separation and confirmed capsule use.
