"""Sites and plans (FR-01): the two registry tables nobody bills against directly.

Deliberate decisions:

* **Site scope comes from the principal.** A non-``all_sites`` principal passes the sites it
  holds to the store and a site outside them is ``None``, which becomes 404 — never 403, so a
  support agent cannot discover that a site exists by its answer (D-14).
* **Plans are currency-bound.** ``min_top_up`` and ``max_top_up`` must be in the plan's own
  currency and ordered; a plan's currency never changes, and the limits of a plan that is no
  longer a draft are locked (422 ``plan_locked``), because customers are already priced by it.
* **Every write is audited on the ``registry`` stream**, with ids and field names only: a site
  name is not personal data, but the habit is what keeps a later customer payload clean.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from hub.customers.contracts import (
    MeterCounter,
    PlanRow,
    PlanStore,
    PlanTariffRow,
    SiteRow,
    SiteStore,
)
from hub.customers.domain.errors import InvalidStateTransition, PlanLocked, RegistryNotFound
from hub.identity.contracts import AuditLog, Principal
from hub.shared.contracts import UnitOfWork
from hub.shared.errors import Duplicate, ValidationFailed
from hub.shared.ports import Clock

__all__ = [
    "CreatePlan",
    "CreateSite",
    "GetPlan",
    "GetSite",
    "ListPlans",
    "ListSites",
    "PlanInput",
    "PlanService",
    "SiteInput",
    "SiteService",
    "UpdatePlan",
    "UpdateSite",
]

UnitOfWorkFactory = Callable[[Principal | None], UnitOfWork]

SITE_STATUSES = ("planned", "active", "decommissioned")
PLAN_STATUSES = ("draft", "active", "retired")
# Lifecycle is a one-way walk: a retired plan is not revived, a site is not un-planned.
_PLAN_TRANSITIONS = {"draft": {"active"}, "active": {"retired"}, "retired": set()}


@dataclass(frozen=True, slots=True)
class SiteInput:
    """The mutable half of a site: what a create or an update carries."""

    code: str
    name: str
    timezone: str = "Africa/Lubumbashi"
    status: str = "active"


@dataclass(frozen=True, slots=True)
class CreateSite:
    site: SiteInput


@dataclass(frozen=True, slots=True)
class UpdateSite:
    site_id: UUID
    name: str | None = None
    timezone: str | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class GetSite:
    site_id: UUID


@dataclass(frozen=True, slots=True)
class ListSites:
    after_id: UUID | None = None
    limit: int = 25


@dataclass(frozen=True, slots=True)
class PlanInput:
    """A plan as the contract asks for it: amounts are minor units in the plan's currency."""

    code: str
    name: str
    currency: str
    min_topup_minor: int
    max_topup_minor: int
    status: str = "draft"


@dataclass(frozen=True, slots=True)
class CreatePlan:
    plan: PlanInput


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    plan_id: UUID
    name: str | None = None
    min_topup_minor: int | None = None
    max_topup_minor: int | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class GetPlan:
    plan_id: UUID


@dataclass(frozen=True, slots=True)
class ListPlans:
    after_id: UUID | None = None
    limit: int = 25


def _validate_plan_amounts(min_minor: int, max_minor: int) -> None:
    if min_minor <= 0 or max_minor < min_minor:
        raise ValidationFailed(
            details={
                "errors": [
                    {
                        "field": "/max_top_up/amount_minor",
                        "code": "out_of_range",
                        "message": "max_top_up must be at least min_top_up, and both positive",
                    }
                ]
            }
        )


class SiteService:
    """``listSites``, ``createSite``, ``getSite`` and ``updateSite``."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: Any,
        sites: SiteStore,
        meters: MeterCounter,
        audit: AuditLog,
        unit_of_work: UnitOfWorkFactory,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._sites = sites
        self._meters = meters
        self._audit = audit
        self._unit_of_work = unit_of_work

    def _scope(self, principal: Principal) -> list[UUID] | None:
        """``None`` is ``all_sites``; a list (possibly empty) is the caller's own sites."""
        return None if _all_sites(principal) else sorted(principal.site_ids)

    def list(self, principal: Principal, query: ListSites) -> tuple[Sequence[SiteRow], UUID | None]:
        rows = self._sites.list_scoped(
            site_ids=self._scope(principal), after_id=query.after_id, limit=query.limit + 1
        )
        page = rows[: query.limit]
        next_cursor = page[-1].id if len(rows) > query.limit else None
        return page, next_cursor

    def get(self, principal: Principal, query: GetSite) -> SiteRow:
        site = self._sites.get(query.site_id)
        if site is None:
            raise RegistryNotFound()
        scope = self._scope(principal)
        if scope is not None and site.id not in set(scope):
            raise RegistryNotFound()
        return site

    def create(self, principal: Principal, command: CreateSite) -> SiteRow:
        now = self._clock.now()
        existing = self._sites.find_by_code(command.site.code)
        if existing is not None:
            raise Duplicate()
        site = SiteRow(
            id=self._ids.new_id(),
            code=command.site.code,
            name=command.site.name,
            country_code="CD",
            currency="USD",
            timezone=command.site.timezone,
            status=command.site.status,
            created_at=now,
            updated_at=now,
        )
        with self._unit_of_work(principal) as uow:
            self._sites.add(site)
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.site.created",
                entity_type="site",
                entity_id=site.id,
                site_id=site.id,
                details={"site_ref": site.code, "fields": ["code", "name", "timezone", "status"]},
            )
            del uow  # the audit call is the last statement, as the chain requires
        return site

    def update(self, principal: Principal, command: UpdateSite) -> SiteRow:
        current = self.get(principal, GetSite(site_id=command.site_id))
        if command.status is not None and command.status not in SITE_STATUSES:
            raise ValidationFailed()
        if (
            command.status is not None
            and command.status != current.status
            and current.status == "decommissioned"
        ):
            raise InvalidStateTransition()
        updated = replace(
            current,
            name=command.name if command.name is not None else current.name,
            timezone=command.timezone if command.timezone is not None else current.timezone,
            status=command.status if command.status is not None else current.status,
            updated_at=self._clock.now(),
        )
        changed = [
            field
            for field, value in (
                ("name", command.name),
                ("timezone", command.timezone),
                ("status", command.status),
            )
            if value is not None
        ]
        with self._unit_of_work(principal) as uow:
            self._sites.update(updated)
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.site.updated",
                entity_type="site",
                entity_id=updated.id,
                site_id=updated.id,
                details={"fields": changed},
            )
            del uow
        return updated


