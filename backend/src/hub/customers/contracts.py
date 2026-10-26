"""Public surface of `customers`: Protocols, DTOs and domain errors other modules
may use. Nothing else in this package is importable from outside (docs/03 section 0).

Slice 02 adds the registry DTOs of the slice document's Interfaces block plus the row shapes
the repositories return and the use cases write. A ``CustomerView`` is what other modules see;
a ``CustomerRow`` is the whole stored row, version included, and it never leaves the module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

__all__ = [
    "ApprovalProposer",
    "CustomerDirectory",
    "CustomerRow",
    "CustomerView",
    "EnergyQuote",
    "PlanRow",
    "PlanStore",
    "PlanTariffRow",
    "PlanView",
    "ProposalReceipt",
    "SiteRow",
    "SiteStore",
    "SiteView",
    "TariffStore",
    "WalletBalanceProbe",
]


@dataclass(frozen=True, slots=True)
class SiteRow:
    """One row of ``hub.sites`` as the repositories write and read it."""

    id: UUID
    code: str
    name: str
    country_code: str
    currency: str
    timezone: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SiteView:
    """What the API and other modules see of a site (``api/openapi.yaml`` Site)."""

    id: UUID
    code: str
    name: str
    timezone: str
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PlanRow:
    """One row of ``hub.plans``."""

    id: UUID
    code: str
    name: str
    currency: str
    min_topup_minor: int
    max_topup_minor: int
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PlanView:
    """What other modules see of a plan, with the tariff in force at read time."""

    id: UUID
    code: str
    name: str
    currency: str
    min_topup_minor: int
    max_topup_minor: int
    status: str
    tariff_minor_per_kwh: int | None = None
    plan_tariff_id: UUID | None = None
    effective_from: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlanTariffRow:
    """One row of ``hub.plan_tariffs``: an immutable price version (D-08)."""

    id: UUID
    plan_id: UUID
    tariff_minor_per_kwh: int
    effective_from: datetime
    status: str
    proposed_by: UUID | None
    approval_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CustomerRow:
    """One row of ``hub.customers``, version included: the shape a use case writes."""

    id: UUID
    site_id: UUID
    plan_id: UUID
    external_ref: str | None
    full_name: str
    phone_e164: str | None
    email: str | None
    language: str
    status: str
    pseudonym: str | None
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class CustomerView:
    """What another module sees of a customer; ``email`` and ``phone`` may be absent."""

    id: UUID
    site_id: UUID
    plan_id: UUID
    full_name: str
    phone_e164: str | None
    email: str | None
    locale: str
    status: str
    version: int


@dataclass(frozen=True, slots=True)
class EnergyQuote:
    """What an amount of money buys at one tariff (D-08): whole watt-hours, floored."""

    plan_tariff_id: UUID
    tariff_minor_per_kwh: int
    energy_wh: int


class CustomerDirectory(Protocol):
    """The read surface other modules use instead of reading the registry themselves."""

    def get(self, customer_id: UUID) -> CustomerView: ...

    def current_plan(self, customer_id: UUID) -> PlanView: ...

    def quote_energy(
        self, customer_id: UUID, amount_minor: int, *, at: datetime
    ) -> EnergyQuote: ...

    def meter_for_customer(self, customer_id: UUID) -> UUID | None: ...

    def customer_for_meter(self, meter_id: UUID) -> UUID | None: ...


class WalletBalanceProbe(Protocol):
    """The balance a close must see zero; slice 03 binds the ledger, 0 until then."""

    def balance_minor(self, customer_id: UUID) -> int: ...


class SiteStore(Protocol):
    """Persistence of ``hub.sites``, as the use cases need it (no Django, no SQL outside)."""

    def add(self, site: SiteRow) -> None: ...

    def get(self, site_id: UUID) -> SiteRow | None: ...

    def find_by_code(self, code: str) -> SiteRow | None: ...

    def update(self, site: SiteRow) -> None: ...

    def list_scoped(
        self,
        *,
        site_ids: Sequence[UUID] | None,
        after_id: UUID | None = None,
        limit: int = 25,
    ) -> list[SiteRow]: ...


class PlanStore(Protocol):
    """Persistence of ``hub.plans`` and the tariff lookup the plan document needs."""

    def add(self, plan: PlanRow) -> None: ...

    def get(self, plan_id: UUID) -> PlanRow | None: ...

    def update(self, plan: PlanRow) -> None: ...

    def list_all(self) -> list[PlanRow]: ...

    def current_tariff(self, plan_id: UUID, *, at: datetime) -> PlanTariffRow | None: ...


class TariffStore(Protocol):
    """Persistence of the immutable price versions of ``hub.plan_tariffs`` (D-08).

    ``add`` is where the database's two guards live: the unique ``(plan_id, effective_from)``
    and the trigger that refuses a revision beyond the per-revision cap. Both arrive as
    ``IntegrityError`` and leave the adapter as a domain conflict, never as a 500.
    """

    def add(self, tariff: PlanTariffRow) -> None: ...

    def previous(self, plan_id: UUID, *, before: datetime) -> PlanTariffRow | None:
        """The version in force just before ``before``: what the cap is measured against."""
        ...

    def supersede_before(self, plan_id: UUID, *, before: datetime) -> int:
        """Retire every active version whose date is earlier; returns how many moved."""
        ...

    def page(
        self, plan_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[PlanTariffRow]:
        """One keyset page of the history, newest ``effective_from`` first."""
        ...

    def has_pending_proposal(
        self, plan_id: UUID, *, effective_from: datetime, at: datetime
    ) -> bool:
        """True when a live proposal for that plan and instant already waits for a decision.

        ``at`` is the caller's clock: an approval past its expiry can no longer be decided, so
        it must not hold the date hostage.
        """
        ...


@dataclass(frozen=True, slots=True)
class ProposalReceipt:
    """A stored proposal: its id, and the document ``POST .../tariffs`` answers with."""

    id: UUID
    document: Mapping[str, Any]


class ApprovalProposer(Protocol):
    """The maker-checker entry point as this module needs it; `identity` owns the rows.

    The adapter is bound in ``customers.wiring``, so this package never imports
    ``identity.application`` — only the "propose" half of the service crosses the boundary.
    """

    def propose(
        self,
        principal: Any,
        *,
        kind: str,
        payload: Mapping[str, Any],
        reason: str,
    ) -> ProposalReceipt: ...


class MeterCounter(Protocol):
    """Meters per site, for the computed ``meter_count`` the Site document carries."""

    def per_site(self) -> dict[UUID, int]: ...
