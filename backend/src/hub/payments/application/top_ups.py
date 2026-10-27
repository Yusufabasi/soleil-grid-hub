"""Reading and cancelling a top-up (FR-02, docs/03 6.1).

Three rules, and each of them is a way an endpoint could leak or lie:

* **A customer sees only their own rows and gets 404, never 403, for anybody else's.** 403 would
  confirm that an id exists; the portal has no reason to learn that. The scope is applied in
  SQL (``TopUpRepository.page``), so even the page's ``has_next`` cannot count rows the caller
  may not see.
* **A staff caller is scoped by site.** ``users.all_sites`` widens it; an empty set narrows it
  to nothing, which is the safe reading of "no site is assigned to you".
* **Cancelling is idempotent and only reachable while waiting.** ``initiated``, ``pending`` and
  ``pending_unknown`` may be cancelled — the last one because the provider may never have taken
  the money. A confirmed top-up is refused with 409, and a second cancel answers the same row
  rather than a second change, because a retried request is not an error.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from hub.customers.contracts import CustomerDirectory
from hub.customers.domain.errors import RegistryNotFound
from hub.identity.contracts import Principal
from hub.payments.application.initiate_top_up import top_up_view
from hub.payments.contracts import TopUpRow, TopUpView
from hub.payments.domain.errors import InsufficientScope, PaymentNotFound
from hub.payments.domain.top_up import TopUp
from hub.shared.contracts import UnitOfWork
from hub.shared.ports import Clock

__all__ = [
    "CancelTopUp",
    "GetTopUp",
    "TopUpQuery",
    "TopUpService",
    "TopUpStore",
]

UnitOfWorkFactory = Callable[[Principal | None], UnitOfWork]

# Statuses a caller may still cancel: money has not been credited in any of them.
CANCELLABLE = frozenset({"initiated", "pending", "pending_unknown"})

# Roles that see the whole ledger of payments rather than their own rows (docs/05 tier table).
_READER_ROLES = frozenset({"finance_officer"})


class TopUpStore(Protocol):
    """The reads and writes these use cases need."""

    def get(self, top_up_id: UUID) -> TopUpRow | None: ...

    def lock(self, top_up_id: UUID, *, uow: UnitOfWork) -> TopUpRow | None: ...

    def update_fields(self, top_up_id: UUID, **columns: Any) -> None: ...

    def page(
        self,
        *,
        customer_id: UUID | None = None,
        site_ids: Sequence[UUID] | None = None,
        statuses: Sequence[str] = (),
        meter_id: UUID | None = None,
        provider: str | None = None,
        provider_reference: str | None = None,
        late: bool | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        after_id: UUID | None = None,
        limit: int = 25,
    ) -> list[TopUpRow]: ...


@dataclass(frozen=True, slots=True)
class GetTopUp:
    top_up_id: UUID


@dataclass(frozen=True, slots=True)
class CancelTopUp:
    top_up_id: UUID


@dataclass(frozen=True, slots=True)
class TopUpQuery:
    """``listTopUps`` filters: keyset on ``id``, newest first."""

    statuses: tuple[str, ...] = ()
    customer_id: UUID | None = None
    meter_id: UUID | None = None
    provider: str | None = None
    provider_reference: str | None = None
    late: bool | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    after_id: UUID | None = None
    limit: int = 25


def _is_customer(principal: Principal) -> bool:
    return principal.kind == "customer"


def _require_read_scope(principal: Principal) -> None:
    """An integrator key needs ``topups:read``; a portal token never does (it reads itself)."""
    if principal.kind == "integrator" and "topups:read" not in principal.scopes:
        raise InsufficientScope(details={"required": "topups:read"})


class TopUpService:
    """``getTopUp``, ``listTopUps`` and ``cancelTopUp`` over one store and one clock."""

    def __init__(
        self,
        *,
        clock: Clock,
        top_ups: TopUpStore,
        customers: CustomerDirectory,
        unit_of_work: UnitOfWorkFactory,
    ) -> None:
        self._clock = clock
        self._top_ups = top_ups
        self._customers = customers
        self._unit_of_work = unit_of_work

    # --- reads ---------------------------------------------------------------------------
    def get(self, principal: Principal, cmd: GetTopUp) -> TopUpView:
        """One top-up, if this caller may see it; 404 otherwise, whatever the reason."""
        _require_read_scope(principal)
        row = self._top_ups.get(cmd.top_up_id)
        if row is None or not self._visible(principal, row):
            raise PaymentNotFound()
        return top_up_view(row)

    def list(self, principal: Principal, query: TopUpQuery) -> tuple[list[TopUpView], UUID | None]:
        """One keyset page, with the next cursor when there is more."""
        _require_read_scope(principal)
        customer_id, site_ids = self._scope(principal)
        # A customer's own id wins over any filter they send: the portal cannot ask for
        # somebody else's history by naming it.
        requested = customer_id if customer_id is not None else query.customer_id
        rows = self._top_ups.page(
            customer_id=requested,
            site_ids=site_ids,
            statuses=query.statuses,
            meter_id=query.meter_id,
            provider=query.provider,
            provider_reference=query.provider_reference,
            late=query.late,
            created_after=query.created_after,
            created_before=query.created_before,
            after_id=query.after_id,
            limit=query.limit + 1,
        )
        page = rows[: query.limit]
        # One extra row was read to learn whether another page exists; its id is the cursor only
        # when it does. Returning it unconditionally would tell every client there is more.
        next_cursor = page[-1].id if len(rows) > query.limit and page else None
        return [top_up_view(row) for row in page], next_cursor

    # --- cancel --------------------------------------------------------------------------
    def cancel(self, principal: Principal, cmd: CancelTopUp) -> TopUpView:
        """Cancel a top-up that is still waiting for the provider; a repeat is a no-op."""
        now = self._clock.now()
        with self._unit_of_work(principal) as uow:
            row = self._top_ups.lock(cmd.top_up_id, uow=uow)
            if row is None or not self._visible(principal, row):
                raise PaymentNotFound()
            outcome = TopUp(
                id=row.id, status=row.status, expires_at=row.expires_at, late=row.late
            ).cancel()
            if outcome.changed:
                # `cancel()` refuses anything but a waiting top-up, so the guard accepts this
                # transition; `updated_at` is written here because no trigger touches it.
                self._top_ups.update_fields(row.id, status=outcome.status, updated_at=now)
        stored = self._top_ups.get(cmd.top_up_id)
        if stored is None:  # pragma: no cover - a cancel never deletes a row
            raise PaymentNotFound()
        return top_up_view(stored)

    # --- scope ---------------------------------------------------------------------------
    def _scope(self, principal: Principal) -> tuple[UUID | None, Sequence[UUID] | None]:
        """``(customer_id, site_ids)`` for the SQL: one of them is always set."""
        if _is_customer(principal):
            if principal.customer_id is None:
                raise PaymentNotFound()
            return principal.customer_id, None
        if principal.all_sites or principal.kind != "staff":
            # An integrator's site scope is its key's; slice 15 binds that, and until then it
            # is a caller that has already passed the permission class.
            return None, None
        return None, tuple(principal.site_ids)

    def _visible(self, principal: Principal, row: TopUpRow) -> bool:
        """Whether this caller may see ``row``; the same rule for a read and a cancel."""
        if _is_customer(principal):
            return row.customer_id == principal.customer_id
        if principal.kind != "staff":
            return principal.kind == "integrator"
        if principal.all_sites:
            return True
        if not principal.site_ids:
            # No site assigned: this caller's scope is empty, not everything.
            return False
        try:
            site_id = self._customers.get(row.customer_id).site_id
        except RegistryNotFound:
            # A customer row that is gone (erased) takes its top-ups out of every staff view.
            return False
        return site_id in principal.site_ids
