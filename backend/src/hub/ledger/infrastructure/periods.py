"""The narrow repository for ``hub.accounting_periods`` (D-23).

A period is a calendar month with two states, and it is the one ledger table an application
role may write directly: ``hub_api`` holds INSERT and UPDATE on ``accounting_periods`` because
locking is a status change, not a posting. Everything else about it is a rule the executor
enforces (the approver's ``finance_lead`` attribute, the open-exception count), and the posting
guard in SQL enforces the consequence (a posting dated inside a locked period is refused).

``ensure_for`` is lazy on purpose: a period is created the first time somebody asks about a
month, so a fresh deployment has no backfill and a future month locks the same way a past one
does. The unique ``period_start`` makes the race between two requests harmless.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

from django.db import connections

from hub.ledger.infrastructure.reader import PERIOD_COLUMNS, AccountingPeriodRow

__all__ = ["AccountingPeriodRepository", "month_bounds"]


def month_bounds(on: date) -> tuple[date, date]:
    """The first and last day of ``on``'s month."""
    first = on.replace(day=1)
    next_first = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return first, next_first - timedelta(days=1)


class AccountingPeriodRepository:
    """Read and change periods; the status change is what a lock approval executes."""

    def __init__(self, *, using: str = "default") -> None:
        self._using = using

    def ensure_for(self, on: date, *, new_id: UUID) -> AccountingPeriodRow:
        """Return the period containing ``on``, creating its month if nobody has yet."""
        start, end = month_bounds(on)
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.accounting_periods (id, period_start, period_end, status)"
                " VALUES (%s, %s, %s, 'open')"
                " ON CONFLICT (period_start) DO NOTHING",
                [new_id, start, end],
            )
            cursor.execute(
                f"SELECT {PERIOD_COLUMNS} FROM hub.accounting_periods WHERE period_start = %s",  # noqa: S608
                [start],
            )
            row = cursor.fetchone()
        if row is None:  # pragma: no cover - the insert above guarantees it exists
            raise RuntimeError("accounting period disappeared between insert and select")
        return AccountingPeriodRow(row)

    def lock(self, period_id: UUID, *, approver_id: UUID, at: datetime) -> bool:
        """Lock an open period; returns False when it was already locked (a replay)."""
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                "UPDATE hub.accounting_periods"
                "   SET status = 'locked', locked_by = %s, locked_at = %s"
                " WHERE id = %s AND status = 'open'",
                [approver_id, at, period_id],
            )
            return int(cursor.rowcount) == 1

    def reopen(self, period_id: UUID) -> bool:
        """Reopen a locked period; the schema's CHECK requires both lock columns to be null."""
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                "UPDATE hub.accounting_periods"
                "   SET status = 'open', locked_by = NULL, locked_at = NULL"
                " WHERE id = %s AND status = 'locked'",
                [period_id],
            )
            return int(cursor.rowcount) == 1
