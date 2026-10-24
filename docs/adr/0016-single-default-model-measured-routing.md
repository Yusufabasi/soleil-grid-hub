# ADR-0016: One default model, with cheaper routing only after measurement

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, AI engineer

## Context

The default model is `claude-opus-5-5`: thinking cannot be disabled, effort defaults to `medium`, forced
tool choice and prefill are rejected, and output tokens are 72% of the cost. At 6,000 tickets a month the
whole AI bill is about $700.

## Decision

Use the one model for every feature, read from configuration, with effort set explicitly per feature
(triage low, draft medium, summary low, ask-the-data medium). Do not build a multi-model cascade until
the monthly bill passes about $5,000 and a smaller model passes the same golden-set gates, because at
today's volume routing could save at most about $100 a month and would split the prompt cache across
models. Measure the single model at lower effort first.

## Alternatives Considered

### Route simple tasks to a cheaper model from the start
- **Pros**: lower unit cost.
- **Cons**: separate prompts, evaluations and caches for savings that do not yet exist.
- **Why not**: premature.

### Pin a dated snapshot
- **Pros**: reproducibility.
- **Cons**: none is published for this model alias.
- **Why not**: not available; drift monitoring replaces it.

## Consequences

### Positive
- One evaluation suite, one cache namespace, simpler operations.

### Negative
- Cost per ticket has a floor set by the model's output.

### Risks
- Latency of ask-the-data at medium effort; an early spike decides the effort level.
