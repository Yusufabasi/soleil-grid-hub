"""Composition of the customers module (docs/03 section 3.3): the only place naming adapters.

The api and application layers see the Protocols in ``customers.contracts``; this module turns
them into the concrete repositories, the kernel clock and the audit adapter. It sits outside
the layer contract on purpose, exactly like ``identity.wiring``.

The clock delegates to ``hub.shared.clock.current`` so a test that installs a frozen clock —
through ``identity.wiring.use_clock`` — freezes tariff effective dates and audit timestamps
in this module too, and so generated ids stay time-ordered with the rest of the system.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings

from hub.customers.application.customers import CustomerService
from hub.customers.application.sites_plans import PlanService, SiteService
from hub.customers.application.tariffs import TariffChangeExecutor, TariffService
from hub.customers.contracts import MeterCounter, ProposalReceipt, WalletBalanceProbe
from hub.customers.infrastructure.repositories import (
    CustomerRepository,
    PlanRepository,
    SiteRepository,
    TariffRepository,
)
from hub.identity import wiring as identity_wiring
from hub.identity.contracts import AuditLog, Principal
from hub.identity.infrastructure.audit import HubAuditLog
from hub.shared import clock as kernel_clock
from hub.shared.contracts import UnitOfWork
from hub.shared.ids import Uuid7Generator
from hub.shared.ports import Clock

__all__ = [
    "audit_log",
    "balance_probe",
    "clock",
    "customer_service",
    "customers",
    "ids",
    "meter_counter",
    "plans",
    "register_approval_executors",
    "reset",
    "sites",
    "tariffs",
    "unit_of_work",
    "use_meter_counter",
]


class _KernelClock:
    """The process clock, read through the kernel's seam (a frozen clock in tests)."""

    def now(self) -> datetime:
        return kernel_clock.current()


_lock = threading.RLock()
_clock: Clock | None = None
_ids: Uuid7Generator | None = None
_sites: SiteService | None = None
_plans: PlanService | None = None
_tariffs: TariffService | None = None
_audit: AuditLog | None = None
_customers: CustomerRepository | None = None
_customer_service: CustomerService | None = None


class _NoMeters:
    """Until the meters module registers its own counter (``MetersConfig.ready``)."""

    def per_site(self) -> dict[UUID, int]:
        return {}


# A process-level registration, like the denial recorder: ``reset`` keeps it, because it is
# composition, not cached state. ``hub.meters`` registers its repository here at start-up so
# this module never imports ``hub.meters.infrastructure`` (the contract allows only its
# contracts and events).
_counter: MeterCounter = _NoMeters()


def reset() -> None:
    """Drop every cached service; tests call it after changing a setting.

    The tariff executor is not dropped with them: it is registered as a *contribution* on
    `identity`'s registry, which keeps contributions across its own resets (composition, not
    cache). An approval kind with no executor is refused, so losing it would look like a
    permission bug rather than the missing registration it is.
    """
    global _clock, _ids, _sites, _plans, _tariffs, _audit, _customers, _customer_service  # noqa: PLW0603
    global _probe  # noqa: PLW0603
    with _lock:
        _clock = _ids = _sites = _plans = _tariffs = _audit = _customers = None
        _customer_service = None
        _probe = _NoWallet()


def clock() -> Clock:
    global _clock  # noqa: PLW0603
    if _clock is None:
        with _lock:
            if _clock is None:
                _clock = _KernelClock()
    return _clock


def ids() -> Uuid7Generator:
    global _ids  # noqa: PLW0603
    if _ids is None:
        with _lock:
            if _ids is None:
                _ids = Uuid7Generator(clock())
    return _ids


def audit_log() -> AuditLog:
    global _audit  # noqa: PLW0603
    if _audit is None:
        with _lock:
            if _audit is None:
                _audit = HubAuditLog()
    return _audit


def customers() -> CustomerRepository:
    global _customers  # noqa: PLW0603
    if _customers is None:
        with _lock:
            if _customers is None:
                _customers = CustomerRepository()
    return _customers


def use_meter_counter(counter: MeterCounter) -> None:
    """Install the meter counter; called by ``hub.meters`` at app start-up."""
    global _counter  # noqa: PLW0603
    _counter = counter


