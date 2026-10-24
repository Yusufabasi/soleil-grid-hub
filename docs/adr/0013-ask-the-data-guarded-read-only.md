# ADR-0013: Ask-the-data through guarded, read-only, per-role analytics

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, AI engineer, database engineer, security engineer

## Context

Staff want plain-language questions answered from the data (FR-12). Model-written SQL is untrusted and
must not read forbidden data, write, or run unbounded.

## Decision

Three independent walls. The guard parses the generated SQL, accepts one SELECT over the role's allowed
views only, rejects unknown functions, and executes the text re-rendered from the parse tree. The
connection uses a per-role, read-only login role (`analytics_support`, `analytics_finance`,
`analytics_ops`) on the replica with a 5-second statement timeout and connection limits set on the login
role itself. The database engine denies everything outside the role's views. Views are aggregate-first;
finance gets pseudonymous money rows; no phone digits. Query rows never go to the model. The
administrator has no ask-the-data. Time budget: 3 s model, 5 s query, 8 s outer.

## Alternatives Considered

### Let the model call a tool that returns rows
- **Pros**: richer answers.
- **Cons**: results become model input and a leakage and injection path.
- **Why not**: rejected for v1; the explanation describes the SQL only.

### Site-scoped row-level security in analytics
- **Pros**: finer isolation.
- **Cons**: a session setting the model's SQL could change is not a boundary; views run as owner.
- **Why not**: accepted residual risk, because analytics reads aggregated and pseudonymous data; the main API enforces site scope.

## Consequences

### Positive
- A guard bypass still meets the read-only role and the engine's grants.

### Negative
- Small-group inference from aggregates remains possible and is documented as residual risk.

### Risks
- Latency with a model that cannot disable thinking; measured by an early spike with fallbacks defined.
