# ADR-0007: Maker-checker approvals for money and privilege changes

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, security engineer, product planner

## Context

The adversarial review showed a single insider could mint money (a fabricated statement line resolved
into a top-up, or a tariff set near zero) or grant themselves privileges. The Terms of Reference
require the finance lead's sign-off on finance changes.

## Decision

A generic `approvals` resource. An approval records the maker, a hash of the exact proposed payload
and the approver, who must differ from the maker by user and by session. It is required, with a
threshold of zero in v1, for money-posting reconciliation resolutions, manual adjustments and refunds,
tariff changes, role grants, user creation, MFA reset, API-key creation, erasure approval, data export
and KB publication. `match_to_top_up` additionally needs provider evidence obtained by the system. A
conflict check blocks staff from approving money for customers linked to them. The administrator has
no money capability at all.

## Alternatives Considered

### Role-based permission only
- **Pros**: simple.
- **Cons**: one compromised or dishonest account is enough.
- **Why not**: the loss scenarios are unbounded.

### Approval above a monetary threshold
- **Pros**: less friction.
- **Cons**: many small approvals-free actions add up; threshold abuse by splitting.
- **Why not**: start at zero and relax with data.

## Consequences

### Positive
- No single actor can create money or privilege; approvals are auditable and bound to the payload.

### Negative
- Slower finance and admin workflows; a second approver must always be available.

### Risks
- Collusion between two staff members; mitigated by monitoring and the conflict check, not eliminated.
