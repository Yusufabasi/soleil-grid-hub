# ADR-0002: PostgreSQL as system of record, with a double-entry ledger

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, database engineer

## Context

Money must be exact, auditable and impossible to double-credit (NFR-05, FR-04). Staff also need
relational queries, and ask-the-data needs a database a read-only role can be confined to.

## Decision

PostgreSQL 16 holds everything of record. Money is stored as integer minor units plus an ISO-4217
currency. The ledger is double-entry: a transaction's entries sum to zero per currency, enforced by
a deferred constraint trigger; entries and transactions are immutable (corrections are reversing
postings); `amount_minor > 0` is a debit and a wallet balance is the negative of the sum of its
entries. Application roles cannot write ledger tables directly: they call a `SECURITY DEFINER`
posting function.

## Alternatives Considered

### A dedicated ledger database (for example TigerBeetle)
- **Pros**: purpose-built for double-entry and throughput.
- **Cons**: a second datastore to operate, back up and secure; no transactional join with top-ups and outbox rows.
- **Why not**: peak load is about 30 postings per second, far inside PostgreSQL's capability, and atomicity with the business row matters more than throughput.

### A document store with application-level balance checks
- **Pros**: flexible schema.
- **Cons**: invariants live in code that every path must remember.
- **Why not**: the invariants must hold even when application code is wrong, which only database constraints give.

## Consequences

### Positive
- Invariants are tested directly in the database, including concurrent double-credit attempts.
- One backup and recovery story.

### Negative
- Ledger growth (about 72 million entries a year at target scale) needs monitoring and, eventually, archiving.
- Definer functions need careful `search_path` and ownership review.

### Risks
- A superuser or migrator can still alter history; mitigated by `ENABLE ALWAYS` triggers, an event alert on trigger changes and the audit chain.
