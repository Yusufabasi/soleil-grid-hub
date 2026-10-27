"""Composition of the payments module (docs/03 3.3): the only place naming its adapters.

The api and application layers see the Protocols; this module turns them into the two
repositories, the provider gateway and the three services, and reads the settings that decide
how money moves. Three dependencies arrive from neighbours:

* **`customers`** for the customer, its plan and the energy quote — imported through its
  contracts, never its tables.
* **`ledger`** for the wallet and, later, the postings — the same way.
* **`meters`** through the kernel registry: the module DAG forbids `payments` importing
  `hub.meters` at all, so the registry is published under a kernel key and reached by name.

The clock delegates to ``hub.shared.clock.current`` so a frozen clock installed by a test also
freezes expiry, `initiated_at` and the event timestamps.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from django.conf import settings

from hub.customers import wiring as customers_wiring
from hub.customers.contracts import CustomerDirectory
from hub.identity import wiring as identity_wiring
from hub.ledger import wiring as ledger_wiring
from hub.ledger.contracts import LedgerService
from hub.payments.application.confirm_payment import ConfirmPayment
from hub.payments.application.initiate_top_up import (
    InitiateTopUpService,
    MeterStatusLookup,
    WalletPort,
)
from hub.payments.application.ports import ProviderGateway
from hub.payments.application.replay_event import ReplayEvent
from hub.payments.application.sweepers import ExpireTopUps, RetryInitiation, SweepPendingUnknown
from hub.payments.application.top_ups import TopUpService
from hub.payments.application.webhook_log import WebhookLogService
from hub.payments.infrastructure.eligibility import RegistryEligibility
from hub.payments.infrastructure.fake_provider import DEFAULT_FAKE_SECRET, FakeProviderGateway
from hub.payments.infrastructure.provider_http import HttpProviderGateway, envelope_to_event
from hub.payments.infrastructure.quarantine import WebhookQuarantineLog
from hub.payments.infrastructure.repositories import ProviderEventRepository, TopUpRepository
from hub.shared import clock as kernel_clock
from hub.shared.ids import Uuid7Generator
from hub.shared.jobs import JobContext, register_cron, register_handler
from hub.shared.ports import Clock
from hub.shared.queue import OutboxJobQueue
from hub.shared.registry import require

__all__ = [
    "cache",
    "confirm_payment",
    "event_repository",
    "gateway",
    "ids",
    "initiation",
    "knows_provider",
    "ledger",
    "provider_secrets",
    "quarantine_reservations",
    "queue",
    "register_jobs",
    "replay_event",
    "reset",
    "top_ups",
    "unit_of_work",
    "webhook_log",
    "webhook_max_bytes",
]

# The meter registry lives behind the kernel registry: `payments` may import `meters.contracts`
# but never `meters.wiring` (module DAG), exactly as `ledger` reaches `identity`'s services.
METER_REGISTRY_PORT = "meters.registry"


class _KernelClock:
    """The process clock, read through the kernel's seam (a frozen clock in tests)."""

    def now(self) -> datetime:
        return kernel_clock.current()


_lock = threading.RLock()
_clock: Clock | None = None
_ids: Uuid7Generator | None = None
_top_ups: TopUpRepository | None = None
_events: ProviderEventRepository | None = None
_initiation: InitiateTopUpService | None = None
_top_up_service: TopUpService | None = None
_confirm: ConfirmPayment | None = None
_quarantine: WebhookQuarantineLog | None = None
_queue: OutboxJobQueue | None = None
_log: WebhookLogService | None = None
_replay: ReplayEvent | None = None
_expire: ExpireTopUps | None = None
_retry: RetryInitiation | None = None
_sweep: SweepPendingUnknown | None = None
_gateways: dict[str, ProviderGateway] = {}


def reset() -> None:
    """Drop every cached adapter; tests call it after changing a setting."""
    global _clock, _ids, _top_ups, _events, _initiation  # noqa: PLW0603
    global _top_up_service, _confirm, _quarantine  # noqa: PLW0603
    global _queue, _log, _replay, _expire, _retry, _sweep  # noqa: PLW0603
    with _lock:
        _clock = _ids = _top_ups = _events = None
        _initiation = _top_up_service = _confirm = _quarantine = None
        _queue = _log = _replay = _expire = _retry = _sweep = None
        _gateways.clear()


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


def unit_of_work(principal: Any) -> Any:
    """A transaction that announces the caller, as every other module does."""
    return identity_wiring.unit_of_work(principal)


def cache() -> Any:
    """The kernel's key-value store, through identity's wiring so a test reset reaches it."""
    return identity_wiring.store()


def top_up_repository() -> TopUpRepository:
    global _top_ups  # noqa: PLW0603
    if _top_ups is None:
        with _lock:
            if _top_ups is None:
                _top_ups = TopUpRepository()
    return _top_ups


