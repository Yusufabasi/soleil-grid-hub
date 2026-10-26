"""Repositories over the registry tables (ADR-0020: plain SQL, no ORM writes).

Three behaviours here are worth naming:

* **Optimistic concurrency is a predicate, not a lock.** ``update_with_version`` issues
  ``UPDATE ... WHERE id = %s AND version = %s`` and treats zero affected rows as a lost race.
  The version itself is bumped by ``trg_customers_version``, never by Python, so a writer that
  forgot to check still cannot overwrite silently.
* **A natural-key clash is translated here**, next to the statement that can cause it, from
  the constraint the database names: ``uq_customers_phone`` is a duplicate phone (409
  ``duplicate_resource``, D-11), ``uq_sites_code``/``uq_plans_code`` a duplicate code.
* **Site scope is applied in SQL.** A non-``all_sites`` staff principal passes the sites it
  holds and the list is ``site_id = ANY(%s)``; reading one row outside them returns ``None``,
  which the use case turns into 404 so existence never leaks (D-14). Row-level security on
  ``customers`` is the second layer, not the first (the other registry tables have none).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, connection

from hub.customers.contracts import (
    CustomerRow,
    PlanRow,
    PlanTariffRow,
    SiteRow,
)
from hub.customers.domain.errors import (
    DuplicatePhone,
    TariffChangeTooLarge,
    VersionConflict,
)
from hub.shared.errors import Duplicate

__all__ = ["CustomerRepository", "PlanRepository", "SiteRepository", "TariffRepository"]

# The trigger's own code, embedded in the ``RAISE EXCEPTION`` message (db/schema.sql 3b).
TARIFF_CAP_CODE = "tariff_change_too_large"

_SITE_COLUMNS = "id, code, name, country_code, currency, timezone, status, created_at, updated_at"
_PLAN_COLUMNS = (
    "id, code, name, currency, min_topup_minor, max_topup_minor, status, created_at, updated_at"
)
_CUSTOMER_COLUMNS = (
    "id, site_id, plan_id, external_ref, full_name, phone_e164, email, language, status, "
    "pseudonym, created_at, updated_at, version"
)
_TARIFF_COLUMNS = (
    "id, plan_id, tariff_minor_per_kwh, effective_from, status, proposed_by, approval_id, "
    "created_at"
)


def _constraint(exc: IntegrityError) -> str:
    """The constraint name the server reported, or an empty string."""
    cause = exc.__cause__
    diag = getattr(cause, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return str(name) if name else ""


def _site(row: Any) -> SiteRow:
    values = tuple(row)
    return SiteRow(
        id=values[0],
        code=str(values[1]),
        name=str(values[2]),
        country_code=str(values[3]),
        currency=str(values[4]),
        timezone=str(values[5]),
        status=str(values[6]),
        created_at=values[7],
        updated_at=values[8],
    )


def _plan(row: Any) -> PlanRow:
    values = tuple(row)
    return PlanRow(
        id=values[0],
        code=str(values[1]),
        name=str(values[2]),
        currency=str(values[3]),
        min_topup_minor=int(values[4]),
        max_topup_minor=int(values[5]),
        status=str(values[6]),
        created_at=values[7],
        updated_at=values[8],
    )


def _customer(row: Any) -> CustomerRow:
    values = tuple(row)
    return CustomerRow(
        id=values[0],
        site_id=values[1],
        plan_id=values[2],
        external_ref=None if values[3] is None else str(values[3]),
        full_name=str(values[4]),
        phone_e164=None if values[5] is None else str(values[5]),
        email=None if values[6] is None else str(values[6]),
        language=str(values[7]),
        status=str(values[8]),
        pseudonym=None if values[9] is None else str(values[9]),
        created_at=values[10],
        updated_at=values[11],
        version=int(values[12]),
    )


def _tariff(row: Any) -> PlanTariffRow:
    values = tuple(row)
    return PlanTariffRow(
        id=values[0],
        plan_id=values[1],
        tariff_minor_per_kwh=int(values[2]),
        effective_from=values[3],
        status=str(values[4]),
        proposed_by=values[5],
        approval_id=values[6],
        created_at=values[7],
    )


class SiteRepository:
    """Reads and writes of ``hub.sites``; the table carries no version column."""

    def add(self, site: SiteRow) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO hub.sites (id, code, name, country_code, currency, timezone, "
                    "status, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        site.id,
                        site.code,
                        site.name,
                        site.country_code,
                        site.currency,
                        site.timezone,
                        site.status,
                        site.created_at,
                        site.updated_at,
                    ],
                )
        except IntegrityError as exc:
            if _constraint(exc) == "uq_sites_code":
                raise Duplicate() from exc
            raise

    def get(self, site_id: UUID) -> SiteRow | None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_SITE_COLUMNS} FROM hub.sites WHERE id = %s",  # noqa: S608 - literal columns
                [site_id],
            )
            row = cursor.fetchone()
        return _site(row) if row is not None else None

    def find_by_code(self, code: str) -> SiteRow | None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_SITE_COLUMNS} FROM hub.sites WHERE code = %s",  # noqa: S608
                [code],
            )
            row = cursor.fetchone()
        return _site(row) if row is not None else None

    def update(self, site: SiteRow) -> None:
        """Write name, timezone and status; ``updated_at`` is the trigger's to move."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.sites SET name = %s, timezone = %s, status = %s WHERE id = %s",
                [site.name, site.timezone, site.status, site.id],
            )

    def list_scoped(
        self,
        *,
        site_ids: Sequence[UUID] | None,
        after_id: UUID | None = None,
        limit: int = 25,
    ) -> list[SiteRow]:
        """One keyset page, within the sites a non-``all_sites`` principal holds.

        Ordering is by ``id`` because that is what the cursor carries: a uuid has no ties
        and never reorders, which ``code`` cannot promise once a site is renamed.
        """
        filters: list[str] = []
        params: list[Any] = []
        if site_ids is not None:
            filters.append("id = ANY(%s)")
            params.append(list(site_ids))
        if after_id is not None:
            filters.append("id > %s")
            params.append(after_id)
        where = f"WHERE {' AND '.join(filters)} " if filters else ""
        statement = (
            f"SELECT {_SITE_COLUMNS} FROM hub.sites {where}"  # noqa: S608 - literal columns
            "ORDER BY id LIMIT %s"
        )
        params.append(limit)
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return [_site(row) for row in cursor.fetchall()]