def meter_counter() -> MeterCounter:
    return _counter


def unit_of_work(principal: Principal | None = None) -> UnitOfWork:
    """A transaction that announces the caller (``app.user_id``), as identity does."""
    return identity_wiring.unit_of_work(principal)


def sites() -> SiteService:
    global _sites  # noqa: PLW0603
    if _sites is None:
        with _lock:
            if _sites is None:
                _sites = SiteService(
                    clock=clock(),
                    ids=ids(),
                    sites=SiteRepository(),
                    meters=meter_counter(),
                    audit=audit_log(),
                    unit_of_work=unit_of_work,
                )
    return _sites


def plans() -> PlanService:
    global _plans  # noqa: PLW0603
    if _plans is None:
        with _lock:
            if _plans is None:
                _plans = PlanService(
                    clock=clock(),
                    ids=ids(),
                    plans=PlanRepository(),
                    audit=audit_log(),
                    unit_of_work=unit_of_work,
                )
    return _plans


class _IdentityApprovals:
    """The propose half of `identity`'s maker-checker, adapted to this module's port.

    Only ``propose`` crosses the boundary: a tariff is decided through `/approvals`, where the
    executor registered below runs, so the customers module never holds an approval row.
    """

    def propose(
        self,
        principal: Principal,
        *,
        kind: str,
        payload: Any,
        reason: str,
    ) -> ProposalReceipt:
        approval = identity_wiring.approvals().propose(
            principal, kind=kind, payload=payload, reason=reason
        )
        return ProposalReceipt(id=approval.id, document=approval.document())


def tariffs() -> TariffService:
    """``listPlanTariffs`` and ``createPlanTariff``, on the shared plan repository."""
    global _tariffs  # noqa: PLW0603
    if _tariffs is None:
        with _lock:
            if _tariffs is None:
                _tariffs = TariffService(
                    clock=clock(),
                    plans=PlanRepository(),
                    tariffs=TariffRepository(),
                    approvals=_IdentityApprovals(),
                )
    return _tariffs


def register_approval_executors() -> None:
    """Publish ``tariff.change`` to `identity`'s registry, once per process.

    ``add_approval_executor`` stores it as a contribution, so a registry `identity` rebuilds
    later (its wiring resets in tests) still knows the kind; the executor itself is stateless
    apart from the clock, which is read through the kernel seam on every call.
    """
    identity_wiring.add_approval_executor(
        TariffChangeExecutor.kind,
        required_permission=TariffChangeExecutor.required_permission,
        required_attribute=TariffChangeExecutor.required_attribute,
        execute=TariffChangeExecutor(
            clock=clock(), ids=ids(), tariffs=TariffRepository(), audit=audit_log()
        ),
    )


class _NoWallet:
    """The balance a close must see zero; slice 03 binds the ledger and replaces this."""

    def balance_minor(self, customer_id: UUID) -> int:  # noqa: ARG002 - the port's shape
        return 0


_probe: WalletBalanceProbe = _NoWallet()


def use_balance_probe(probe: WalletBalanceProbe) -> None:
    """Install a wallet probe; slice 03 does this at composition, tests do it directly."""
    global _probe, _customer_service  # noqa: PLW0603
    with _lock:
        _probe = probe
        _customer_service = None


def balance_probe() -> WalletBalanceProbe:
    return _probe


def customer_service() -> CustomerService:
    """The registry's customer use cases, sharing the site and plan stores with the others."""
    global _customer_service  # noqa: PLW0603
    if _customer_service is None:
        with _lock:
            if _customer_service is None:
                from hub.customers.infrastructure.repositories import PlanRepository  # noqa: PLC0415

                _customer_service = CustomerService(
                    clock=clock(),
                    ids=ids(),
                    customers=customers(),
                    sites=SiteRepository(),
                    plans=PlanRepository(),
                    balances=balance_probe(),
                    audit=audit_log(),
                    unit_of_work=unit_of_work,
                    default_phone_prefix=str(settings.HUB_DEFAULT_PHONE_PREFIX),
                )
    return _customer_service
