# CR-001: `ledger_owner` cannot read `hub.staff_customer_link`, so every manual posting fails

- **Slice and task:** 03 (ledger core), Tasks 03.5 and 03.6 — the adjustment, refund and period
  approvals.
- **Frozen file involved:** `db/schema.sql` (grants block, "Definer owners hold only what their
  functions need", around line 2468).

## What I expected

`POST /ledger/adjustments` proposes an approval; a second finance lead approves it; the executor
posts the approved lines through `hub.post_ledger_transaction`, which marks the approval
`consumed`. The slice's review focus 2 ("approval replay and payload swap") and the whole
maker-checker path of FR-04 depend on that working.

## What I found

```
django.db.utils.ProgrammingError: permission denied for table staff_customer_link
CONTEXT:  SQL expression "NEW.status IN ('approved','consumed') AND NEW.subject_customer_id IS NOT NULL AND EXISTS (
           SELECT 1 FROM hub.staff_customer_link l
           WHERE l.customer_id = NEW.subject_customer_id AND l.user_id IN (NEW.approver_user_id, NEW.maker_user_id))"
  PL/pgSQL function approvals_guard() line 17 at IF
  SQL statement "UPDATE hub.approvals SET status = 'consumed', consumed_at = now() WHERE id = p_approval_id"
  PL/pgSQL function reverse_ledger_transaction(uuid,text,uuid,uuid) line 67 at SQL statement
```

`hub.approvals_guard` is SECURITY INVOKER, so it runs as the role that updated the row. The
ledger functions are SECURITY DEFINER as `ledger_owner`, and the baseline grants that owner:

```
GRANT SELECT ON hub.approvals TO ledger_owner;
GRANT SELECT ON hub.top_ups, hub.provider_events, hub.accounting_periods, hub.customers TO ledger_owner;
```

but not `hub.staff_customer_link`, which the guard's own query reads. Any posting that consumes
an approval therefore fails — adjustment, refund, and every reconciliation fix of slice 14 — and
the failure is a 500, not a refusal the client can read.

## Smallest change I propose

Add one grant to the baseline block:

```sql
GRANT SELECT ON hub.staff_customer_link TO ledger_owner;
```

No write privilege, no other table, no change to the guard. It matches what `ledger_owner`
already holds on `hub.customers` (the same guard-adjacent read).

## What I did meanwhile

`db/migrations/0003_ledger_owner_conflict_guard_grant.sql` (plus its `hubdb` wrapper) applies the
same grant, which is the additive route the executor guide allows for a missing privilege. It is
idempotent, so folding the grant into `db/schema.sql` makes the migration a no-op rather than a
conflict. Recorded under "Open change requests" in `docs/plan/progress.md`.

## Requirements and tests touched

FR-04 (AC-FR-04-2: a manual adjustment needs a second approver), FR-14 (D-40 period lock through
maker-checker), TC-FR-04-1, TC-FR-04-4, TC-FR-04-5. Affected tests:
`tests/integration/ledger/test_adjustment_flow.py` (all), `test_periods.py::test_lock_needs_second_finance_lead`,
`test_concurrency.py::test_two_wallet_postings_do_not_deadlock`.
