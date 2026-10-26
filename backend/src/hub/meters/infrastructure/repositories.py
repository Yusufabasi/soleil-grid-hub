"""Repository over the meter tables (ADR-0020: plain SQL, no ORM writes).

Assignment is a race by nature (Review focus 1): two operators can hand one meter to two
customers at the same instant. ``lock_for_assign`` takes ``SELECT ... FOR UPDATE`` on the
meter row inside the caller's unit of work, so the second transaction waits, re-reads a row
whose ``version`` has moved and fails the ``If-Match`` check — exactly one assignment lands,
and the loser gets 412 or 409 rather than a second ``meter_assignments`` row.

The history table is not written here: ``trg_meters_assignment`` records every new
``(meter, customer)`` pair, which is what keeps ``meter_assignments`` honest even if a future
writer forgets about it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from django.db import IntegrityError, connection

from hub.meters.contracts import MeterRef, MeterRow
from hub.meters.domain.errors import MeterNotUsable, MeterSerialConflict, VersionConflict
from hub.shared.errors import NotFound

__all__ = ["MeterCreditProbe", "MeterRegistryImpl", "MeterRepository"]

_METER_COLUMNS = (
    "id, serial_no, site_id, gateway_id, customer_id, status, firmware, token_counter, "
    "installed_at, created_at, updated_at, version"
)


class MeterCreditProbe:
    """Credit outstanding on a meter; slice 06 binds the token ledger, 0 until then."""

    def credit_wh(self, meter_id: UUID) -> int:  # noqa: ARG002 - the port's shape
        return 0


def _meter(row: Any) -> MeterRow:
    values = tuple(row)
    return MeterRow(
        id=values[0],
        serial_no=str(values[1]),
        site_id=values[2],
        gateway_id=values[3],
        customer_id=values[4],
        status=str(values[5]),
        firmware=None if values[6] is None else str(values[6]),
        token_counter=int(values[7]),
        installed_at=values[8],
        created_at=values[9],
        updated_at=values[10],
        version=int(values[11]),
    )


def _constraint(exc: IntegrityError) -> str:
    cause = exc.__cause__
    diag = getattr(cause, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return str(name) if name else ""


class MeterRepository:
    """Reads and writes of ``hub.meters`` and its assignment history."""

    def add(self, meter: MeterRow) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO hub.meters (id, serial_no, site_id, gateway_id, customer_id, "
                    "status, firmware, token_counter, installed_at, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        meter.id,
                        meter.serial_no,
                        meter.site_id,
                        meter.gateway_id,
                        meter.customer_id,
                        meter.status,
                        meter.firmware,
                        meter.token_counter,
                        meter.installed_at,
                        meter.created_at,
                        meter.updated_at,
                    ],
                )
        except IntegrityError as exc:
            if _constraint(exc) == "uq_meters_serial":
                raise MeterSerialConflict() from exc
            raise

    def get(self, meter_id: UUID) -> MeterRow | None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_METER_COLUMNS} FROM hub.meters WHERE id = %s",  # noqa: S608
                [meter_id],
            )
            row = cursor.fetchone()
        return _meter(row) if row is not None else None

    def lock_for_assign(self, meter_id: UUID) -> MeterRow | None:
        """Read the row under ``FOR UPDATE``; the caller must be inside a unit of work."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_METER_COLUMNS} FROM hub.meters WHERE id = %s FOR UPDATE",  # noqa: S608
                [meter_id],
            )
            row = cursor.fetchone()
        return _meter(row) if row is not None else None

    def update_with_version(self, meter: MeterRow, *, expected_version: int) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.meters SET site_id = %s, customer_id = %s, status = %s, "
                "firmware = %s, installed_at = %s WHERE id = %s AND version = %s",
                [
                    meter.site_id,
                    meter.customer_id,
                    meter.status,
                    meter.firmware,
                    meter.installed_at,
                    meter.id,
                    expected_version,
                ],
            )
            changed = cursor.rowcount
        if changed != 1:
            raise VersionConflict()

    def assignment_history(self, meter_id: UUID) -> list[UUID]:
        """Every customer this meter has served, oldest first (the trigger's rows)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT customer_id FROM hub.meter_assignments WHERE meter_id = %s "
                "ORDER BY first_assigned_at",
                [meter_id],
            )
            return [row[0] for row in cursor.fetchall()]

    def active_for_customer(self, customer_id: UUID) -> MeterRow | None:
        """The one meter a customer may hold in v1 (Plan notes 5)."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_METER_COLUMNS} FROM hub.meters "  # noqa: S608
                "WHERE customer_id = %s AND status <> 'decommissioned' ORDER BY created_at LIMIT 1",
                [customer_id],
            )
            row = cursor.fetchone()
        return _meter(row) if row is not None else None

    def customer_for_meter(self, meter_id: UUID) -> UUID | None:
        meter = self.get(meter_id)
        return meter.customer_id if meter is not None else None

    def list_scoped(
        self,
        *,
        site_ids: Sequence[UUID] | None,
        customer_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 25,
    ) -> list[MeterRow]:
        filters: list[str] = []
        params: list[Any] = []
        if site_ids is not None:
            filters.append("site_id = ANY(%s)")
            params.append(list(site_ids))
        if customer_id is not None:
            filters.append("customer_id = %s")
            params.append(customer_id)
        if after_id is not None:
            filters.append("id < %s")
            params.append(after_id)
        where = f"WHERE {' AND '.join(filters)} " if filters else ""
        statement = (
            f"SELECT {_METER_COLUMNS} FROM hub.meters {where}"  # noqa: S608 - literal columns
            "ORDER BY id DESC LIMIT %s"
        )
        params.append(limit)
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return [_meter(row) for row in cursor.fetchall()]

    def per_site(self) -> dict[UUID, int]:
        with connection.cursor() as cursor:
            cursor.execute("SELECT site_id, count(*) FROM hub.meters GROUP BY site_id")
            return {row[0]: int(row[1]) for row in cursor.fetchall()}


class MeterRegistryImpl:
    """``MeterRegistry`` over the repository: what a credit path may assume."""

    def __init__(self, repository: MeterRepository) -> None:
        self._repository = repository

    def get(self, meter_id: UUID) -> MeterRef:
        meter = self._repository.get(meter_id)
        if meter is None:
            raise NotFound()
        return MeterRef(
            id=meter.id,
            serial_no=meter.serial_no,
            site_id=meter.site_id,
            customer_id=meter.customer_id,
            status=meter.status,
            version=meter.version,
        )

    def assert_usable_for_credit(self, meter_id: UUID, customer_id: UUID) -> None:
        meter = self._repository.get(meter_id)
        if meter is None:
            raise NotFound()
        if meter.status == "decommissioned":
            raise MeterNotUsable("meter_decommissioned")
        if meter.customer_id != customer_id:
            raise MeterNotUsable("meter_not_owned")
