"""Reads and writes of ``hub.top_ups``, ``hub.provider_events`` and ``hub.webhook_quarantine``.

Three rules shape this file, and each of them is a bug the schema already had:

* **The event row is inserted with ``ON CONFLICT DO NOTHING RETURNING id``.** An
  ``INSERT ... ON CONFLICT`` is the only statement that answers "did I win?" atomically; a
  ``SELECT`` first and an ``INSERT`` after would let two deliveries both see nothing and both
  try to write. Zero rows back means this delivery lost the race and the caller answers
  ``duplicate``.
* **Locking a top-up requires an open transaction.** ``lock_by_provider_ref`` takes
  ``FOR UPDATE OF top_ups`` and calls ``uow.require_active()`` first, so a use case that forgot
  its ``with uow:`` gets an error instead of an unlocked read that looks like a lock.
* **``update_fields`` only writes columns ``hub_ingest`` holds.** The grant is per column; a
  write to ``amount_minor`` would be a permission error in production, so the whitelist here
  says the same thing earlier and in words.

Nothing here decides anything: the state rules live in ``domain/top_up.py`` and the routing of a
confirmation in ``domain/routing.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import connection
from psycopg.types.json import Jsonb

from hub.payments.contracts import ProviderEventRow, TopUpRow
from hub.shared.contracts import UnitOfWork

__all__ = ["ProviderEventRepository", "TopUpRepository"]

_TOP_UP_COLUMNS = (
    "id, customer_id, meter_id, provider, provider_ref, provider_txn_id, payer_msisdn,"
    " payer_matched, status_pull_at, amount_minor, currency, plan_tariff_id,"
    " tariff_minor_per_kwh, energy_wh, status, late, provider_event_id, ledger_transaction_id,"
    " reversal_transaction_id, initiated_at, expires_at, confirmed_at, updated_at"
)

_EVENT_COLUMNS = (
    "id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload,"
    " status, last_error, received_at, processed_at"
)

# `hub_ingest` holds UPDATE on exactly these columns of `top_ups` (db/schema.sql grants block).
WRITABLE_TOP_UP_COLUMNS = frozenset(
    {
        "status",
        "late",
        "payer_matched",
        "status_pull_at",
        "provider_event_id",
        "provider_txn_id",
        "ledger_transaction_id",
        "reversal_transaction_id",
        "confirmed_at",
        "updated_at",
    }
)


def _row(record: Sequence[Any]) -> TopUpRow:
    return TopUpRow(
        id=record[0],
        customer_id=record[1],
        meter_id=record[2],
        provider=record[3],
        provider_ref=record[4],
        provider_txn_id=record[5],
        payer_msisdn=record[6],
        payer_matched=record[7],
        status_pull_at=record[8],
        amount_minor=record[9],
        currency=record[10],
        plan_tariff_id=record[11],
        tariff_minor_per_kwh=record[12],
        energy_wh=record[13],
        status=record[14],
        late=record[15],
        provider_event_id=record[16],
        ledger_transaction_id=record[17],
        reversal_transaction_id=record[18],
        initiated_at=record[19],
        expires_at=record[20],
        confirmed_at=record[21],
        updated_at=record[22],
    )


def _event_row(record: Sequence[Any]) -> ProviderEventRow:
    return ProviderEventRow(
        id=record[0],
        provider=record[1],
        event_id=record[2],
        provider_txn_id=record[3],
        event_type=record[4],
        amount_minor=record[5],
        currency=record[6],
        payload=record[7],
        status=record[8],
        last_error=record[9],
        received_at=record[10],
        processed_at=record[11],
    )


class TopUpRepository:
    """The top-up table, as the use cases need it."""

    def add(self, row: TopUpRow) -> None:
        """Insert a top-up that is already ``initiated`` (the trigger insists on that)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, provider_ref,"
                " payer_msisdn, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh,"
                " energy_wh, status, late, initiated_at, expires_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    row.id,
                    row.customer_id,
                    row.meter_id,
                    row.provider,
                    row.provider_ref,
                    row.payer_msisdn,
                    row.amount_minor,
                    row.currency,
                    row.plan_tariff_id,
                    row.tariff_minor_per_kwh,
                    row.energy_wh,
                    row.status,
                    row.late,
                    row.initiated_at,
                    row.expires_at,
                    row.updated_at,
                ],
            )

    def get(self, top_up_id: UUID) -> TopUpRow | None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_TOP_UP_COLUMNS} FROM hub.top_ups WHERE id = %s",  # noqa: S608
                [top_up_id],
            )
            record = cursor.fetchone()
        return None if record is None else _row(record)

    def peek_by_provider_ref(self, provider: str, provider_ref: str) -> TopUpRow | None:
        """The top-up a delivery names, read without a lock: the payer and pull pre-checks."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_TOP_UP_COLUMNS} FROM hub.top_ups"  # noqa: S608 - literal columns
                " WHERE provider = %s AND provider_ref = %s",
                [provider, provider_ref],
            )
            record = cursor.fetchone()
        return None if record is None else _row(record)

    def lock_by_provider_ref(
        self, provider: str, provider_ref: str, *, uow: UnitOfWork
    ) -> TopUpRow | None:
        """The top-up, locked for the rest of the transaction (``FOR UPDATE OF top_ups``).

        ``OF top_ups`` rather than a bare ``FOR UPDATE``: the statement joins nothing today,
        but naming the table keeps a later join from locking rows nobody meant to lock.
        """
        uow.require_active()
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_TOP_UP_COLUMNS} FROM hub.top_ups"  # noqa: S608 - literal columns
                " WHERE provider = %s AND provider_ref = %s FOR UPDATE OF top_ups",
                [provider, provider_ref],
            )
            record = cursor.fetchone()
        return None if record is None else _row(record)

    def lock(self, top_up_id: UUID, *, uow: UnitOfWork) -> TopUpRow | None:
        """The top-up by id, locked for the rest of the transaction."""
        uow.require_active()
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_TOP_UP_COLUMNS} FROM hub.top_ups"  # noqa: S608 - literal columns
                " WHERE id = %s FOR UPDATE OF top_ups",
                [top_up_id],
            )
            record = cursor.fetchone()
        return None if record is None else _row(record)

    def update_fields(self, top_up_id: UUID, **columns: Any) -> None:
        """Write the named columns, refusing any that ``hub_ingest`` cannot update."""
        unknown = set(columns) - WRITABLE_TOP_UP_COLUMNS
        if unknown:
            raise ValueError(f"not writable by the payment path: {sorted(unknown)}")
        if not columns:
            return
        assignments = ", ".join(f"{name} = %s" for name in columns)
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE hub.top_ups SET {assignments} WHERE id = %s",  # noqa: S608 - whitelist
                [*columns.values(), top_up_id],
            )

    def count_for_meter_since(self, meter_id: UUID, *, since: datetime) -> int:
        """Top-ups for one meter since ``since``, every status counted (a limit, not a ledger)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM hub.top_ups WHERE meter_id = %s AND initiated_at >= %s",
                [meter_id, since],
            )
            return int(cursor.fetchone()[0])

    def count_for_payer_since(self, payer_msisdn: str, *, since: datetime) -> int:
        """Top-ups from one payer number since ``since``: the per-payer hourly ceiling."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM hub.top_ups WHERE payer_msisdn = %s AND initiated_at >= %s",
                [payer_msisdn, since],
            )
            return int(cursor.fetchone()[0])

    def list_expirable(self, *, now: datetime, limit: int) -> list[UUID]:
        """Ids still waiting for the provider when their window closed, oldest first."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM hub.top_ups WHERE status IN ('initiated','pending')"
                " AND expires_at <= %s ORDER BY expires_at LIMIT %s",
                [now, limit],
            )
            return [record[0] for record in cursor.fetchall()]

    def list_initiated_before(self, *, before: datetime, limit: int) -> list[UUID]:
        """Top-ups whose provider call never came back, oldest first (the retry sweeper)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM hub.top_ups WHERE status = 'initiated' AND initiated_at <= %s"
                " ORDER BY initiated_at LIMIT %s",
                [before, limit],
            )
            return [record[0] for record in cursor.fetchall()]

    def list_pending_unknown_before(self, *, before: datetime, limit: int) -> list[UUID]:
        """Top-ups whose initiation timed out and that nobody has resolved since."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM hub.top_ups WHERE status = 'pending_unknown'"
                " AND updated_at <= %s ORDER BY updated_at LIMIT %s",
                [before, limit],
            )
            return [record[0] for record in cursor.fetchall()]

    def page_for_customer(
        self, customer_id: UUID, *, after_id: UUID | None, limit: int
    ) -> list[TopUpRow]:
        """One page of a customer's history, newest id first (keyset on the uuidv7)."""
        clauses = ["customer_id = %s"]
        params: list[Any] = [customer_id]
        if after_id is not None:
            clauses.append("id < %s")
            params.append(after_id)
        params.append(limit)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_TOP_UP_COLUMNS} FROM hub.top_ups"  # noqa: S608 - literal columns
                f" WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT %s",
                params,
            )
            return [_row(record) for record in cursor.fetchall()]

    def page(
        self,
        *,
        customer_id: UUID | None,
        after_id: UUID | None,
        limit: int,
    ) -> list[TopUpRow]:
        """One page, optionally for one customer: the staff list and the portal share it."""
        clauses: list[str] = []
        params: list[Any] = []
        if customer_id is not None:
            clauses.append("customer_id = %s")
            params.append(customer_id)
        if after_id is not None:
            clauses.append("id < %s")
            params.append(after_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_TOP_UP_COLUMNS} FROM hub.top_ups{where}"  # noqa: S608 - literal
                " ORDER BY id DESC LIMIT %s",
                params,
            )
            return [_row(record) for record in cursor.fetchall()]

    def confirmed_between(self, start: datetime, end: datetime, provider: str) -> list[TopUpRow]:
        """Confirmed payments in a window: what a reconciliation batch compares against."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_TOP_UP_COLUMNS} FROM hub.top_ups"  # noqa: S608 - literal columns
                " WHERE provider = %s AND status = 'confirmed'"
                " AND confirmed_at >= %s AND confirmed_at < %s ORDER BY confirmed_at, id",
                [provider, start, end],
            )
            return [_row(record) for record in cursor.fetchall()]


class ProviderEventRepository:
    """The verified-delivery log, which is also the idempotency arbiter."""

    def insert_if_new(
        self,
        *,
        event_id: UUID,
        provider: str,
        provider_event_key: str,
        provider_txn_id: str,
        event_type: str,
        amount_minor: int | None,
        currency: str | None,
        payload: Mapping[str, Any],
        received_at: datetime,
    ) -> UUID | None:
        """Record the delivery, or return ``None`` when either unique key already exists.

        One statement, so the winner is decided by PostgreSQL: the loser blocks on the unique
        index until the winner commits and then sees no row back, which is exactly the
        ``duplicate`` answer the provider's retry needs.
        """
        # One list of mixed types: the payload is a `Jsonb` and the rest are scalars.
        parameters: list[Any] = [
            event_id,
            provider,
            provider_event_key,
            provider_txn_id,
            event_type,
            amount_minor,
            currency,
            Jsonb(dict(payload)),
            received_at,
        ]
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id,"
                " event_type, amount_minor, currency, payload, status, received_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'received', %s)"
                " ON CONFLICT DO NOTHING RETURNING id",
                parameters,
            )
            record = cursor.fetchone()
        return None if record is None else record[0]

    def get(self, event_id: UUID) -> ProviderEventRow | None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_EVENT_COLUMNS} FROM hub.provider_events WHERE id = %s",  # noqa: S608
                [event_id],
            )
            record = cursor.fetchone()
        return None if record is None else _event_row(record)

    def find_by_event_key(self, provider: str, event_key: str) -> ProviderEventRow | None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_EVENT_COLUMNS} FROM hub.provider_events"  # noqa: S608 - literals
                " WHERE provider = %s AND event_id = %s",
                [provider, event_key],
            )
            record = cursor.fetchone()
        return None if record is None else _event_row(record)

    def mark(
        self,
        event_id: UUID,
        *,
        status: str,
        processed_at: datetime | None = None,
        last_error: str | None = None,
    ) -> None:
        """Finish an event: ``processed`` or ``ignored`` set the timestamp the CHECK requires."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.provider_events"
                " SET status = %s, processed_at = %s, last_error = %s WHERE id = %s",
                [status, processed_at, last_error, event_id],
            )

    def page(
        self, *, after_id: UUID | None, limit: int, provider: str | None = None
    ) -> list[ProviderEventRow]:
        """The staff webhook log, newest first; the payload stays in the database."""
        clauses: list[str] = []
        params: list[Any] = []
        if provider is not None:
            clauses.append("provider = %s")
            params.append(provider)
        if after_id is not None:
            clauses.append("id < %s")
            params.append(after_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_EVENT_COLUMNS} FROM hub.provider_events{where}"  # noqa: S608
                " ORDER BY id DESC LIMIT %s",
                params,
            )
            return [_event_row(record) for record in cursor.fetchall()]

    def quarantine(
        self,
        *,
        quarantine_id: UUID,
        provider: str,
        reason: str,
        source_ip: str | None,
        body_sha256: str,
        body_length: int,
        signature_header: str | None,
        received_at: datetime,
    ) -> None:
        """Log a rejected delivery: a hash and a length, never the body (FR-03)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.webhook_quarantine (id, provider, reason, source_ip,"
                " body_sha256, body_length, signature_hdr, received_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    quarantine_id,
                    provider,
                    reason,
                    source_ip,
                    body_sha256,
                    body_length,
                    signature_header,
                    received_at,
                ],
            )

    def quarantine_counts(self, *, since: datetime) -> dict[str, int]:
        """Rejections per reason since ``since``: the counter the alert watches."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT reason, count(*) FROM hub.webhook_quarantine"
                " WHERE received_at >= %s GROUP BY reason",
                [since],
            )
            return {str(reason): int(count) for reason, count in cursor.fetchall()}
