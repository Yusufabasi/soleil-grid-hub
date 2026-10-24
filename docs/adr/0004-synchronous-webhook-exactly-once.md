# ADR-0004: Synchronous webhook processing, exactly-once effect in PostgreSQL

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, system architect, database engineer, security engineer

## Context

Providers retry webhooks for hours and may send duplicates, out-of-order events and forgeries
(FR-03, T-01, T-02). A provider that received a 200 will not retry, so a lost acknowledged event is a
silent loss of money. NFR-02 wants the acknowledgement under 300 ms.

## Decision

The handler verifies the HMAC over `timestamp.event_id.body` (300-second window), checks the payer
number, optionally pull-verifies large amounts, and then in one transaction records the event in
`provider_events`, posts the ledger entries and writes the outbox row, before acknowledging.
Uniqueness is on `(provider, event_id)` and `(provider, provider_txn_id)` and on the posting key, in
PostgreSQL. Invalid signatures go to a quarantine log, not the table. A late confirmation after expiry
or cancel is credited once and flagged `late`; only mismatches go to `suspense`.

## Alternatives Considered

### Acknowledge first, post asynchronously
- **Pros**: fastest acknowledgement.
- **Cons**: a crash after the acknowledgement loses the money until reconciliation.
- **Why not**: the payment path must not depend on a queue being healthy.

### Redis-based deduplication
- **Pros**: very fast.
- **Cons**: a flush or failover reopens double credit.
- **Why not**: Redis is a hint only; constraints decide.

## Consequences

### Positive
- A confirmed payment is in the ledger before the provider sees success; replays and races are rejected by the database.

### Negative
- The acknowledgement latency includes a database commit; the 300 ms target is tested under duplicate bursts.

### Risks
- A slow database slows acknowledgements; ingest has its own pool, database role and connection limits (ADR-0018).
