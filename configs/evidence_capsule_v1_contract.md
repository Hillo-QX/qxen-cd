# Evidence Capsule v1 (public contract)

The capsule is a storage and handoff format, not a final decision protocol.
Required fields are `capsule_id`, `source_type`, `relevance`, `key_evidence`,
and `sufficiency`. Each key-evidence item contains non-empty `text` and
traceable `source`; `preserve_verbatim=true` marks content that must not be
rewritten or dropped.

Allowed values are intentionally narrow at the guard boundary:

- `source_type`: `data_file`, `config`, `report`, `code`, `model_weights`,
  `log`, `doc`, `env_check`, `other`;
- `relevance`: `low`, `medium`, `high`;
- `sufficiency`: `insufficient`, `sufficient`;
- optional `operative_status`: `CURRENT`, `STALE`, `SUPERSEDED`.

Other semantic fields such as authority, conflicts, uncertainty, and next
step remain advisory. A host agent must review them before acting.

Any parse failure, truncation, missing required field, illegal enum, missing
evidence source, or source that cannot be matched to input material produces
`FALLBACK`. The raw model output is retained in the review envelope.

See `evidence_capsule_v1_schema.json` for the machine-readable schema.
