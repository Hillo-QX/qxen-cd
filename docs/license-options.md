# License decision and asset map

## Recommended: Apache-2.0

Apache-2.0 is the best default for this infrastructure because it permits
commercial use, modification, redistribution, and closed-source integration,
while providing an explicit patent license and patent-termination clause.
That is useful for an MCP/runtime, hook, and audit framework likely to be
embedded by companies.

## Alternatives

- MIT is shorter and very permissive, but has less explicit patent language.
- AGPLv3 is appropriate only if network-service modifications must remain open;
  it can materially reduce enterprise adoption and connector reuse.
- A dual-license model can be considered later, but it adds administration and
  is unnecessary for the first release.

## Boundary recommendation

| Component | Recommended treatment |
|---|---|
| QXEN-CD core code | Apache-2.0 |
| Codex adapter code | Apache-2.0; optional integration only |
| LocalQwen adapter code | Apache-2.0; optional integration only |
| Schema and technical docs | Apache-2.0 |
| Synthetic example data | CC BY 4.0, with attribution and provenance |
| Model weights/adapters | Separate upstream/model license; do not bundle |
| Training/evaluation data | Per-source license or do not publish |

The root `LICENSE` covers Apache-2.0 files. The CC BY 4.0 examples carry an
explicit notice in `examples/synthetic/README.md`. Model and data licenses are
not inherited from the root code license.
