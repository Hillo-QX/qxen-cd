# QXEN-CD

QXEN-CD is an auditable evidence-processing layer for agent systems. It turns
long or noisy material into bounded Evidence Capsules, preserves citations and
verbatim fields, rejects unsafe model output, and compacts accepted capsules
into rolling context.

It is deliberately not an autonomous truth, legal, authority, conflict, or
action classifier. A model may propose semantic fields; the deterministic
guard decides whether the proposal can enter stable context, and the host
agent remains responsible for final decisions.

## What is included

- `qxen_cd.guard`: JSON parsing, required fields, enum checks, source
  canonicalization, and complete raw-output fallback.
- `qxen_cd.compact`: deterministic de-duplication, verbatim preservation,
  pending-review isolation, and context budgets.
- `qxen_cd.audit`: paired baseline/QXEN usage observations without inventing
  savings when a baseline is missing.
- `qxen_cd.mcp_server`: optional MCP adapter exposing the deterministic core.
- `configs/evidence_capsule_v1_schema.json`: public storage contract.

No model weights, training data, private logs, API keys, Codex configuration,
or host-specific paths are included.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[schema]'
pytest
```

Use the core without a model:

```python
from qxen_cd import guard_v1, compact

prompt = "来源：report/a.txt"
raw = '{"capsule_id":"EC-1","source_type":"report","relevance":"high",' \
      '"key_evidence":[{"text":"原文","source":"report/a.txt",' \
      '"preserve_verbatim":true}],"sufficiency":"sufficient"}'
checked = guard_v1(raw, prompt)
state = compact([checked], max_chars=24000)
```

## Provider boundary

The public package does not load a model. An application may connect a local,
remote, or hosted model, then pass its raw JSON through `guard_v1` before any
stable-context write. Recommended production flow:

```text
material -> provider -> raw JSON -> deterministic guard
        -> ACCEPT capsule -> rolling compact -> main-agent review
        -> FALLBACK raw + reason -> explicit review queue
```

Do not treat `operative_status`, `authority`, `conflicts`, `next_step`, or
`uncertainty` as final decisions without host-agent review.

## License and third-party assets

The repository uses explicit asset boundaries:

| Asset | License policy |
|---|---|
| Core code and optional Codex/LocalQwen adapters | Apache-2.0 |
| Documentation and schema | Apache-2.0 |
| Synthetic examples | CC BY 4.0; attribution required |
| Model weights and LoRA adapters | Separate upstream/model license |
| Training data | Per-source license; private data is excluded |

Model weights, training/evaluation data, external connectors, and user-provided
materials are not relicensed by this repository. See `docs/model-policy.md`,
`docs/data-policy.md`, and `docs/security.md` before publishing an adapter or
dataset.