class PlanService:
    """``listPlans``, ``createPlan``, ``getPlan`` and ``updatePlan``."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: Any,
        plans: PlanStore,
        audit: AuditLog,
        unit_of_work: UnitOfWorkFactory,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._plans = plans
        self._audit = audit
        self._unit_of_work = unit_of_work

    def list(self, principal: Principal, query: ListPlans) -> tuple[Sequence[PlanRow], UUID | None]:
        rows = self._plans.list_all()
        if query.after_id is not None:
            rows = [row for row in rows if row.id > query.after_id]
        page = rows[: query.limit]
        next_cursor = page[-1].id if len(rows) > query.limit else None
        return page, next_cursor

    def get(self, principal: Principal, query: GetPlan) -> PlanRow:
        plan = self._plans.get(query.plan_id)
        if plan is None:
            raise RegistryNotFound()
        return plan

    def current_tariff(self, principal: Principal, plan_id: UUID) -> PlanTariffRow | None:
        """The price in force now: the document's ``current_tariff`` (absent until one lands)."""
        self.get(principal, GetPlan(plan_id=plan_id))
        return self._plans.current_tariff(plan_id, at=self._clock.now())

    def create(self, principal: Principal, command: CreatePlan) -> PlanRow:
        plan_input = command.plan
        _validate_plan_amounts(plan_input.min_topup_minor, plan_input.max_topup_minor)
        now = self._clock.now()
        plan = PlanRow(
            id=self._ids.new_id(),
            code=plan_input.code,
            name=plan_input.name,
            currency=plan_input.currency,
            min_topup_minor=plan_input.min_topup_minor,
            max_topup_minor=plan_input.max_topup_minor,
            status=plan_input.status,
            created_at=now,
            updated_at=now,
        )
        with self._unit_of_work(principal) as uow:
            self._plans.add(plan)
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.plan.created",
                entity_type="plan",
                entity_id=plan.id,
                site_id=None,
                details={"plan_ref": plan.code, "currency": plan.currency},
            )
            del uow
        return plan

    def update(self, principal: Principal, command: UpdatePlan) -> PlanRow:
        current = self.get(principal, GetPlan(plan_id=command.plan_id))
        min_minor = (
            command.min_topup_minor
            if command.min_topup_minor is not None
            else current.min_topup_minor
        )
        max_minor = (
            command.max_topup_minor
            if command.max_topup_minor is not None
            else current.max_topup_minor
        )
        _validate_plan_amounts(min_minor, max_minor)

        limits_change = (min_minor, max_minor) != (current.min_topup_minor, current.max_topup_minor)
        if limits_change and current.status != "draft":
            raise PlanLocked()

        status = command.status if command.status is not None else current.status
        if status != current.status:
            if status not in PLAN_STATUSES:
                raise ValidationFailed()
            if status not in _PLAN_TRANSITIONS.get(current.status, set()):
                raise InvalidStateTransition()

        updated = replace(
            current,
            name=command.name if command.name is not None else current.name,
            min_topup_minor=min_minor,
            max_topup_minor=max_minor,
            status=status,
            updated_at=self._clock.now(),
        )
        changed = [
            field
            for field, value in (
                ("name", command.name),
                ("min_topup_minor", command.min_topup_minor),
                ("max_topup_minor", command.max_topup_minor),
                ("status", command.status),
            )
            if value is not None
        ]
        with self._unit_of_work(principal) as uow:
            self._plans.update(updated)
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.plan.updated",
                entity_type="plan",
                entity_id=updated.id,
                site_id=None,
                details={"fields": changed},
            )
            del uow
        return updated


def _all_sites(principal: Principal) -> bool:
    """``users.all_sites``, read from the database into the principal with the roles.

    It is a scope fact, not a grant: it widens *which rows* the caller sees, never what they
    may do, so a support agent with ``all_sites`` still cannot create a site.
    """
    return principal.all_sites
