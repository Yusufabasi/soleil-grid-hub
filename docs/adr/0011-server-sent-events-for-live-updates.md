# ADR-0011: Server-Sent Events for the live dashboard

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, system architect, UX architect, security engineer

## Context

Operations need near-real-time site status and alerts (FR-13) over 3G connections, for about 200
concurrent staff. Customers need payment status but there may be 300,000 of them.

## Decision

SSE for staff dashboards only. A single-use, 30-second stream ticket (bound to the caller's IP) is
requested with `POST /dashboard/stream-tickets` and used as `GET /dashboard/stream?ticket=`, because a
browser's EventSource cannot set headers; the balancer log format removes the ticket. A stream lives at
most ten minutes, then the client requests a new ticket; the role and token version are re-checked on
every heartbeat. Events are fanned out from Redis pub/sub; a resync event tells clients to reload the
snapshot. Customers poll payment status with backoff instead of holding streams.

## Alternatives Considered

### WebSockets
- **Pros**: bidirectional.
- **Cons**: more proxy and authentication complexity; nothing here needs client-to-server push.
- **Why not**: SSE is simpler, works through proxies and reconnects natively.

### Polling only
- **Pros**: simplest.
- **Cons**: either stale or expensive for dashboards.
- **Why not**: used for customers, where it is cheaper than streams.

## Consequences

### Positive
- Cheap live updates; automatic reconnect; limited blast radius for a leaked ticket.

### Negative
- Tickets in a query string need log hygiene; the ten-minute cap adds reconnect churn.

### Risks
- Streams outliving a role change; mitigated by per-heartbeat re-checks.