def event_repository() -> ProviderEventRepository:
    global _events  # noqa: PLW0603
    if _events is None:
        with _lock:
            if _events is None:
                _events = ProviderEventRepository()
    return _events


def meters() -> MeterStatusLookup:
    """The meter registry, published by `MetersConfig.ready` (never imported).

    Typed by the payments module's own port: the module DAG forbids ``payments`` importing
    ``hub.meters`` at all, so the kernel registry is the seam and ``MeterStatusLookup`` is
    what this module promises to use of it.
    """
    return cast("MeterStatusLookup", require(METER_REGISTRY_PORT)())


def _customers() -> CustomerDirectory:
    return customers_wiring.customer_directory()


def ledger() -> LedgerService:
    """The ledger's posting surface, which is what the credit path names (docs/03 2.5)."""
    return ledger_wiring.service()


def _wallet() -> WalletPort:
    return cast("WalletPort", ledger_wiring.service())


# --- provider ------------------------------------------------------------------------------
def provider_secrets() -> dict[str, list[str]]:
    """``{provider: [current, previous?]}`` as configured; both are accepted while rotating."""
    configured = settings.HUB_PROVIDER_WEBHOOK_SECRETS
    if not isinstance(configured, dict):  # pragma: no cover - settings parsing refuses this
        return {}
    return {
        str(name): [str(secret) for secret in secrets if str(secret)]
        for name, secrets in configured.items()
    }


def provider_base_urls() -> dict[str, str]:
    configured = settings.HUB_PROVIDER_BASE_URLS
    if not isinstance(configured, dict):  # pragma: no cover - settings parsing refuses this
        return {}
    return {str(name): str(url) for name, url in configured.items()}


def knows_provider(name: str) -> bool:
    """A provider this deployable holds a secret for, and nothing else."""
    return name in provider_secrets()


def webhook_max_bytes() -> int:
    return int(settings.HUB_WEBHOOK_MAX_BODY_BYTES)


def gateway(provider: str) -> ProviderGateway:
    """The adapter for ``provider``: HTTP when a base URL is configured, the stub otherwise."""
    cached = _gateways.get(provider)
    if cached is not None:
        return cached
    with _lock:
        cached = _gateways.get(provider)
        if cached is not None:
            return cached
        secrets = tuple(provider_secrets().get(provider) or (DEFAULT_FAKE_SECRET,))
        base_url = provider_base_urls().get(provider)
        adapter: ProviderGateway
        if base_url:
            adapter = HttpProviderGateway(
                provider=provider,
                base_url=base_url,
                secrets=secrets,
                webhook_tolerance_seconds=int(settings.HUB_WEBHOOK_TOLERANCE_SECONDS),
                initiate_timeout_seconds=float(settings.HUB_PROVIDER_INITIATE_TIMEOUT_S),
                pull_timeout_seconds=float(settings.HUB_PULL_TIMEOUT_S),
            )
        else:
            # The in-memory adapter is a real implementation of the port, so a local run and a
            # test exercise the same signature check the HTTP one does.
            adapter = FakeProviderGateway(secrets=secrets)
        _gateways[provider] = adapter
        return adapter


# --- services ------------------------------------------------------------------------------
def initiation() -> InitiateTopUpService:
    """``createTopUp``, wired to the registry, the wallet and the configured provider."""
    global _initiation  # noqa: PLW0603
    if _initiation is None:
        with _lock:
            if _initiation is None:
                _initiation = InitiateTopUpService(
                    clock=clock(),
                    ids=ids(),
                    customers=_customers(),
                    meters=meters(),
                    top_ups=top_up_repository(),
                    wallet=_wallet(),
                    # The resolver, not one adapter: each top-up names its own provider.
                    provider=gateway,
                    unit_of_work=unit_of_work,
                    expiry_minutes=int(settings.HUB_TOPUP_EXPIRY_MINUTES),
                    max_per_meter_24h=int(settings.HUB_TOPUP_MAX_PER_METER_24H),
                    max_per_payer_1h=int(settings.HUB_TOPUP_MAX_PER_PAYER_1H),
                )
    return _initiation


def top_ups() -> TopUpService:
    """``getTopUp``, ``listTopUps`` and ``cancelTopUp``."""
    global _top_up_service  # noqa: PLW0603
    if _top_up_service is None:
        with _lock:
            if _top_up_service is None:
                _top_up_service = TopUpService(
                    clock=clock(),
                    top_ups=top_up_repository(),
                    customers=_customers(),
                    unit_of_work=unit_of_work,
                )
    return _top_up_service


def eligibility() -> RegistryEligibility:
    """The customer and meter checks the credit path routes on (D-05, D-09)."""
    return RegistryEligibility(customers=_customers(), meters=meters())


