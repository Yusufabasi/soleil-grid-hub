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

A confirmation moves money, and the ledger function decides whether it may: it reads the
top-up's *stored* ``provider_event_id`` and refuses a credit whose evidence does not match the
amount, the currency or the provider's transaction id. So the writes inside the transaction
have a fixed order — record the evidence, then post, then mark the row confirmed — and the
ledger is the reason, not a convention.

Two deliveries this build does not route yet answer 503 and commit nothing: a reversal, and a
confirmation of a top-up the hub already gave up on. Both are Tasks 04.8 and 04.9; a 503 is
the one answer a provider retries, and nothing about the money has been decided.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from hub.customers.domain.phone import normalise_e164
from hub.ledger.contracts import AccountRef, LedgerService, PostingLine, PostingRequest
from hub.payments.application.ports import ProviderGateway, VerifiedEvent
from hub.payments.contracts import ProviderEventRow, TopUpRow, WebhookAck
from hub.payments.domain.errors import CreditPathUnavailable, InvalidTopUpState
from hub.payments.domain.top_up import CONFIRMED, TopUp
from hub.payments.events import payment_confirmed, payment_failed, reconciliation_exception
from hub.shared.contracts import KeyValueStore, UnitOfWork
from hub.shared.money import Currency
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
        ledger: LedgerService,
        cache: KeyValueStore,
        unit_of_work: UnitOfWorkFactory,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._provider = provider
        self._top_ups = top_ups
        self._events = events
        self._ledger = ledger
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
                last_error = self._fail(top_up, data, event_row_id, uow, now)
            elif data.type == "payment.reversed":
                # Task 04.9 lands the reversal; raising rolls the event row back with the
                # transaction, so a retry finds no row and no half-decided money.
                raise CreditPathUnavailable(details={"reason": "reversal_not_implemented"})
            else:
                last_error = self._confirm(top_up, data, event_row_id, uow, now)

            # One mark for every routed delivery, so a reason written inside a branch cannot
            # be overwritten by this statement's default (it was, before this was one call).
            self._events.mark(
                event_row_id, status="processed", processed_at=now, last_error=last_error
            )
        return ACCEPTED

    def _confirm(
        self,
        top_up: TopUpRow,
        data: VerifiedEvent,
        event_row_id: UUID,
        uow: UnitOfWork,
        now: datetime,
    ) -> str | None:
        """Credit the wallet once, in three writes the database's own rules force into order.

        1. **The evidence first.** ``hub.post_ledger_transaction`` reads the top-up's stored
           ``provider_event_id`` and refuses the credit when the event's amount, currency or
           provider transaction id disagrees with the top-up. So the row is pointed at the
           event before the ledger is asked for anything — and a first confirmation also
           records the ``initiated -> pending`` step the guard requires.

        The posting assumes the wallet exists. It does: ``InitiateTopUp`` calls
        ``ensure_wallet`` in the transaction that inserted the row, and ``create_wallet_account``
        is executed only by roles that hold its grant — which the webhook pool does not.
        2. **Then the posting.** ``posting_key = topup:<id>:confirm`` is the at-most-once
           guard: a replay returns the same transaction instead of a second effect.
        3. **Then the row.** ``confirmed`` needs its ledger transaction and its instant in the
           same statement (``ck_top_ups_confirmed``), and ``late`` can only be set alongside
           it (``ck_top_ups_late``).
        """
        current = TopUp(
            id=top_up.id, status=top_up.status, expires_at=top_up.expires_at, late=top_up.late
        )
        try:
            outcome = current.confirm(now)
        except InvalidTopUpState as exc:
            # `failed`, `reversed`, or a second distinct charge against a top-up that already
            # has one: none of these can be credited by this build. Task 04.8 routes them to
            # `suspense`; until then the provider is told to retry and nothing is committed,
            # which is the only answer that neither loses the money nor invents an allocation.
            raise CreditPathUnavailable(
                details={"reason": "unroutable_state", "status": current.status}
            ) from exc

        evidence: dict[str, Any] = {
            "provider_event_id": event_row_id,
            "provider_txn_id": data.provider_reference,
            "payer_matched": _payer_matches(top_up.payer_msisdn, data.payer_msisdn),
            "updated_at": now,
        }
        if len(outcome.steps) > 1:
            # A top-up whose provider answer never arrived is still `initiated`; no path
            # credits it in one step, so the intermediate status is written first.
            evidence["status"] = outcome.steps[0]
        self._top_ups.update_fields(top_up.id, **evidence)

        amount = top_up.amount
        currency: Currency = amount.currency
        # No `ensure_wallet` here, and that is a permission, not an omission: the webhook pool
        # connects as `hub_ingest`, which may post through the definer function but may not
        # execute `hub.create_wallet_account`. The wallet already exists — initiation created it
        # in the same transaction that inserted this top-up, as `hub_api`.
        posted = self._ledger.post(
            PostingRequest(
                posting_key=f"topup:{top_up.id}:confirm",
                kind="topup_received",
                lines=[
                    PostingLine(
                        account=AccountRef(
                            kind="provider_clearing", currency=currency, provider=top_up.provider
                        ),
                        direction="debit",
                        amount=amount,
                    ),
                    PostingLine(
                        account=AccountRef(
                            kind="customer_wallet",
                            currency=currency,
                            customer_id=top_up.customer_id,
                        ),
                        direction="credit",
                        amount=amount,
                    ),
                ],
                effective_at=now,
                source_top_up_id=top_up.id,
            ),
            uow=uow,
        )
        self._top_ups.update_fields(
            top_up.id,
            status=CONFIRMED,
            late=outcome.late,
            ledger_transaction_id=posted.transaction_id,
            confirmed_at=now,
            updated_at=now,
        )
        uow.outbox.add(
            payment_confirmed(
                ids=self._ids,
                occurred_at=now,
                top_up_id=top_up.id,
                customer_id=top_up.customer_id,
                meter_id=top_up.meter_id,
                amount=amount,
                provider=top_up.provider,
                provider_txn_id=data.provider_reference,
                confirmed_at=now,
                late=outcome.late,
                quoted_energy_wh=top_up.energy_wh,
                plan_tariff_id=top_up.plan_tariff_id,
                ledger_transaction_id=posted.transaction_id,
            )
        )
        return None

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
    ) -> str | None:
        """A refused collection moves a waiting top-up to ``failed``; nothing is posted.

        Returns the reason to store on the event when the delivery was deliberately ignored.
        """
        current = TopUp(
            id=top_up.id, status=top_up.status, expires_at=top_up.expires_at, late=top_up.late
        )
        if current.status not in ("initiated", "pending", "pending_unknown"):
            # `confirmed`, `expired`, `cancelled` or already `failed`: a later failure must
            # never downgrade a row that already has money behind it.
            return "ignored_terminal"
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
        return None

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


def _payer_matches(expected: str | None, delivered: str | None) -> bool:
    """Whether the delivery's payer is the number the top-up was opened for (D-09).

    The routing decision (``suspense`` on a mismatch) is Task 04.8; this records the fact the
    column exists for, and only claims a comparison it actually made: a delivery that names no
    payer cannot contradict the pending top-up, and the two sides are compared in E.164.
    """
    if delivered is None:
        return True
    if expected is None:
        return True
    try:
        return normalise_e164(delivered) == normalise_e164(expected)
    except ValueError:
        return delivered.strip() == expected.strip()


def _provider_txn_id(data: VerifiedEvent) -> str:
    """The provider's transaction id as `hub.provider_events` stores it.

    The envelope only names the provider's reference, and the unique key is
    ``(provider, provider_txn_id)``: a failure is stored under a suffixed id so a later success
    for the same charge is not swallowed as a duplicate (Plan note 4).
    """
    if data.type == "payment.failed":
        return f"{data.provider_reference}:failed"
    return data.provider_reference
