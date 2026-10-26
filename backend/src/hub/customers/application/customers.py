"""Customers (FR-01, D-11, TC-FR-01-4): register, update, close, read, list.

Three rules shape this module:

* **One customer per phone number, in E.164.** The number is normalised before the uniqueness
  check, so ``+243 81 234 5678`` and ``+243812345678`` cannot become two people (Review
  focus 2). The repository's unique index is the last word; the pre-check only turns the
  common case into a 409 without a round trip through an aborted transaction.
* **A write is a compare-and-set.** The API checks ``If-Match`` before the use case runs; this
  layer re-reads the row, writes with the version it read, and lets the repository fail the
  statement if someone else got there first (409 ``version_conflict``).
* **Closing is not erasure.** ``closeCustomer`` refuses a non-zero wallet and moves the row to
  ``closed``; erasure is a separate approved process (slice 16), which is why the row keeps its
  identifiers here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from hub.customers.contracts import (
    CustomerRow,
    CustomerView,
    PlanStore,
    SiteStore,
    WalletBalanceProbe,
)
from hub.customers.domain.errors import (
    CustomerHasBalance,
    InvalidStateTransition,
    RegistryNotFound,
)
from hub.customers.domain.phone import normalise_e164
from hub.identity.contracts import AuditLog, Principal
from hub.shared.contracts import UnitOfWork
from hub.shared.errors import Conflict, ValidationFailed
from hub.shared.ports import Clock

__all__ = [
    "CloseCustomer",
    "CustomerQuery",
    "CustomerService",
    "GetCustomer",
    "ListCustomers",
    "RegisterCustomer",
    "UpdateCustomer",
    "customer_view",
]

UnitOfWorkFactory = Callable[[Principal | None], UnitOfWork]

# A customer may be suspended and reactivated; it may not leave `closed` except to `erased`,
# which slice 16's approved process owns.
_CUSTOMER_TRANSITIONS = {
    "active": {"suspended", "closed"},
    "suspended": {"active", "closed"},
    "closed": set(),
    "erased": set(),
}


@dataclass(frozen=True, slots=True)
class RegisterCustomer:
    """``createCustomer``: everything the contract requires, phone as typed by the caller."""

    full_name: str
    phone: str
    site_id: UUID
    plan_id: UUID
    email: str | None = None
    locale: str = "fr"
    external_ref: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateCustomer:
    """``updateCustomer``: only the fields present are written."""

    customer_id: UUID
    full_name: str | None = None
    email: str | None = None
    locale: str | None = None
    site_id: UUID | None = None
    plan_id: UUID | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class CloseCustomer:
    customer_id: UUID


@dataclass(frozen=True, slots=True)
class GetCustomer:
    customer_id: UUID


@dataclass(frozen=True, slots=True)
class CustomerQuery:
    """``listCustomers`` filters: keyset on ``id``, newest first."""

    statuses: Sequence[str] = ()
    site_id: UUID | None = None
    plan_id: UUID | None = None
    phone_e164: str | None = None
    after_id: UUID | None = None
    limit: int = 25


@dataclass(frozen=True, slots=True)
class ListCustomers:
    query: CustomerQuery


class CustomerStore(Protocol):
    """Persistence of ``hub.customers`` as this module needs it."""

    def add(self, customer: CustomerRow) -> None: ...

    def get(self, customer_id: UUID) -> CustomerRow | None: ...

    def find_by_phone(self, phone_e164: str) -> CustomerRow | None: ...

    def update_with_version(self, customer: CustomerRow, *, expected_version: int) -> None: ...

    def list_scoped(
        self,
        *,
        site_ids: Sequence[UUID] | None,
        statuses: Sequence[str] = ...,
        site_id: UUID | None = None,
        plan_id: UUID | None = None,
        phone_e164: str | None = None,
        after_id: UUID | None = None,
        limit: int = 25,
    ) -> list[CustomerRow]: ...


def customer_view(row: CustomerRow) -> CustomerView:
    return CustomerView(
        id=row.id,
        site_id=row.site_id,
        plan_id=row.plan_id,
        full_name=row.full_name,
        phone_e164=row.phone_e164,
        email=row.email,
        locale=row.language,
        status=row.status,
        version=row.version,
    )


class CustomerService:
    """The registry's customer surface, behind the same site scope as sites."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: Any,
        customers: CustomerStore,
        sites: SiteStore,
        plans: PlanStore,
        balances: WalletBalanceProbe,
        audit: AuditLog,
        unit_of_work: UnitOfWorkFactory,
        default_phone_prefix: str = "+243",
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._customers = customers
        self._sites = sites
        self._plans = plans
        self._balances = balances
        self._audit = audit
        self._unit_of_work = unit_of_work
        self._default_prefix = default_phone_prefix

    # --- scope ---------------------------------------------------------------------------
    def _scope(self, principal: Principal) -> list[UUID] | None:
        return None if principal.all_sites else sorted(principal.site_ids)

    def _visible(self, principal: Principal, customer_id: UUID) -> CustomerRow:
        """The row, or 404 — a customer outside the caller's sites does not exist for them."""
        row = self._customers.get(customer_id)
        if row is None:
            raise RegistryNotFound()
        scope = self._scope(principal)
        if scope is not None and row.site_id not in set(scope):
            raise RegistryNotFound()
        return row

    def _require_site_visible(self, principal: Principal, site_id: UUID) -> None:
        scope = self._scope(principal)
        if scope is not None and site_id not in set(scope):
            raise RegistryNotFound()

    # --- commands ------------------------------------------------------------------------
    def register(self, principal: Principal, command: RegisterCustomer) -> CustomerRow:
        phone = normalise_e164(command.phone, self._default_prefix)
        self._require_site_visible(principal, command.site_id)
        if self._sites.get(command.site_id) is None:
            raise RegistryNotFound()
        if self._plans.get(command.plan_id) is None:
            raise ValidationFailed(
                details={
                    "errors": [{"field": "/plan_id", "code": "unknown", "message": "no such plan"}]
                }
            )
        if self._customers.find_by_phone(phone) is not None:
            raise Conflict("duplicate_resource", message_key="duplicate_phone")

        now = self._clock.now()
        row = CustomerRow(
            id=self._ids.new_id(),
            site_id=command.site_id,
            plan_id=command.plan_id,
            external_ref=command.external_ref,
            full_name=command.full_name,
            phone_e164=phone,
            email=command.email,
            language=_locale(command.locale),
            status="active",
            pseudonym=None,
            created_at=now,
            updated_at=now,
            version=1,
        )
        with self._unit_of_work(principal) as uow:
            self._customers.add(row)
            uow.outbox.add(
                _customer_event(
                    name="customer.registered",
                    ids=self._ids,
                    now=now,
                    customer=row,
                    extra={"site_id": str(row.site_id), "plan_id": str(row.plan_id)},
                )
            )
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.customer.registered",
                entity_type="customer",
                entity_id=row.id,
                site_id=row.site_id,
                details={"fields": ["full_name", "phone_e164", "email", "language", "plan_id"]},
                subject_customer_id=row.id,
            )
        return row

    def update(self, principal: Principal, command: UpdateCustomer) -> CustomerRow:
        current = self._visible(principal, command.customer_id)

        if command.site_id is not None and command.site_id != current.site_id:
            self._require_site_visible(principal, command.site_id)
            if self._sites.get(command.site_id) is None:
                raise RegistryNotFound()
        if command.plan_id is not None and command.plan_id != current.plan_id:
            if self._plans.get(command.plan_id) is None:
                raise ValidationFailed(
                    details={
                        "errors": [
                            {"field": "/plan_id", "code": "unknown", "message": "no such plan"}
                        ]
                    }
                )

        status = command.status if command.status is not None else current.status
        if status != current.status and status not in _CUSTOMER_TRANSITIONS.get(
            current.status, set()
        ):
            raise InvalidStateTransition()

        updated = replace(
            current,
            full_name=command.full_name if command.full_name is not None else current.full_name,
            email=command.email if command.email is not None else current.email,
            language=_locale(command.locale) if command.locale is not None else current.language,
            site_id=command.site_id if command.site_id is not None else current.site_id,
            plan_id=command.plan_id if command.plan_id is not None else current.plan_id,
            status=status,
            updated_at=self._clock.now(),
        )
        changed = [
            field
            for field, value in (
                ("full_name", command.full_name),
                ("email", command.email),
                ("language", command.locale),
                ("site_id", command.site_id),
                ("plan_id", command.plan_id),
                ("status", command.status),
            )
            if value is not None
        ]
        with self._unit_of_work(principal) as uow:
            self._customers.update_with_version(updated, expected_version=current.version)
            # The trigger owns the increment, so the row's version is read back rather than
            # computed: answering with `current.version + 1` would be a second source of truth.
            stored = self._customers.get(updated.id) or updated
            if stored.status != current.status:
                now = self._clock.now()
                uow.outbox.add(
                    _customer_event(
                        name="customer.status_changed",
                        ids=self._ids,
                        now=now,
                        customer=stored,
                        extra={"status": stored.status},
                    )
                )
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.customer.updated",
                entity_type="customer",
                entity_id=stored.id,
                site_id=stored.site_id,
                details={"fields": changed},
                subject_customer_id=stored.id,
            )
        return stored

    def close(self, principal: Principal, command: CloseCustomer) -> None:
        current = self._visible(principal, command.customer_id)
        balance = self._balances.balance_minor(current.id)
        if balance != 0:
            raise CustomerHasBalance(details={"balance_minor": balance})

        closed = replace(
            current, status="closed", updated_at=self._clock.now(), version=current.version
        )
        with self._unit_of_work(principal) as uow:
            self._customers.update_with_version(closed, expected_version=current.version)
            uow.outbox.add(
                _customer_event(
                    name="customer.status_changed",
                    ids=self._ids,
                    now=closed.updated_at,
                    customer=closed,
                    extra={"status": "closed"},
                )
            )
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.customer.closed",
                entity_type="customer",
                entity_id=closed.id,
                site_id=closed.site_id,
                details={"balance_minor": balance},
                subject_customer_id=closed.id,
            )

    # --- reads ---------------------------------------------------------------------------
    def get(self, principal: Principal, query: GetCustomer) -> CustomerRow:
        return self._visible(principal, query.customer_id)

    def list(
        self, principal: Principal, command: ListCustomers
    ) -> tuple[Sequence[CustomerRow], UUID | None]:
        query = command.query
        rows = self._customers.list_scoped(
            site_ids=self._scope(principal),
            statuses=query.statuses,
            site_id=query.site_id,
            plan_id=query.plan_id,
            phone_e164=query.phone_e164,
            after_id=query.after_id,
            limit=query.limit + 1,
        )
        page = rows[: query.limit]
        next_cursor = page[-1].id if len(rows) > query.limit else None
        return page, next_cursor


def _locale(value: str) -> str:
    """Only ``en`` and ``fr`` are stored (``ck_customers_language``); anything else is ``en``."""
    return "fr" if value == "fr" else "en"


def _customer_event(
    *, name: str, ids: Any, now: datetime, customer: CustomerRow, extra: dict[str, Any]
) -> Any:
    from hub.customers.events import customer_registered, customer_status_changed  # noqa: PLC0415

    builder = customer_registered if name == "customer.registered" else customer_status_changed
    return builder(
        ids=ids,
        occurred_at=now,
        customer_id=customer.id,
        payload={
            "site_id": str(customer.site_id),
            "plan_id": str(customer.plan_id),
            "status": customer.status,
            **extra,
        },
    )
