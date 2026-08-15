# Data and model release policy

## Models and LoRA

Model weights and LoRA adapters are not part of the core repository. Each
release must include the exact base-model identifier, revision, quantization,
adapter relationship, upstream license URL, and any restrictions on
redistribution. A LoRA trained on a base model does not automatically become
Apache-2.0 merely because the surrounding code is Apache-2.0.

For the local Qwen3.5 asset, retain the exact upstream model license and
include its required notices. Publish the adapter separately only after
checking the base model's current repository license and distribution terms.

## Training and evaluation data

Data must be reviewed item by item. Record source, owner, collection method,
license, permitted purpose, transformations, and whether redistribution is
allowed. Private financial, legal, customer, task-history, and log material is
excluded from the public repository.

## Synthetic examples

Synthetic examples are clearly marked and licensed CC BY 4.0. They must never
be described as real evidence or used to imply that a model passed a real-world
Gate.
