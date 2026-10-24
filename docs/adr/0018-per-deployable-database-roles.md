# ADR-0018: Per-deployable database roles and definer functions

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, database engineer, security engineer

## Context

One shared application role would let a flaw in the internet-facing ingest pool insert a user role, mint
an API key or post a balanced ledger transaction (adversarial review, S-2).

## Decision

Separate login roles `hub_api`, `hub_ingest` and `hub_worker`, an `audit_writer`, a non-login
`ledger_owner` that owns the `SECURITY DEFINER` functions (`post_ledger_transaction`,
`apply_wallet_balance`, `audit_append`, `erase_customer`) with a pinned `search_path`, a `migrator`, and
analytics login roles per staff function. Table-level grants per role (ingest cannot touch users, roles,
keys or the ledger). Wallet and account tables are not writable directly. PUBLIC loses CONNECT and TEMP.
Row-level security is forced for site and customer scope, and documented honestly as a safety net where
the scope is a session setting.

## Alternatives Considered

### One application role
- **Pros**: simple.
- **Cons**: any code flaw has full write access.
- **Why not**: rejected by the review.

### A separate database per deployable
- **Pros**: strongest isolation.
- **Cons**: breaks atomic posting and joins.
- **Why not**: the ledger and top-ups must share a transaction.

## Consequences

### Positive
- A compromised ingest process cannot create privileges or money.

### Negative
- More grants and functions to maintain; migrations must keep them in step.

### Risks
- A definer function bug runs with elevated rights; each is small, reviewed and tested.
