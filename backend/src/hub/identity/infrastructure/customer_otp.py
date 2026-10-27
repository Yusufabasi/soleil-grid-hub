"""Rows behind the customer one-time code: ``hub.customer_otp`` and ``hub.customers`` (D-21).

Two things this module deliberately does *not* do: it never stores a phone number (only the
HMAC the service computed), and it never returns a row without its lock when a verification is
about to count an attempt — two concurrent guesses must not both see ``attempts = 2``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import connection

from hub.identity.application.customer_otp import OtpRow
from hub.identity.contracts import CustomerRecord

__all__ = ["CustomerDirectory", "OtpRepository"]

_COLUMNS = "id, phone_hmac, code_hash, purpose, attempts, max_attempts, expires_at, consumed_at"


def _row(values: Any) -> OtpRow:
    return OtpRow(
        id=values[0],
        phone_hmac=bytes(values[1]),
        code_hash=bytes(values[2]),
        purpose=str(values[3]),
        attempts=int(values[4]),
        max_attempts=int(values[5]),
        expires_at=values[6],
        consumed_at=values[7],
    )


class OtpRepository:
    """``hub.customer_otp``, one challenge at a time."""

    def insert(
        self,
        *,
        challenge_id: UUID,
        phone_hmac: bytes,
        code_hash: bytes,
        purpose: str,
        request_ip: str | None,
        created_at: datetime,
        expires_at: datetime,
        max_attempts: int,
    ) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.customer_otp (id, phone_hmac, code_hash, purpose, attempts, "
                "max_attempts, request_ip, created_at, expires_at) "
                "VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s)",
                [
                    challenge_id,
                    phone_hmac,
                    code_hash,
                    purpose,
                    max_attempts,
                    request_ip,
                    created_at,
                    expires_at,
                ],
            )

    def get(self, challenge_id: UUID, *, for_update: bool = False) -> OtpRow | None:
        statement = f"SELECT {_COLUMNS} FROM hub.customer_otp WHERE id = %s"  # noqa: S608
        if for_update:
            statement += " FOR UPDATE"
        with connection.cursor() as cursor:
            cursor.execute(statement, [challenge_id])
            row = cursor.fetchone()
        return _row(row) if row is not None else None

    def record_attempt(self, challenge_id: UUID) -> int:
        """Count one wrong code and answer with the new total."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.customer_otp SET attempts = attempts + 1 WHERE id = %s "
                "RETURNING attempts",
                [challenge_id],
            )
            row = cursor.fetchone()
        return int(row[0]) if row is not None else 0

    def consume(self, challenge_id: UUID, *, at: datetime) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.customer_otp SET consumed_at = coalesce(consumed_at, %s) WHERE id = %s",
                [at, challenge_id],
            )


class CustomerDirectory:
    """The lookup slice 01 needs from a module slice 02 owns; only active customers sign in.

    ``last_login_at`` is not a column: a customer's last sign-in *is* their last refresh
    session, which every completed login writes. Deriving it keeps the dormancy rule (D-21)
    honest even for a session that ended, and needs no new column to maintain.
    """

    _SELECT = (
        "SELECT c.id, c.language, "
        "       (SELECT max(rt.issued_at) FROM hub.refresh_tokens rt "
        "         WHERE rt.customer_id = c.id) AS last_login_at "
        "  FROM hub.customers c "
    )

    def find_active_by_phone(self, phone_e164: str) -> CustomerRecord | None:
        with connection.cursor() as cursor:
            cursor.execute(
                self._SELECT
                + "WHERE c.phone_e164 = %s AND c.status = 'active' AND c.deleted_at IS NULL",
                [phone_e164],
            )
            row = cursor.fetchone()
        return _customer(row) if row is not None else None

    def by_id(self, customer_id: UUID) -> CustomerRecord | None:
        with connection.cursor() as cursor:
            cursor.execute(
                self._SELECT + "WHERE c.id = %s AND c.status = 'active' AND c.deleted_at IS NULL",
                [customer_id],
            )
            row = cursor.fetchone()
        return _customer(row) if row is not None else None


def _customer(values: Any) -> CustomerRecord:
    return CustomerRecord(
        id=values[0], locale="fr" if values[1] == "fr" else "en", last_login_at=values[2]
    )
