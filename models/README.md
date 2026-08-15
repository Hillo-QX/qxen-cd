# Model release boundary

No model weights or LoRA files are included in the QXEN-CD core repository.

To publish an optional model package, create a separate release containing:

1. exact base model and revision;
2. adapter name and base-model compatibility;
3. quantization/runtime details;
4. the upstream model license and required notices;
5. data provenance and evaluation limitations;
6. a statement that QXEN-CD code remains Apache-2.0 but the model package is
   governed by its own license.

The local Qwen3.5-9B MLX asset is an external model artifact. Its local README
identifies the upstream Qwen3.5-9B license as Apache-2.0; verify the exact
upstream revision before distributing any converted weights or LoRA adapter.
