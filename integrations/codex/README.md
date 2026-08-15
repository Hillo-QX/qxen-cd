# Codex adapter boundary

This directory documents the optional Codex integration boundary. Any adapter
code authored for this repository is Apache-2.0 under the root `LICENSE`.

The adapter must:

- pass provider output through `qxen_cd.guard_v1`;
- never treat a capsule as a final decision;
- keep host-specific hooks, credentials, paths, and private session state out
  of the public repository;
- preserve the complete fallback payload for main-agent review.

This public package does not include a personal Codex hook or installation
configuration. Hosts should implement those pieces locally.
