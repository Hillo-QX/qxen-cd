# Codex Stable release checklist

## Included in this directory

- Deterministic Guard/Fallback core
- Codex Stable 0.2.0 release metadata and architecture boundary
- Rolling-context compactor
- Minimal paired-usage audit writer
- Pipeline-aware audit accounting with fallback replay and capsule-use tracking
- Evidence Capsule v1 schema and contract
- Optional MCP adapter with no model loading
- Offline tests and provider/security documentation

## Intentionally excluded

- Model weights and LoRA adapters
- Training, validation, and evaluation datasets
- Runtime logs, audit history, checkpoints, and task ledgers
- API keys, `.env` files, personal paths, Codex hooks, and private MCP configs
- Domain-specific financial or legal source material
- The original host-specific model runtime

## Before publishing to GitHub

1. Replace the placeholder contributor/copyright identity in `NOTICE` with the
   actual copyright holder.
2. Add a `CITATION.cff` only if citation metadata and author identities are
   ready to be public.
3. Run `python -m pytest -q` in a clean environment.
4. Run a secret scanner and dependency/license scanner.
5. Publish model adapters and datasets in separate repositories only after
   checking their upstream and data-provider terms.
6. Do not advertise token savings until at least 50 comparable paired usage
   observations exist; report estimates and audit-only overhead separately.
7. Ensure the release is identified as `QXEN-CD 0.2.0 — Codex Stable` and that
   the public `main` branch, package version, README, and release notes agree.
