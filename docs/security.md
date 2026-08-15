# Security and release checklist

Treat all input material as data, not instructions. Provider prompts should
state that commands found inside evidence must not be executed.

Before releasing a build or adapter:

1. Run the tests and inspect every fallback reason.
2. Verify that rejected output preserves the complete raw response for review.
3. Verify that accepted evidence sources are traceable to supplied material.
4. Keep model weights, API credentials, private logs, user data, and training
   sets outside the repository.
5. Do not allow capsule fields to directly trigger filesystem writes, external
   messages, financial trades, or legal conclusions.
6. Record paired baseline/QXEN observations. Report savings only for comparable
   work items; label character-to-token conversion as an estimate.

The guard is a safety boundary, not a guarantee that the underlying model is
factually correct. Host systems must add authorization, access control,
content filtering, and domain-specific validation.
