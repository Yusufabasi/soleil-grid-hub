"""Composition of the meters module (docs/03 section 3.3): the only place that names adapters.

``api`` and ``application`` depend on the Protocols in ``meters.contracts``; this module turns
them into concrete adapters and is the seam the other modules register through.

Two facts arrive from outside this package and both are *registrations*, not imports:

* the registry facts (does this customer exist, is it active, which site is it on) come from
  ``hub.customers`` through ``use_registry_facts``;
* the credit probe comes from the token ledger in slice 06 through ``use_credit_probe``.

Until each is installed the module answers conservatively: no registry means no customer can
be validated, and no credit means zero — which is the safe reading for a probe whose job is to
*block* a decommission.
"""

from __future__ import annotations

import threading
from datetime import datetime
from uuid import UUID

from hub.identity import wiring as identity_wiring
from hub.identity.contracts import AuditLog, Principal
from hub.meters.application.csv_import import MeterCsvImporter
from hub.meters.application.registry import CreditProbe, MeterService
from hub.meters.contracts import CustomerFacts, MeterRegistry, RegistryFacts, SiteFacts
from hub.meters.infrastructure.repositories import MeterRegistryImpl, MeterRepository
from hub.shared import clock as kernel_clock
from hub.shared.contracts import UnitOfWork
from hub.shared.ids import Uuid7Generator
from hub.shared.imports import register_importer
from hub.shared.ports import Clock

__all__ = [
    "csv_importer",
    "ids",
    "meter_service",
    "publish_ports",
    "register_csv_import",
    "registry",
    "registry_facts",
    "repository",
    "reset",
    "unit_of_work",
    "use_credit_probe",
    "use_registry_facts",
]


class _NoCredit:
    """Until slice 06 binds the token ledger: nothing outstanding, so nothing to block."""

    def credit_wh(self, meter_id: UUID) -> int:  # noqa: ARG002 - the port's shape
        return 0


class _NoRegistry:
    """Until `customers` registers its adapter: every customer and site is unknown."""

    def customer(self, customer_id: UUID) -> CustomerFacts | None:  # noqa: ARG002
        return None

    def site(self, site_id: UUID) -> SiteFacts | None:  # noqa: ARG002
        return None

    def site_by_code(self, code: str) -> SiteFacts | None:  # noqa: ARG002
        return None

    def customer_by_external_ref(self, external_ref: str) -> CustomerFacts | None:  # noqa: ARG002
        return None


class _KernelClock:
    """The request clock, so tests can freeze time through the kernel seam."""

    def now(self) -> datetime:
        return kernel_clock.current()


_lock = threading.RLock()
_clock: Clock | None = None
_ids: Uuid7Generator | None = None
_repository: MeterRepository | None = None
_service: MeterService | None = None
# Process-level registrations, like the meter counter `customers` publishes: `reset` keeps
# them, because they are composition rather than cached state.
_registry: RegistryFacts = _NoRegistry()
_credit: CreditProbe = _NoCredit()


def reset() -> None:
    """Drop every cached adapter; tests call it after changing a setting or a probe."""
    global _clock, _ids, _repository, _service  # noqa: PLW0603
    with _lock:
        _clock = None
        _ids = None
        _repository = None
        _service = None


def use_registry_facts(facts: RegistryFacts) -> None:
    """Install the customer/site lookup; called by `hub.meters` at app start-up."""
    global _registry  # noqa: PLW0603
    _registry = facts


def registry_facts() -> RegistryFacts:
    return _registry


def use_credit_probe(probe: CreditProbe) -> None:
    """Install the credit probe; slice 06 (or a test) calls this."""
    global _credit  # noqa: PLW0603
    _credit = probe


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


def repository() -> MeterRepository:
    global _repository  # noqa: PLW0603
    if _repository is None:
        with _lock:
            if _repository is None:
                _repository = MeterRepository()
    return _repository


def registry() -> MeterRegistry:
    """This module's ``MeterRegistry``: the meter facts a money or credit path may rely on.

    Slice 04's top-up and slice 06's token issuance call ``get`` and
    ``assert_usable_for_credit`` instead of reading ``hub.meters`` themselves; the adapter
    composes the repository here so both the registry's own use cases and the neighbours reach
    the same rows through the same code.
    """
    return MeterRegistryImpl(repository())


# The name `payments` asks the kernel registry for: the two modules sit on the same layer of
# the module DAG, so each may see the other's contracts but never its composition root.
REGISTRY_PORT = "meters.registry"


def publish_ports() -> None:
    """Publish the meter registry by name, for a module that may not import this one.

    ``ledger`` uses the same seam for `identity`'s services. The factory is a callable rather
    than an instance, so a reset in a test cannot leave a consumer holding a stale adapter.
    """
    from hub.shared import registry as ports  # noqa: PLC0415 - avoids a cycle at import time

    ports.provide(REGISTRY_PORT, registry)


def unit_of_work(principal: Principal | None = None) -> UnitOfWork:
    """A transaction that announces the caller (``app.user_id``), as identity does."""
    return identity_wiring.unit_of_work(principal)


def _audit() -> AuditLog:
    return identity_wiring.audit_log()


class _RegistryRef:
    """Reads the registration at call time, so installing an adapter later still lands."""

    def customer(self, customer_id: UUID) -> CustomerFacts | None:
        return _registry.customer(customer_id)

    def site(self, site_id: UUID) -> SiteFacts | None:
        return _registry.site(site_id)

    def site_by_code(self, code: str) -> SiteFacts | None:
        return _registry.site_by_code(code)

    def customer_by_external_ref(self, external_ref: str) -> CustomerFacts | None:
        return _registry.customer_by_external_ref(external_ref)


class _CreditRef:
    """Same indirection for the probe: slice 06 may bind it after this service exists."""

    def credit_wh(self, meter_id: UUID) -> int:
        return _credit.credit_wh(meter_id)


def meter_service() -> MeterService:
    global _service  # noqa: PLW0603
    if _service is None:
        with _lock:
            if _service is None:
                _service = MeterService(
                    clock=clock(),
                    ids=ids(),
                    meters=repository(),
                    registry=_RegistryRef(),
                    credit=_CreditRef(),
                    audit=_audit(),
                    unit_of_work=unit_of_work,
                )
    return _service


def csv_importer() -> MeterCsvImporter:
    """The ``meters`` arm of ``manage.py import_registry`` (Task 02.8).

    The registry facts are read through the ref, so a customer suspended since start-up is
    refused a meter now: nothing here caches what `customers` owns.
    """
    return MeterCsvImporter(
        clock=clock(),
        ids=ids(),
        meters=repository(),
        registry=_RegistryRef(),
        audit=_audit(),
        unit_of_work=unit_of_work,
        resolve_actor=identity_wiring.actor_for_email,
    )


def register_csv_import() -> None:
    """Publish this module's importer to the kernel's command (``MetersConfig.ready``)."""
    register_importer(csv_importer())
