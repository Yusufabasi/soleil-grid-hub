"""The rows behind maker-checker: ``hub.approvals`` and ``hub.staff_customer_link``.

The database is the second line of defence here, not the first. Its guard trigger recomputes
``payload_hash`` on insert, refuses any update that would change the payload, action, maker or
subject, and raises ``restrict_violation`` when the maker or approver is linked to the customer.
The service checks all of those before it writes, so a client gets a problem code instead of a
500 — but the trigger is what makes the guarantee true for every writer, including a future one.

``payload`` is jsonb, which this driver hands back as text: the mapping this module returns has
already parsed it, so the service never works with a string.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import connection
from psycopg.types.json import Jsonb

__all__ = ["ApprovalRepository"]

_COLUMN_NAMES = (
    "id",
    "action",
    "subject_customer_id",
    "maker_user_id",
    "maker_session_id",
    "payload",
    "payload_hash",
    "status",
    "approver_user_id",
    "approver_session_id",
    "decided_at",
    "consumed_at",
    "created_at",
    "expires_at",
)
_COLUMNS = ", ".join(_COLUMN_NAMES)


class ApprovalRepository:
    """One table, one link table, and nothing else."""

    def insert(
        self,
        *,
        approval_id: UUID,
        action: str,
        subject_customer_id: UUID | None,
        maker_user_id: UUID,
        maker_session_id: UUID,
        payload: Mapping[str, Any] | Sequence[Any],
        expires_at: datetime,
        created_at: datetime,
    ) -> Mapping[str, Any]:
        """Insert the proposal; the trigger computes ``payload_hash``."""
        values: list[Any] = [
            approval_id,
            action,
            subject_customer_id,
            maker_user_id,
            maker_session_id,
            # A payload is usually an object but may be a bare array: the ledger's adjustment
            # approval hashes exactly the lines `post_ledger_transaction` compares against.
            Jsonb(payload if isinstance(payload, list) else dict(payload)),
            # The trigger overwrites this; the column is NOT NULL, so a placeholder is what the
            # insert needs.
            "0" * 64,
            created_at,
            expires_at,
        ]
        # The only interpolated text is this module's own column list; all values are parameters.
        statement = (
            "INSERT INTO hub.approvals (id, action, subject_customer_id, maker_user_id, "  # noqa: S608
            "maker_session_id, payload, payload_hash, created_at, expires_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_COLUMNS}"
        )
        with connection.cursor() as cursor:
            cursor.execute(statement, values)
            row = cursor.fetchone()
        if row is None:  # pragma: no cover - RETURNING always yields on success
            raise RuntimeError("approval insert returned no row")
        return _row(row)

    def get(self, approval_id: UUID, *, for_update: bool = False) -> Mapping[str, Any] | None:
        """One row, locked when a decision is about to be made on it."""
        # The only interpolated text is the module's own column list; every value is a parameter.
        sql = f"SELECT {_COLUMNS} FROM hub.approvals WHERE id = %s"  # noqa: S608
        if for_update:
            sql += " FOR UPDATE"
        with connection.cursor() as cursor:
            cursor.execute(sql, [approval_id])
            row = cursor.fetchone()
            if row is None:
                return None
            return _row(row)

    def page(
        self,
        *,
        actions: Sequence[str],
        statuses: Sequence[str],
        maker_id: UUID | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Mapping[str, Any]]:
        """One page, newest first; the keyset is ``(created_at, id)`` so it stays stable."""
        conditions = ["action = ANY(%s)"]
        params: list[Any] = [list(actions)]  # Any: the driver adapts lists, uuids and Jsonb alike
        if statuses:
            conditions.append("status = ANY(%s)")
            params.append(list(statuses))
        if maker_id is not None:
            conditions.append("maker_user_id = %s")
            params.append(maker_id)
        if after is not None:
            conditions.append("(created_at, id) < (%s, %s)")
            params.extend([after[0], after[1]])
        params.append(limit)
        # Conditions are fixed strings this module builds; the values are all parameters.
        sql = (
            f"SELECT {_COLUMNS} FROM hub.approvals WHERE {' AND '.join(conditions)} "  # noqa: S608
            "ORDER BY created_at DESC, id DESC LIMIT %s"
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [_row(row) for row in rows]

    def decide(
        self,
        approval_id: UUID,
        *,
        status: str,
        approver_user_id: UUID,
        approver_session_id: UUID,
        decided_at: datetime,
    ) -> None:
        """Record the decision. The trigger refuses anything but a legal transition."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.approvals SET status = %s, approver_user_id = %s, "
                "approver_session_id = %s, decided_at = %s WHERE id = %s",
                [status, approver_user_id, approver_session_id, decided_at, approval_id],
            )

    def mark_consumed(self, approval_id: UUID, *, at: datetime) -> None:
        """The action ran: the approval can never be spent again."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.approvals SET status = 'consumed', consumed_at = %s WHERE id = %s",
                [at, approval_id],
            )

    def is_linked(self, user_id: UUID, customer_id: UUID) -> bool:
        """True when this user is recorded as connected to that customer (D-15)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM hub.staff_customer_link WHERE user_id = %s AND customer_id = %s",
                [user_id, customer_id],
            )
            return cursor.fetchone() is not None


def _row(values: Sequence[Any]) -> Mapping[str, Any]:
    """``_COLUMN_NAMES`` zipped with the fetched row, with jsonb parsed."""
    record = dict(zip(_COLUMN_NAMES, values, strict=True))
    payload = record.get("payload")
    if isinstance(payload, str):
        record["payload"] = json.loads(payload)
    return record
