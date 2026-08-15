# Training and evaluation data boundary

Private training data, evaluation data, task ledgers, logs, financial
materials, and third-party text are intentionally excluded.

Before publishing any dataset, record for every source:

```text
source_id | owner | collection_method | license | redistribution_allowed
purpose | transformations | sensitive_data_review | provenance_url
```

Only data with verified redistribution rights may be released. Synthetic
examples belong under `examples/synthetic/` and use CC BY 4.0; they are not
silently treated as real-world evidence.
