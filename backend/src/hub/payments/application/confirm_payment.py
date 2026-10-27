"""`ConfirmPayment`: one signed delivery, recorded once, then routed (FR-03, NFR-05).

The order of the steps is the requirement (docs/03 6.1, ADR-0004):

1. **Verify before touching anything.** Signature first, then the timestamp window, then the
   envelope. A forged delivery must not create a row that could squat a real event id.
2. **The Redis hint is a read, never a write.** A hit answers ``duplicate`` without a round
   trip; the hint is only written *after* the transaction commits, because a hint written
   before a rollback would turn the provider's retry into a false ``duplicate`` for money the
   hub never recorded (Review focus 1).
3. **PostgreSQL decides who wins.** ``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` is the
   arbiter: fifty identical deliveries produce one row and forty-nine zero-row answers. Redis
   is an optimisation on top of that, never a substitute.
4. **One transaction, one effect.** The event row, the top-up lock, the ledger posting and the
   outbox row commit together or not at all, so a 503 leaves nothing behind and a retry is
   safe.

This task lands the verification, the event log and the two outcomes that do not move money
(a failed collection, and a delivery naming a top-up this build does not have). The credit and
reversal paths are Tasks 04.5 and 04.9; until they exist, a confirmed delivery answers 503 and
commits nothing, which is the one answer a provider retries rather than trusts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from hub.payments.application.ports import ProviderGateway, VerifiedEvent
from hub.payments.contracts import ProviderEventRow, TopUpRow, WebhookAck
from hub.payments.domain.errors import CreditPathUnavailable
from hub.payments.domain.top_up import TopUp
from hub.payments.events import payment_failed, reconciliation_exception
from hub.shared.contracts import KeyValueStore, UnitOfWork
from hub.shared.ports import Clock, IdGenerator
from hub.shared.retry import retry_transient

__all__ = ["ACCEPTED", "DUPLICATE", "ConfirmPayment", "ProviderEventStore", "WebhookTopUpStore"]

logger = logging.getLogger(__name__)

ACCEPTED = "accepted"
DUPLICATE = "duplicate"

# The hint lives three days: longer than any provider retries, shorter than the event log's
# retention, and it is only ever a shortcut for a row PostgreSQL already holds.
HINT_TTL_SECONDS = 72 * 3600
HINT_PREFIX = "provider_event"

# Envelope type to the stored one: `payment.confirmed` is D-01's `payment.succeeded`.
_STORED_TYPES = {
    "payment.confirmed": "payment.succeeded",
    "payment.failed": "payment.failed",
    "payment.reversed": "payment.reversed",
}

UnitOfWorkFactory = Callable[[Any], UnitOfWork]


class WebhookTopUpStore(Protocol):
    """The two top-up reads and the one write the delivery path makes."""

    def lock_by_provider_ref(
        self, provider: str, provider_ref: str, *, uow: UnitOfWork
    ) -> TopUpRow | None: ...

    def peek_by_provider_ref(self, provider: str, provider_ref: str) -> TopUpRow | None: ...

    def update_fields(self, top_up_id: UUID, **columns: Any) -> None: ...


class ProviderEventStore(Protocol):
    """The delivery log, which is also the idempotency arbiter."""

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
    ) -> UUID | None: ...

    def mark(
        self,
        event_id: UUID,
        *,
        status: str,
        processed_at: datetime | None = None,
        last_error: str | None = None,
    ) -> None: ...

    def get(self, event_id: UUID) -> ProviderEventRow | None: ...


class ConfirmPayment:
    """``receiveProviderWebhook``: the synchronous, exactly-once delivery path."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        provider: ProviderGateway,
        top_ups: WebhookTopUpStore,
        events: ProviderEventStore,
        cache: KeyValueStore,
        unit_of_work: UnitOfWorkFactory,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._provider = provider
        self._top_ups = top_ups
        self._events = events
        self._cache = cache
        self._unit_of_work = unit_of_work

    def run(self, raw: bytes, headers: Mapping[str, str], provider: str) -> WebhookAck:
        """Verify, record once, route, and answer the provider."""
        now = self._clock.now()
        data = self._provider.verify_webhook(headers, raw, now=now)

        if self._hint_says_seen(provider, data.event_id):
            return WebhookAck(status=DUPLICATE, event_id=data.event_id)

        outcome = retry_transient(lambda: self._record(provider, data))
        if outcome == DUPLICATE:
            # A concurrent delivery of the same event committed first; answer the same way the
            # hint would have, without writing the hint again.
            return WebhookAck(status=DUPLICATE, event_id=data.event_id)

        self._remember(provider, data.event_id)
        return WebhookAck(status=ACCEPTED, event_id=data.event_id)

    # --- the transaction ------------------------------------------------------------------
    def _record(self, provider: str, data: VerifiedEvent) -> str:
        """One transaction: event row, top-up lock, effect, outbox row."""
        now = self._clock.now()
        with self._unit_of_work(None) as uow:
            event_row_id = self._events.insert_if_new(
                event_id=self._ids.new_id(),
                provider=provider,
                provider_event_key=data.event_id,
                provider_txn_id=_provider_txn_id(data),
                event_type=_STORED_TYPES[data.type],
                amount_minor=data.amount.amount_minor,
                currency=data.amount.currency,
                payload=data.payload,
                received_at=now,
            )
            if event_row_id is None:
                # Zero rows back: either unique key already exists. Nothing else to do, and
                # nothing to undo — the winner's transaction is the effect.
                return DUPLICATE

            top_up = self._top_ups.lock_by_provider_ref(
                provider, str(data.external_reference), uow=uow
            )
            if top_up is None:
                self._park_unknown_reference(data, event_row_id, uow, now)
                return ACCEPTED

            if data.type == "payment.failed":
                self._fail(top_up, data, event_row_id, uow, now)
            else:
                # Task 04.5 (credit) and 04.9 (reversal) land the money paths. Raising here
                # rolls the event row back with the transaction, so the provider's retry finds
                # no row and no second effect: 503 means "nothing was committed", which is
                # exactly what a retry needs to be safe.
                raise CreditPathUnavailable()

            self._events.mark(event_row_id, status="processed", processed_at=now)
        return ACCEPTED

    def _park_unknown_reference(
        self, data: VerifiedEvent, event_row_id: UUID, uow: UnitOfWork, now: datetime
    ) -> None:
        """A payment for a top-up this hub does not have: record it and hand it to a human."""
        self._events.mark(
            event_row_id,
            status="ignored",
            processed_at=now,
            last_error="unknown_reference",
        )
        uow.outbox.add(
            reconciliation_exception(
                ids=self._ids,
                occurred_at=now,
                aggregate_id=event_row_id,
                customer_id=None,
                amount=data.amount,
                reason_code="unknown_reference",
                provider_event_id=event_row_id,
                kind="statement_only",
            )
        )

    def _fail(
        self,
        top_up: TopUpRow,
        data: VerifiedEvent,
        event_row_id: UUID,
        uow: UnitOfWork,
        now: datetime,
    ) -> None:
        """A refused collection moves a waiting top-up to ``failed``; nothing is posted."""
        current = TopUp(
            id=top_up.id, status=top_up.status, expires_at=top_up.expires_at, late=top_up.late
        )
        if current.status not in ("initiated", "pending", "pending_unknown"):
            # `confirmed`, `expired`, `cancelled` or already `failed`: a later failure must
            # never downgrade a row that already has money behind it.
            self._events.mark(
                event_row_id,
                status="processed",
                processed_at=now,
                last_error="ignored_terminal",
            )
            return
        self._top_ups.update_fields(top_up.id, status="failed", updated_at=now)
        uow.outbox.add(
            payment_failed(
                ids=self._ids,
                occurred_at=now,
                top_up_id=top_up.id,
                customer_id=top_up.customer_id,
                provider=top_up.provider,
                failure_code=data.failure_code,
            )
        )

    # --- the hint -------------------------------------------------------------------------
    def _hint_key(self, provider: str, event_id: str) -> str:
        return f"{HINT_PREFIX}:{provider}:{event_id}"

    def _hint_says_seen(self, provider: str, event_id: str) -> bool:
        """A read. A miss, or a Redis that is down, means "ask PostgreSQL"."""
        try:
            return self._cache.get(self._hint_key(provider, event_id)) is not None
        except Exception:  # noqa: BLE001 - the hint is an optimisation, never a dependency
            logger.warning("payments.hint_read_failed", extra={"provider": provider})
            return False

    def _remember(self, provider: str, event_id: str) -> None:
        """Write the hint after commit; a failure here costs a round trip, never correctness."""
        try:
            self._cache.add_once(
                self._hint_key(provider, event_id), "1", ttl_seconds=HINT_TTL_SECONDS
            )
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning("payments.hint_write_failed", extra={"provider": provider})


def _provider_txn_id(data: VerifiedEvent) -> str:
    """The provider's transaction id as `hub.provider_events` stores it.

    The envelope only names the provider's reference, and the unique key is
    ``(provider, provider_txn_id)``: a failure is stored under a suffixed id so a later success
    for the same charge is not swallowed as a duplicate (Plan note 4).
    """
    if data.type == "payment.failed":
        return f"{data.provider_reference}:failed"
    return data.provider_reference
