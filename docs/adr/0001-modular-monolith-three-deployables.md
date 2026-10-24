# ADR-0001: Modular monolith with three deployables

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, system architect, software architect

## Context

The platform has bursty, critical ingestion (payments, telemetry), a staff and customer API, and
background work. Nuru-style teams are small and the Terms of Reference ask for tools that
non-specialists can run and hand over. Money correctness (NFR-05) needs atomicity between modules
that a distributed design would have to rebuild with sagas.

## Decision

One codebase and one repository, organised as thirteen Python modules with hexagonal layers, built
into three deployables: `hub-api`, `hub-ingest` (run as two Deployments, webhooks and telemetry) and
`hub-worker`. Modules talk through `contracts.py` and `events.py` only; import-linter enforces it.
A module becomes a service only when a measured trigger is hit.

## Alternatives Considered

### Microservices from day one
- **Pros**: independent scaling and deployment; clear ownership.
- **Cons**: distributed transactions around the ledger, many moving parts for a small team, harder local environment (NFR-14).
- **Why not**: the cost is paid immediately and the benefit only appears at a scale and team size this project does not have.

### A single deployable
- **Pros**: simplest.
- **Cons**: a telemetry storm shares CPU and connections with the payment path, which NFR-01 depends on.
- **Why not**: workload isolation is cheap here because the same image runs with different entrypoints.

## Consequences

### Positive
- Atomic payment-to-ledger posting in one database transaction.
- One build, one local environment, one set of conventions.
- Clean extraction paths because boundaries are already enforced.

### Negative
- A shared database means a bad query can affect every module; mitigated by per-deployable database roles (ADR-0018).
- Discipline is enforced by tooling, not by the network.

### Risks
- Boundaries erode under deadline pressure. Mitigation: import-linter in CI as a merge gate.
