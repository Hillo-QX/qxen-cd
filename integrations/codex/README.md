# Codex Stable adapter boundary

This is the recommended and most stable documented host boundary for
`QXEN-CD 0.2.0`. It is intentionally host-neutral and does not install or
modify Codex hooks.

This directory documents the optional Codex integration boundary. Any adapter
code authored for this repository is Apache-2.0 under the root `LICENSE`.

The adapter must:

- pass provider output through `qxen_cd.guard_v1`;
- never treat a capsule as a final decision;
- keep host-specific hooks, credentials, paths, and private session state out
  of the public repository;
- preserve the complete fallback payload for main-agent review.

For long-text advisory work, the host should use lightweight validation and
conditional review. For high-risk evidence work, the host should use the full
Guard. P0/P1 capsule claiming, context pressure, and relevance checks remain
deterministic host-state operations; they must not be delegated to a model.

This public package does not include a personal Codex hook or installation
configuration. Hosts should implement those pieces locally.