def pull_thresholds() -> dict[str, int]:
    """``{currency: minor units}`` at or above which a confirmation is pulled before crediting."""
    configured = settings.HUB_PULL_VERIFY_THRESHOLD_MINOR
    if not isinstance(configured, dict):  # pragma: no cover - settings parsing refuses this
        return {}
    return {str(code): int(amount) for code, amount in configured.items()}


def confirm_payment() -> ConfirmPayment:
    """``receiveProviderWebhook``: verification, event log and routing."""
    global _confirm  # noqa: PLW0603
    if _confirm is None:
        with _lock:
            if _confirm is None:
                _confirm = ConfirmPayment(
                    clock=clock(),
                    ids=ids(),
                    # A delivery is verified and pulled with the adapter of the provider on
                    # its route, never with a default one.
                    provider=gateway,
                    top_ups=top_up_repository(),
                    events=event_repository(),
                    ledger=ledger(),
                    eligibility=eligibility(),
                    cache=cache(),
                    unit_of_work=unit_of_work,
                    pull_thresholds=pull_thresholds(),
                )
    return _confirm


def queue() -> OutboxJobQueue:
    """The durable job queue: the outbox, so the job commits with the request."""
    global _queue  # noqa: PLW0603
    if _queue is None:
        with _lock:
            if _queue is None:
                _queue = OutboxJobQueue(clock=clock(), ids=ids())
    return _queue


def webhook_log() -> WebhookLogService:
    """``listWebhookEvents``, ``getWebhookEvent`` and ``replayWebhookEvent``."""
    global _log  # noqa: PLW0603
    if _log is None:
        with _lock:
            if _log is None:
                _log = WebhookLogService(events=event_repository(), queue=queue())
    return _log


def replay_event() -> ReplayEvent:
    """``payments.replay_event``: decode a stored envelope and re-route it."""
    global _replay  # noqa: PLW0603
    if _replay is None:
        with _lock:
            if _replay is None:
                _replay = ReplayEvent(
                    events=event_repository(),
                    confirm=confirm_payment(),
                    decode=envelope_to_event,
                )
    return _replay


def _expire_top_ups() -> ExpireTopUps:
    global _expire  # noqa: PLW0603
    if _expire is None:
        with _lock:
            if _expire is None:
                _expire = ExpireTopUps(
                    clock=clock(), top_ups=top_up_repository(), unit_of_work=unit_of_work
                )
    return _expire


def _retry_initiation() -> RetryInitiation:
    global _retry  # noqa: PLW0603
    if _retry is None:
        with _lock:
            if _retry is None:
                _retry = RetryInitiation(
                    clock=clock(),
                    top_ups=top_up_repository(),
                    provider=gateway,
                    unit_of_work=unit_of_work,
                )
    return _retry


def _sweep_pending_unknown() -> SweepPendingUnknown:
    global _sweep  # noqa: PLW0603
    if _sweep is None:
        with _lock:
            if _sweep is None:
                _sweep = SweepPendingUnknown(
                    clock=clock(),
                    ids=ids(),
                    top_ups=top_up_repository(),
                    provider=gateway,
                    unit_of_work=unit_of_work,
                )
    return _sweep


# --- jobs -----------------------------------------------------------------------------------
def register_jobs() -> None:
    """Contribute the three sweepers and the replay handler (``PaymentsConfig.ready``).

    Handlers resolve their service on the first run, never here: ``ready`` runs for every
    management command, and composing a provider adapter at start-up would read settings a
    command like ``makemigrations`` has no business touching.
    """

    def replay(ctx: JobContext) -> str | None:
        payload = ctx.payload or {}
        raw = payload.get("event_id")
        if not raw:
            raise ValueError("payments.replay_event needs an event_id in its payload")
        return replay_event()(UUID(str(raw)))

    register_cron(
        name="payments.expire_top_ups",
        cron="* * * * *",
        handler=lambda ctx: _expire_top_ups().run(),
    )
    register_cron(
        name="payments.retry_initiation",
        cron="* * * * *",
        handler=lambda ctx: _retry_initiation().run(),
    )
    register_cron(
        name="payments.sweep_pending_unknown",
        cron="*/2 * * * *",
        handler=lambda ctx: _sweep_pending_unknown().run(),
    )
    register_handler(name="payments.replay_event", handler=replay)


def quarantine_reservations() -> WebhookQuarantineLog:
    """The rejection log the plain view writes to."""
    global _quarantine  # noqa: PLW0603
    if _quarantine is None:
        with _lock:
            if _quarantine is None:
                _quarantine = WebhookQuarantineLog(
                    clock=clock(), ids=ids(), events=event_repository()
                )
    return _quarantine
