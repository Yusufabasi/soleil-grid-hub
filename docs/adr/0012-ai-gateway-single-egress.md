# ADR-0012: One AI gateway as the single egress to Claude

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, AI engineer, security engineer

## Context

Claude is used for triage, reply drafting, summaries and ask-the-data (FR-09 to FR-12). The Terms of
Reference allow only approved providers, forbid sensitive data in AI tools and require human review of
output that affects money. The model's output is untrusted input.

## Decision

All calls go through the `ai_gateway` module: redaction before the call, structured output
(`output_config.format`, no forced tool choice, no prefill), effort set explicitly per feature,
validation of every response, handling of the `refusal` stop reason, retries and a circuit breaker,
budgets in dollars (per ticket, per feature per day, per customer and per API key), and an `ai_runs`
audit row for every call recording the served model. Server-side fallbacks are pinned to a list of
approved models inside the approved agreement and are off for evaluation runs and the batch path. No
model-controlled path to any write exists: the gateway's database role may only insert into `ai_runs`.
Ask-the-data never returns query rows to the model.

## Alternatives Considered

### Each module calls the API directly
- **Pros**: less indirection.
- **Cons**: redaction, budgets and audit would be repeated and forgotten.
- **Why not**: the controls must be impossible to bypass.

### Provider-chosen default fallbacks
- **Pros**: no list to maintain.
- **Cons**: the substitute model cannot be pinned or reviewed against the approved agreement.
- **Why not**: rejected by the security review.

## Consequences

### Positive
- One place to audit, budget, redact and change models; drift is observable.

### Negative
- The gateway is a hard dependency of the AI features and needs its own degradation path to manual work.

### Risks
- The model alias `claude-opus-5-5` has no dated snapshot; drift monitoring and golden-set gates are required.
