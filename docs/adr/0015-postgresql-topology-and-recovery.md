# ADR-0015: PostgreSQL topology and recovery targets

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, system architect, database engineer, SRE

## Context

NFR-04 wants RPO of at most 5 minutes and RTO of at most 30 minutes; NFR-01 wants 99.9% on the payment
path. Promoting an asynchronous replica can lose committed confirmations the provider already saw
acknowledged. A restore at 400 to 500 GB takes 45 to 90 minutes.

## Decision

A primary, a synchronous HA standby and an asynchronous read replica (analytics and reads). Ledger
transactions commit synchronously. The standby is required from the 30,000-customer tier; at 3,000
customers promoting the replica is accepted with a documented RPO of seconds. RTO is 30 minutes for
instance and zone failure and 2 hours for logical corruption or region loss. Telemetry leaves the
database at its trigger (ADR-0010), which also shortens restores. Managed services are the default, with
a self-hosted path tested yearly as an exit plan (NFR-12).

## Alternatives Considered

### One primary and one asynchronous replica
- **Pros**: cheapest.
- **Cons**: no safe failover target for money.
- **Why not**: the contract's original topology; replaced.

### Multi-region active-active
- **Pros**: region failure tolerance.
- **Cons**: conflict handling for a ledger; cost and complexity.
- **Why not**: out of proportion to the targets; a warm standby in another region is optional.

## Consequences

### Positive
- No committed ledger transaction is lost on instance or zone failure.

### Negative
- Synchronous commit adds write latency; measured under the payday load test.

### Risks
- The standby is lost together with the primary; quarterly restore drills test recovery from backups.
