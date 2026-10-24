# ADR-0008: Layer-7 load balancing with separate pools

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, system architect, SRE

## Context

Payment webhooks are critical and low-volume; telemetry is bulk and sheddable; staff traffic is
interactive; dashboards hold long-lived streams. A single pool lets a telemetry storm starve payments
(NFR-01).

## Decision

HAProxy is the default balancer (nginx is the documented alternative), terminating TLS and routing by
path to four pools: `be_webhooks`, `be_telemetry`, `be_stream` and `be_api`. Stateless replicas, no
sticky sessions. `leastconn` for API pools; active health checks; graceful draining on deploys; retries
only for idempotent requests (and for the ingest pool where the endpoint is idempotent by design);
SSE passes unbuffered with long timeouts. The balancer itself is redundant (VRRP or a cloud load
balancer). Rate limiting happens at the edge and again in the application.

## Alternatives Considered

### One pool for everything
- **Pros**: simplest.
- **Cons**: no isolation between payment and telemetry load.
- **Why not**: NFR-01 depends on isolation.

### Layer-4 balancing only
- **Pros**: lower overhead.
- **Cons**: no path routing, header handling, or SSE tuning.
- **Why not**: the pools are chosen by path.

## Consequences

### Positive
- Telemetry can be shed or scaled without touching payments.

### Negative
- More configuration to keep consistent across the balancer, the compose file and the documentation (a review found drift here).

### Risks
- A path-routing mistake sends a webhook to the wrong middleware stack; a routing test runs in CI.