class PlanRepository:
    """Reads and writes of ``hub.plans`` and its immutable tariff versions (D-08)."""

    def add(self, plan: PlanRow) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, "
                    "max_topup_minor, status, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        plan.id,
                        plan.code,
                        plan.name,
                        plan.currency,
                        plan.min_topup_minor,
                        plan.max_topup_minor,
                        plan.status,
                        plan.created_at,
                        plan.updated_at,
                    ],
                )
        except IntegrityError as exc:
            if _constraint(exc) == "uq_plans_code":
                raise Duplicate() from exc
            raise

    def get(self, plan_id: UUID) -> PlanRow | None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_PLAN_COLUMNS} FROM hub.plans WHERE id = %s",  # noqa: S608
                [plan_id],
            )
            row = cursor.fetchone()
        return _plan(row) if row is not None else None

    def update(self, plan: PlanRow) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.plans SET name = %s, min_topup_minor = %s, max_topup_minor = %s, "
                "status = %s WHERE id = %s",
                [plan.name, plan.min_topup_minor, plan.max_topup_minor, plan.status, plan.id],
            )

    def list_all(self) -> list[PlanRow]:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT {_PLAN_COLUMNS} FROM hub.plans ORDER BY code")  # noqa: S608
            return [_plan(row) for row in cursor.fetchall()]

    # --- tariffs -------------------------------------------------------------------------
    def current_tariff(self, plan_id: UUID, *, at: datetime) -> PlanTariffRow | None:
        """``hub.current_tariff``: the latest non-proposed row whose date has arrived."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_TARIFF_COLUMNS} FROM hub.plan_tariffs t "  # noqa: S608
                "WHERE t.id = (SELECT plan_tariff_id FROM hub.current_tariff(%s, %s))",
                [plan_id, at],
            )
            row = cursor.fetchone()
        return _tariff(row) if row is not None else None


class TariffRepository:
    """Reads and writes of ``hub.plan_tariffs``, the immutable price versions (D-08).

    Two database guards live behind ``add``: the trigger that caps a revision at 25 percent
    and the unique ``(plan_id, effective_from)``. Both arrive as an ``IntegrityError`` and
    leave as the conflict code a client branches on.
    """

    def add(self, tariff: PlanTariffRow) -> None:
        """Insert a price version; the database's two guards become domain conflicts.

        ``trg_plan_tariffs_guard`` raises ``check_violation`` with the code in the message
        (a ``RAISE`` carries no constraint name), and ``uq_plan_tariffs_effective`` refuses a
        second row for the same date. A proposal that lost either race reports the code the
        client branches on instead of a 500.
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO hub.plan_tariffs (id, plan_id, tariff_minor_per_kwh, "
                    "effective_from, status, proposed_by, approval_id, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        tariff.id,
                        tariff.plan_id,
                        tariff.tariff_minor_per_kwh,
                        tariff.effective_from,
                        tariff.status,
                        tariff.proposed_by,
                        tariff.approval_id,
                        tariff.created_at,
                    ],
                )
        except IntegrityError as exc:
            if _constraint(exc) == "uq_plan_tariffs_effective":
                raise Duplicate() from exc
            if TARIFF_CAP_CODE in str(exc.__cause__):
                raise TariffChangeTooLarge() from exc
            raise

    def previous(self, plan_id: UUID, *, before: datetime) -> PlanTariffRow | None:
        """The latest version dated before ``before`` — exactly what the trigger compares."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_TARIFF_COLUMNS} FROM hub.plan_tariffs "  # noqa: S608
                "WHERE plan_id = %s AND effective_from < %s "
                "ORDER BY effective_from DESC LIMIT 1",
                [plan_id, before],
            )
            row = cursor.fetchone()
        return _tariff(row) if row is not None else None

    def supersede_before(self, plan_id: UUID, *, before: datetime) -> int:
        """Retire the prices this one replaces; a later-dated row is left for its own turn."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.plan_tariffs SET status = 'superseded' "
                "WHERE plan_id = %s AND status = 'active' AND effective_from < %s",
                [plan_id, before],
            )
            return int(cursor.rowcount)

    def page(
        self, plan_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[PlanTariffRow]:
        """One keyset page ordered by ``(effective_from, id)`` descending."""
        conditions = ["plan_id = %s"]
        params: list[Any] = [plan_id]
        if after is not None:
            conditions.append("(effective_from, id) < (%s, %s)")
            params.extend([after[0], after[1]])
        params.append(limit)
        statement = (
            f"SELECT {_TARIFF_COLUMNS} FROM hub.plan_tariffs "  # noqa: S608 - literal columns
            f"WHERE {' AND '.join(conditions)} ORDER BY effective_from DESC, id DESC LIMIT %s"
        )
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return [_tariff(row) for row in cursor.fetchall()]

    def has_pending_proposal(
        self, plan_id: UUID, *, effective_from: datetime, at: datetime
    ) -> bool:
        """Is a live proposal for this plan and date waiting for a second pair of hands?

        This is the one read of ``hub.approvals`` outside `identity`, and it is read-only: a
        proposal has no row in ``plan_tariffs`` yet (it is inserted when it is approved), so the
        unique ``(plan_id, effective_from)`` cannot answer this question before the decision.
        Adding the check here keeps the answer honest without teaching `identity` about tariffs.
        An approval past ``at`` is excluded: it can no longer be decided, so it must not hold a
        date hostage for the rest of the deployment's life.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM hub.approvals WHERE action = 'tariff.change' "
                "AND status = 'pending' AND expires_at > %s AND payload->>'plan_id' = %s "
                "AND (payload->>'effective_from')::timestamptz = %s LIMIT 1",
                [at, str(plan_id), effective_from],
            )
            return cursor.fetchone() is not None


class CustomerRepository:
    """Reads and writes of ``hub.customers``, with the site scope and the version check."""

    def add(self, customer: CustomerRow) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO hub.customers (id, site_id, plan_id, external_ref, full_name, "
                    "phone_e164, email, language, status, pseudonym, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        customer.id,
                        customer.site_id,
                        customer.plan_id,
                        customer.external_ref,
                        customer.full_name,
                        customer.phone_e164,
                        customer.email,
                        customer.language,
                        customer.status,
                        customer.pseudonym,
                        customer.created_at,
                        customer.updated_at,
                    ],
                )
        except IntegrityError as exc:
            if _constraint(exc) == "uq_customers_phone":
                raise DuplicatePhone() from exc
            raise

    def get(self, customer_id: UUID) -> CustomerRow | None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_CUSTOMER_COLUMNS} FROM hub.customers "  # noqa: S608
                "WHERE id = %s AND deleted_at IS NULL",
                [customer_id],
            )
            row = cursor.fetchone()
        return _customer(row) if row is not None else None

    def find_by_phone(self, phone_e164: str) -> CustomerRow | None:
        """The active customer holding this number, for the identity login (D-11)."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_CUSTOMER_COLUMNS} FROM hub.customers "  # noqa: S608
                "WHERE phone_e164 = %s AND deleted_at IS NULL",
                [phone_e164],
            )
            row = cursor.fetchone()
        return _customer(row) if row is not None else None

    def update_with_version(self, customer: CustomerRow, *, expected_version: int) -> None:
        """Write the mutable columns, or raise ``VersionConflict`` if the row moved on."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.customers SET plan_id = %s, full_name = %s, phone_e164 = %s, "
                "email = %s, language = %s, status = %s WHERE id = %s AND version = %s "
                "AND deleted_at IS NULL",
                [
                    customer.plan_id,
                    customer.full_name,
                    customer.phone_e164,
                    customer.email,
                    customer.language,
                    customer.status,
                    customer.id,
                    expected_version,
                ],
            )
            changed = cursor.rowcount
        if changed != 1:
            raise VersionConflict()

    def list_scoped(
        self,
        *,
        site_ids: Iterable[UUID] | None,
        statuses: Sequence[str] = (),
        site_id: UUID | None = None,
        plan_id: UUID | None = None,
        phone_e164: str | None = None,
        after_id: UUID | None = None,
        limit: int = 25,
    ) -> list[CustomerRow]:
        """One page, newest id first by keyset, within the caller's sites.

        ``site_ids is None`` means every site (an ``all_sites`` principal); a list that is
        empty can never widen access, it matches nothing.
        """
        filters = ["deleted_at IS NULL"]
        params: list[Any] = []
        if site_ids is not None:
            filters.append("site_id = ANY(%s)")
            params.append(list(site_ids))
        if site_id is not None:
            filters.append("site_id = %s")
            params.append(site_id)
        if plan_id is not None:
            filters.append("plan_id = %s")
            params.append(plan_id)
        if phone_e164 is not None:
            filters.append("phone_e164 = %s")
            params.append(phone_e164)
        if statuses:
            filters.append("status = ANY(%s)")
            params.append(list(statuses))
        if after_id is not None:
            filters.append("id < %s")
            params.append(after_id)
        statement = (
            f"SELECT {_CUSTOMER_COLUMNS} FROM hub.customers "  # noqa: S608 - literal columns
            f"WHERE {' AND '.join(filters)} ORDER BY id DESC LIMIT %s"
        )
        params.append(limit)
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return [_customer(row) for row in cursor.fetchall()]
