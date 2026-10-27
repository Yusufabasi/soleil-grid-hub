"""The ledger's read path: balances, statements, transactions and periods.

A balance is read, never derived by the caller: a wallet from ``wallet_balances`` (the row the
posting trigger maintains), a system account from its latest snapshot plus the entries written
after it. Both are one or two indexed lookups, which matters because the wallet endpoint is on
the customer portal's hot path and the finance dashboard reads every system account.

Amounts leave this module signed the way the database stores them (debit positive); turning
them into ``direction`` plus a positive :class:`~hub.ledger.contracts.Money` is the API
serializer's job, because it is a presentation rule (D-03), not a storage one.

``statement`` pages by ``id``: ids are UUIDv7, so an id cursor is time ordered and stable while
new rows arrive (a page cannot skip or repeat a line), and ``ix_ledger_entries_account``
covers the predicate. Transactions page on ``(effective_at, id)`` descending, because finance
reads them by accounting date rather than by insertion order.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import connections

from hub.ledger.contracts import AccountRef, AccountView, EntryView, TransactionView, WalletView
from hub.ledger.infrastructure import queries
from hub.shared.clock import current

__all__ = [
    "ZERO_UUID",
    "AccountingPeriodRow",
    "AccountRow",
    "LedgerReader",
    "TransactionRow",
]

ZERO_UUID = UUID(int=0)

PERIOD_COLUMNS = "id, period_start, period_end, status, locked_by, locked_at"


class AccountRow:
    """A row of the ``ACCOUNT_COLUMNS`` projection."""

    __slots__ = ("code", "created_at", "currency", "customer_id", "id", "kind")

    def __init__(self, values: Sequence[Any]) -> None:
        (self.id, self.code, self.kind, self.currency, self.customer_id, self.created_at) = values


class TransactionRow:
    """A row of the ``TRANSACTION_COLUMNS`` projection."""

    __slots__ = (
        "approval_id",
        "created_at",
        "description",
        "effective_at",
        "id",
        "kind",
        "posting_key",
        "reverses_transaction_id",
        "source_top_up_id",
    )

    def __init__(self, values: Sequence[Any]) -> None:
        (
            self.id,
            self.posting_key,
            self.kind,
            self.description,
            self.effective_at,
            self.created_at,
            _created_by,
            self.approval_id,
            self.source_top_up_id,
            self.reverses_transaction_id,
        ) = values


class AccountingPeriodRow:
    """A row of ``hub.accounting_periods``."""

    __slots__ = ("id", "locked_at", "locked_by", "period_end", "period_start", "status")

    def __init__(self, values: Sequence[Any]) -> None:
        (
            self.id,
            self.period_start,
            self.period_end,
            self.status,
            self.locked_by,
            self.locked_at,
        ) = values


class LedgerReader:
    """Every read the ledger's use cases, jobs and views need."""

    def __init__(self, *, using: str = "default") -> None:
        self._using = using

    # --- accounts -----------------------------------------------------------------------
    def account_id(self, ref: AccountRef) -> UUID | None:
        """The id an :class:`AccountRef` names, or ``None`` when it was never created."""
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                f"SELECT {queries.ACCOUNT_COLUMNS} FROM hub.ledger_accounts WHERE code = %s",  # noqa: S608
                [ref.code],
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def account(self, account_id: UUID) -> AccountView | None:
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                f"SELECT {queries.ACCOUNT_COLUMNS} FROM hub.ledger_accounts WHERE id = %s",  # noqa: S608
                [account_id],
            )
            row = cursor.fetchone()
            if row is None:
                return None
            account = AccountRow(row)
            balance, as_of = self._balance(cursor, account)
        return AccountView(
            id=account.id,
            code=account.code,
            kind=account.kind,
            currency=account.currency,
            customer_id=account.customer_id,
            balance_minor=balance,
            balance_as_of=as_of,
            created_at=account.created_at,
        )

    def list_accounts(
        self,
        *,
        kinds: Sequence[str] = (),
        currency: str | None = None,
        customer_id: UUID | None = None,
        after: UUID | None = None,
        limit: int = 25,
    ) -> list[AccountView]:
        clauses = ["TRUE"]
        params: list[Any] = []
        if kinds:
            clauses.append("kind = ANY(%s)")
            params.append(list(kinds))
        if currency:
            clauses.append("currency = %s")
            params.append(currency)
        if customer_id is not None:
            clauses.append("customer_id = %s")
            params.append(customer_id)
        if after is not None:
            clauses.append("id > %s")
            params.append(after)
        params.append(limit)
        sql = (
            f"SELECT {queries.ACCOUNT_COLUMNS} FROM hub.ledger_accounts"  # noqa: S608
            f" WHERE {' AND '.join(clauses)} ORDER BY id LIMIT %s"
        )
        with connections[self._using].cursor() as cursor:
            cursor.execute(sql, params)
            accounts = [AccountRow(row) for row in cursor.fetchall()]
            documents = []
            for account in accounts:
                balance, as_of = self._balance(cursor, account)
                documents.append(
                    AccountView(
                        id=account.id,
                        code=account.code,
                        kind=account.kind,
                        currency=account.currency,
                        customer_id=account.customer_id,
                        balance_minor=balance,
                        balance_as_of=as_of,
                        created_at=account.created_at,
                    )
                )
        return documents

    def wallet(self, customer_id: UUID, currency: str) -> WalletView:
        """The wallet document, with a zero balance when the wallet was never opened."""
        ref = AccountRef(
            kind="customer_wallet",
            currency=currency,  # type: ignore[arg-type]
            customer_id=customer_id,
        )
        account_id = self.account_id(ref)
        with connections[self._using].cursor() as cursor:
            if account_id is None:
                balance, as_of = 0, current()
            else:
                cursor.execute(queries.WALLET_BALANCE, [account_id])
                row = cursor.fetchone()
                balance = int(row[0]) if row else 0
                as_of = row[1] if row else current()
            pending = self._pending_top_ups(cursor, customer_id)
        return WalletView(
            customer_id=customer_id,
            ledger_account_id=account_id,
            balance_minor=balance,
            currency=currency,
            as_of=as_of,
            pending_top_ups=pending,
        )

    def balance_minor(self, account_id: UUID) -> int:
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                f"SELECT {queries.ACCOUNT_COLUMNS} FROM hub.ledger_accounts WHERE id = %s",  # noqa: S608
                [account_id],
            )
            row = cursor.fetchone()
            if row is None:
                return 0
            return self._balance(cursor, AccountRow(row))[0]

    def system_balance(self, account_id: UUID) -> tuple[int, int]:
        """``(balance_minor, entry_count)`` of a system account: snapshot plus later entries."""
        with connections[self._using].cursor() as cursor:
            return self._system_balance(cursor, account_id)

    # --- entries and transactions -------------------------------------------------------
    def statement(
        self, account_id: UUID, *, after: UUID | None = None, limit: int = 25
    ) -> list[EntryView]:
        with connections[self._using].cursor() as cursor:
            cursor.execute(queries.ENTRIES_AFTER, [account_id, after or ZERO_UUID, limit])
            return [self._entry(row) for row in cursor.fetchall()]

    def transaction(self, transaction_id: UUID) -> TransactionView | None:
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                f"SELECT {queries.TRANSACTION_COLUMNS} FROM hub.ledger_transactions WHERE id = %s",  # noqa: S608
                [transaction_id],
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._transaction(cursor, TransactionRow(row))

    def list_transactions(
        self,
        *,
        account_id: UUID | None = None,
        kinds: Sequence[str] = (),
        source_id: UUID | None = None,
        posting_key: str | None = None,
        effective_after: datetime | None = None,
        effective_before: datetime | None = None,
        after: tuple[datetime, UUID] | None = None,
        limit: int = 25,
    ) -> list[TransactionView]:
        clauses = ["TRUE"]
        params: list[Any] = []
        if account_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM hub.ledger_entries e"
                " WHERE e.transaction_id = t.id AND e.account_id = %s)"
            )
            params.append(account_id)
        if kinds:
            clauses.append("t.kind = ANY(%s)")
            params.append(list(kinds))
        if source_id is not None:
            clauses.append("(t.source_top_up_id = %s OR t.reverses_transaction_id = %s)")
            params.extend([source_id, source_id])
        if posting_key:
            clauses.append("t.posting_key = %s")
            params.append(posting_key)
        if effective_after is not None:
            clauses.append("t.effective_at > %s")
            params.append(effective_after)
        if effective_before is not None:
            clauses.append("t.effective_at < %s")
            params.append(effective_before)
        if after is not None:
            clauses.append("(t.effective_at, t.id) < (%s, %s)")
            params.extend([after[0], after[1]])
        params.append(limit)
        sql = (
            f"SELECT {queries.TRANSACTION_COLUMNS} FROM hub.ledger_transactions t"  # noqa: S608
            f" WHERE {' AND '.join(clauses)} ORDER BY t.effective_at DESC, t.id DESC LIMIT %s"
        )
        with connections[self._using].cursor() as cursor:
            cursor.execute(sql, params)
            rows = [TransactionRow(row) for row in cursor.fetchall()]
            return [self._transaction(cursor, row) for row in rows]

    # --- periods ------------------------------------------------------------------------
    def period(self, period_id: UUID) -> AccountingPeriodRow | None:
        with connections[self._using].cursor() as cursor:
            return self._period(cursor, period_id)

    def find_period_for(self, on: date) -> AccountingPeriodRow | None:
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                f"SELECT {PERIOD_COLUMNS} FROM hub.accounting_periods"  # noqa: S608
                " WHERE period_start <= %s AND period_end >= %s"
                " ORDER BY period_start DESC LIMIT 1",
                [on, on],
            )
            row = cursor.fetchone()
        return AccountingPeriodRow(row) if row else None

    def list_periods(
        self, *, statuses: Sequence[str] = (), after: UUID | None = None, limit: int = 25
    ) -> list[AccountingPeriodRow]:
        clauses = ["TRUE"]
        params: list[Any] = []
        if statuses:
            clauses.append("status = ANY(%s)")
            params.append(list(statuses))
        if after is not None:
            clauses.append("id > %s")
            params.append(after)
        params.append(limit)
        sql = (
            f"SELECT {PERIOD_COLUMNS} FROM hub.accounting_periods"  # noqa: S608
            f" WHERE {' AND '.join(clauses)} ORDER BY id LIMIT %s"
        )
        with connections[self._using].cursor() as cursor:
            cursor.execute(sql, params)
            return [AccountingPeriodRow(row) for row in cursor.fetchall()]

    def period_is_locked(self, on: date) -> bool:
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM hub.accounting_periods"
                " WHERE status = 'locked' AND period_start <= %s AND period_end >= %s)",
                [on, on],
            )
            row = cursor.fetchone()
        return bool(row[0]) if row else False

    # --- integrity ----------------------------------------------------------------------
    def drift(self) -> list[dict[str, Any]]:
        with connections[self._using].cursor() as cursor:
            cursor.execute(queries.DRIFT)
            return [
                {
                    "account_id": str(row[0]),
                    "maintained_minor": int(row[1]),
                    "derived_minor": int(row[2]),
                }
                for row in cursor.fetchall()
            ]

    def imbalances(self) -> list[dict[str, Any]]:
        with connections[self._using].cursor() as cursor:
            cursor.execute(queries.IMBALANCE_BY_TRANSACTION)
            found: list[dict[str, Any]] = [
                {
                    "check": "unbalanced_transaction",
                    "transaction_id": str(row[0]),
                    "currency": str(row[1]),
                    "total_minor": int(row[2]),
                    "lines": int(row[3]),
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(queries.TOPUPS_PER_SOURCE)
            found.extend(
                {
                    "check": "duplicate_topup_posting",
                    "source_top_up_id": str(row[0]),
                    "postings": int(row[1]),
                }
                for row in cursor.fetchall()
            )
        return found

    # --- internals ----------------------------------------------------------------------
    def _balance(self, cursor: Any, account: AccountRow) -> tuple[int, datetime]:
        if account.kind == "customer_wallet":
            cursor.execute(queries.WALLET_BALANCE, [account.id])
            row = cursor.fetchone()
            if row is None:
                return 0, account.created_at
            return int(row[0]), row[1]
        balance, _count = self._system_balance(cursor, account.id)
        return balance, current()

    def _system_balance(self, cursor: Any, account_id: UUID) -> tuple[int, int]:
        """The latest snapshot plus the entries written after the day it covers."""
        cursor.execute(queries.SYSTEM_BALANCE_SNAPSHOT, [account_id])
        snapshot = cursor.fetchone()
        base, entries, since = 0, 0, datetime(1970, 1, 1, tzinfo=UTC)
        if snapshot is not None:
            base, entries = int(snapshot[0]), int(snapshot[1])
            snapshot_day: date = snapshot[2]
            # The snapshot includes its own day (entries created before its end), so the delta
            # starts at the next midnight UTC.
            since = datetime(
                snapshot_day.year, snapshot_day.month, snapshot_day.day, tzinfo=UTC
            ) + timedelta(days=1)
        cursor.execute(queries.SYSTEM_BALANCE_DELTA, [account_id, since])
        delta, delta_count = cursor.fetchone()
        return base + int(delta), entries + int(delta_count)

    def _pending_top_ups(self, cursor: Any, customer_id: UUID) -> int:
        cursor.execute(
            "SELECT count(*) FROM hub.top_ups WHERE customer_id = %s"
            " AND status IN ('pending','initiated','confirming')",
            [customer_id],
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def _entry(self, row: Sequence[Any]) -> EntryView:
        amount = int(row[4])
        return EntryView(
            id=row[0],
            transaction_id=row[1],
            account_id=row[2],
            currency=str(row[3]),
            direction="debit" if amount > 0 else "credit",
            amount_minor=abs(amount),
            created_at=row[5],
        )

    def _transaction(self, cursor: Any, row: TransactionRow) -> TransactionView:
        cursor.execute(
            "SELECT id, transaction_id, account_id, currency, amount_minor, created_at"
            "  FROM hub.ledger_entries WHERE transaction_id = %s ORDER BY account_id",
            [row.id],
        )
        lines = [self._entry(line) for line in cursor.fetchall()]
        if row.reverses_transaction_id is not None:
            source_type, source_id = "ledger_transaction", row.reverses_transaction_id
        elif row.source_top_up_id is not None:
            source_type, source_id = "top_up", row.source_top_up_id
        elif row.approval_id is not None:
            source_type, source_id = "approval", row.approval_id
        else:
            source_type, source_id = "system", None
        return TransactionView(
            id=row.id,
            posting_key=row.posting_key,
            kind=row.kind,
            description=row.description,
            effective_at=row.effective_at,
            source_type=source_type,
            source_id=source_id,
            reverses_transaction_id=row.reverses_transaction_id,
            lines=lines,
        )

    def _period(self, cursor: Any, period_id: UUID) -> AccountingPeriodRow | None:
        cursor.execute(
            f"SELECT {PERIOD_COLUMNS} FROM hub.accounting_periods WHERE id = %s",  # noqa: S608
            [period_id],
        )
        row = cursor.fetchone()
        return AccountingPeriodRow(row) if row else None
