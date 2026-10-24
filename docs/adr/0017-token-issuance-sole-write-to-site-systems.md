# ADR-0017: Energy-credit token issuance is the only write to site systems

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, security engineer, operations lead (sign-off to be recorded)

## Context

The Terms of Reference make access to power-site control systems read-only by default. Delivering paid
credit to a meter is a write to a site system.

## Decision

Token issuance is the single, explicitly approved write command. A token is bound to a meter id, a
monotonic counter and an expiry, always references a ledger posting, and is resent with the same id so
the meter applies it at most once. There is no remote disconnect and no remote configuration in v1.
Delivery is pushed through a gateway adapter and acknowledged through a gateway-scoped endpoint.

## Alternatives Considered

### Meters pull their credit
- **Pros**: the hub never writes.
- **Cons**: needs firmware support and polling; more states to reconcile.
- **Why not**: revisit with real firmware.

### Allow general control commands
- **Pros**: operational convenience.
- **Cons**: a large attack surface onto physical equipment.
- **Why not**: not needed for the product and contrary to the governance rule.

## Consequences

### Positive
- A narrow, auditable, replay-resistant write path.

### Negative
- Some operational actions remain manual.

### Risks
- A rogue gateway acknowledges falsely; reconciliation compares acknowledgements with meter credit registers.
