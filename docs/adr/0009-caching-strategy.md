# ADR-0009: Cache-aside with stampede control, and what is never cached

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, system architect

## Context

The dashboard is read-heavy and every staff member loads similar data (FR-13, NFR-02). Some data
must never be stale (balances used in decisions, authorization).

## Decision

Cache-aside in Redis for read models (dashboard summary, site status, KB retrieval results, plan and
tariff lookups), with TTL jitter, a single-flight lock, and stale-while-revalidate for the dashboard.
Invalidation is event-driven from domain events plus a short TTL as a backstop. Never cached: a wallet
balance used for a money decision, authorization decisions beyond the token's life, and anything
personal that erasure must reach. Every Redis use has a defined failure behaviour (cache miss falls
through to the database with a concurrency limit; rate limits fail safe to a local limiter; Redis is
never the only copy of a fact).

## Alternatives Considered

### Write-through caching
- **Pros**: fewer stale reads.
- **Cons**: couples every write path to the cache.
- **Why not**: read models are rebuilt from events cheaply.

### No cache, materialised views only
- **Pros**: one less system.
- **Cons**: concurrent dashboard loads hit the database.
- **Why not**: 200 concurrent users on cold data would otherwise be a constant load on the primary's replica.

## Consequences

### Positive
- Dashboards meet the 500 ms target from cache; a Redis loss degrades speed, not correctness.

### Negative
- Stale-data windows must be shown to users (a stale banner).

### Risks
- A stampede after a flush; mitigated by single-flight and a database concurrency cap.
