# ADR-0003: Transactional outbox with Redis Streams

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, system architect

## Context

Business changes must reliably trigger notifications, token issuance and alerts (FR-05, FR-19), with
at-least-once delivery and no lost events. Dual writes to a database and a broker lose events.

## Decision

Events are written to an `outbox` table in the same transaction as the business change. A relay
publishes them to Redis Streams; consumers run in consumer groups and dedupe in `consumer_inbox`.
The outbox is the source of truth: a replay window of ten minutes plus business sweepers recover from
Redis loss or failover. Uniqueness and exactly-once effects live in PostgreSQL constraints, never in
Redis. Dead-letter streams are named `dlq.<stream>`.

## Alternatives Considered

### Kafka
- **Pros**: durable log, replay, scale.
- **Cons**: heavy to operate; far beyond the event rate (tens per second).
- **Why not**: operational cost without a matching need.

### RabbitMQ or a cloud queue
- **Pros**: mature routing and acknowledgement.
- **Cons**: another stateful system, or a cloud lock-in against NFR-12.
- **Why not**: Redis is already required for caching and rate limits.

### Polling the outbox directly (no broker)
- **Pros**: fewest components.
- **Cons**: latency and database load grow with consumers.
- **Why not**: acceptable as the fallback path, but Streams give consumer groups and back-pressure cheaply.

## Consequences

### Positive
- No event is lost when the broker is; consumers are simple and idempotent.

### Negative
- Redis becomes part of the delivery path and must be monitored; consumers must tolerate duplicates.

### Risks
- A Redis failover can drop acknowledged stream entries; the replay window and sweepers exist for exactly this.
