"""The ledger's read SQL, in one place, so a slow query has one home to be fixed in.

Every statement here is a *query*: none of them changes a row. The two integrity checks
(``v_wallet_balance_drift`` and the per-transaction sum) are the nightly jobs' whole body, and
the balance of a system account is computed from its latest snapshot plus the entries written
after it — never by folding the whole history, which is what would make the finance dashboard
slower every month (docs/04 section 5).
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ACCOUNT_COLUMNS",
    "ACCOUNTS_BY_KIND_AND_CURRENCY",
    "DRIFT",
    "ENTRIES_AFTER",
    "IMBALANCE_BY_TRANSACTION",
    "SYSTEM_BALANCE_DELTA",
    "SYSTEM_BALANCE_SNAPSHOT",
    "TOPUPS_PER_SOURCE",
    "TRANSACTION_COLUMNS",
    "WALLET_BALANCE",
]

ACCOUNT_COLUMNS: Final = "id, code, kind, currency, customer_id, created_at"

TRANSACTION_COLUMNS: Final = (
    "id, posting_key, kind, description, effective_at, created_at, "
    "created_by_user_id, approval_id, source_top_up_id, reverses_transaction_id"
)

# --- balances -------------------------------------------------------------------------------
WALLET_BALANCE: Final = (
    "SELECT balance_minor, updated_at FROM hub.wallet_balances WHERE account_id = %s"
)

SYSTEM_BALANCE_SNAPSHOT: Final = (
    "SELECT balance_minor, entry_count, snapshot_date"
    "  FROM hub.ledger_balance_snapshots"
    " WHERE account_id = %s"
    " ORDER BY snapshot_date DESC"
    " LIMIT 1"
)

# Entries written after the snapshot's day (the snapshot covers everything up to and including it).
SYSTEM_BALANCE_DELTA: Final = (
    "SELECT coalesce(sum(amount_minor), 0), count(*)"
    "  FROM hub.ledger_entries"
    " WHERE account_id = %s AND created_at >= %s"
)

ENTRIES_AFTER: Final = (
    "SELECT id, transaction_id, account_id, currency, amount_minor, created_at"
    "  FROM hub.ledger_entries"
    " WHERE account_id = %s AND id > %s"
    " ORDER BY id"
    " LIMIT %s"
)

ACCOUNTS_BY_KIND_AND_CURRENCY: Final = (
    f"SELECT {ACCOUNT_COLUMNS} FROM hub.ledger_accounts WHERE kind = %s AND currency = %s"  # noqa: S608
    " AND customer_id IS NULL ORDER BY code"
)

# --- integrity ------------------------------------------------------------------------------
# Q: nightly drift check. `v_wallet_balance_drift` is the maintained-vs-derived comparison of the
# schema, so the job reads the same definition the invariant test reads.
DRIFT: Final = "SELECT account_id, maintained_minor, derived_minor FROM hub.v_wallet_balance_drift ORDER BY account_id"

# Q: a transaction whose entries do not sum to zero in one currency. The deferred constraint
# trigger makes this unrepresentable; the job proves it stayed that way.
IMBALANCE_BY_TRANSACTION: Final = (
    "SELECT transaction_id, currency, sum(amount_minor) AS total, count(*) AS lines"
    "  FROM hub.ledger_entries"
    " GROUP BY transaction_id, currency"
    " HAVING sum(amount_minor) <> 0"
    " ORDER BY transaction_id"
)

# Q: at most one `topup_received` posting per top-up (a second credit for one payment).
TOPUPS_PER_SOURCE: Final = (
    "SELECT source_top_up_id, count(*) AS postings"
    "  FROM hub.ledger_transactions"
    " WHERE kind = 'topup_received' AND source_top_up_id IS NOT NULL"
    " GROUP BY source_top_up_id"
    " HAVING count(*) > 1"
)
