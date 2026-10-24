# ADR-0005: REST with OpenAPI, and the database schema as the authority for enumerations

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, API designer, database engineer

## Context

Clients are a React app, integrators and machine gateways. The first draft had an API and a schema
that used different names and states for the same things (45 inconsistencies were found in review).

## Decision

REST over JSON, described by OpenAPI 3.1 under `/api/v1`, errors as RFC 9457 `application/problem+json`
with stable codes, cursor pagination, idempotency keys on money operations. `db/schema.sql` is the
authority for every enumeration and lifecycle; an automated test compares the OpenAPI enums with the
schema's CHECK constraints, and `api/permissions.yaml` is the single source of the roles on each
operation, also tested. The frontend client is generated from the specification.

## Alternatives Considered

### GraphQL
- **Pros**: flexible reads for dashboards.
- **Cons**: harder authorization per field, caching and rate limiting; costlier for machine integrators and low-bandwidth clients.
- **Why not**: access control and cacheability matter more here than query flexibility.

### Code-first spec generated from the application
- **Pros**: no drift between code and spec.
- **Cons**: no code exists yet; the specification is the contract being designed.
- **Why not**: revisit once the implementation exists, keeping the alignment tests.

## Consequences

### Positive
- Drift between artefacts fails CI instead of surviving into code.

### Negative
- Two files must change together for a new state, by design.

### Risks
- Teams edit the test's mapping table to silence a failure; review must treat that file as a contract.
