# Model and data policy

QXEN-CD core code is independent of model weights. A provider adapter may be
distributed separately only when all of the following are documented:

- base-model and adapter names and their exact upstream licenses;
- quantization and runtime requirements;
- training-data provenance and redistribution rights;
- evaluation protocol, known failure modes, and fallback behavior;
- whether outputs are advisory and require a main-agent review.

Do not bundle weights or private evaluation material with the core repository.
Do not claim token savings from model calls that were added only to operate or
audit QXEN-CD. Keep audit-only overhead separate from business-task savings.
