# ADR-0014: Erasure by pseudonymisation, with a single retention table

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, database engineer, security engineer, product planner

## Context

Erasure rights (FR-20, NFR-07) conflict with accounting retention. Ledger rows cannot be deleted and
must stay balanced.

## Decision

Erasure irreversibly pseudonymises personal fields in place (name, phone, email, ticket free text and
related copies in the outbox, idempotency responses, audit addresses, provider-event metadata and
stored prompts); ledger rows remain, attached to a pseudonymous id. It is blocked while the wallet is
non-zero or tokens are unacknowledged, needs a seven-day cooling-off period with a notice to the old
contact, a data-protection administrator and a second approver. Every erasure is written to an
erasure log that is replayed after any restore. Retention is one table in the data model: telemetry
90 days hot then 13 months archived then aggregates; tickets 3 years after closure; AI audit metadata 13
months and redacted prompt payloads 90 days; provider events 13 months; ledger 10 years; audit log 7
years; backups 35 days plus 12 monthly. Legal sign-off is an open item.

## Alternatives Considered

### Crypto-shredding with per-customer keys
- **Pros**: erases backups by destroying a key.
- **Cons**: key management at scale, harder queries and restores.
- **Why not**: deferred; the erasure-log replay covers backups in v1.

### Delete rows
- **Pros**: simple to explain.
- **Cons**: breaks the ledger and foreign keys.
- **Why not**: not possible without destroying financial records.

## Consequences

### Positive
- Financial integrity and erasure coexist; restores cannot resurrect erased people.

### Negative
- The restore procedure has an extra mandatory step.

### Risks
- A retention period is legally wrong; the table is data, changed in one place.
