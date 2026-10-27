"""`ConfirmPayment`: one signed delivery, recorded once, routed once (FR-03, NFR-05).

The order of the steps is the requirement (docs/03 6.1, ADR-0004):

1. **Verify before touching anything.** Signature first, then the timestamp window, then the
   envelope. A forged delivery must not create a row that could squat a real event id.
2. **The pull happens before the transaction.** A success at or above the configured threshold,
   or one whose payer disagrees with the pending top-up, is confirmed against the provider's own
   status API (D-09, TC-FR-02-6). A provider that cannot answer is 503 with nothing stored: the
   retry is the provider's decision, not ours.
3. **The Redis hint is a read, never a write.** A hit answers ``duplicate`` without a round
   trip; the hint is written only *after* the transaction commits, because a hint written
   before a rollback would turn the provider's retry into a false ``duplicate`` for money the
   hub never recorded.
4. **PostgreSQL decides who won.** ``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` is the
   arbiter: fifty identical deliveries produce one row and forty-nine zero-row answers. Redis
   is an optimisation on top of that, never a substitute.
5. **One transaction, one effect.** The event row, the top-up lock, the ledger posting, the row's
   new status and the outbox event commit together or not at all.

A confirmation whose facts disagree with the top-up does not fail: it goes to **suspense** with
a reason (``domain/routing.py`` owns the priority), because the money arrived and the ledger has
to say where it is. A failed collection moves a waiting top-up to ``failed``; a reversal gives
back what the wallet still holds and books the rest as a receivable (D-06).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from hub.customers.domain.phone import normalise_e164
from hub.ledger.contracts import AccountRef, LedgerService, PostingLine, PostingRequest
from hub.payments.application.ports import (
    ProviderGateway,
    ProviderResolver,
    ProviderTxnStatus,
    VerifiedEvent,
)
from hub.payments.contracts import ProviderEventRow, TopUpRow, WebhookAck
from hub.payments.domain.errors import InvalidTopUpState
from hub.payments.domain.routing import (
    CREDIT,
    PullOutcome,
    classify_confirmation,
    exception_kind,
)
from hub.payments.domain.top_up import CONFIRMED, TopUp
from hub.payments.events import (
    payment_confirmed,
    payment_failed,
    payment_reversed,
    reconciliation_exception,
)
from hub.shared.contracts import KeyValueStore, UnitOfWork
from hub.shared.errors import ExternalUnavailable
from hub.shared.metrics import counter, histogram
from hub.shared.money import Currency
from hub.shared.ports import Clock, IdGenerator
from hub.shared.retry import retry_transient

__all__ = [
    "ACCEPTED",
    "DUPLICATE",
    "ConfirmPayment",
    "EligibilityChecker",
    "FaultHooks",
    "NoFaults",
    "ProviderEventStore",
    "WebhookTopUpStore",
]

logger = logging.getLogger(__name__)

ACCEPTED = "accepted"
DUPLICATE = "duplicate"

# `webhook_events_total{provider,outcome}` and the ack latency the dashboards watch (docs/09 5).
WEBHOOK_EVENTS_TOTAL = counter(
    "webhook_events", ["provider", "outcome"], "Provider deliveries, by outcome"
)
WEBHOOK_ACK_SECONDS = histogram(
    "webhook_ack_seconds", [], documentation="Time to acknowledge one provider delivery"
)

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


class EligibilityChecker(Protocol):
    """Whether the credit may be given to the meter the top-up was opened for (Task 04.8)."""

    def check(self, customer_id: UUID, meter_id: UUID) -> str | None:
        """``None`` when the credit may proceed, else the suspense reason it may not."""
        ...


class FaultHooks(Protocol):
    """Seams a crash test needs; production passes :class:`NoFaults`."""

    def after_ledger_post(self) -> None: ...

    def after_commit(self) -> None: ...


class NoFaults:
    """The production fault hook: every seam does nothing."""

    def after_ledger_post(self) -> None:  # noqa: D102 - see the Protocol
        return None

    def after_commit(self) -> None:  # noqa: D102 - see the Protocol
        return None


@dataclass(frozen=True, slots=True)
class _Dispatched:
    """What routing a delivery produced: the answer, and the reason stored on the event."""

    answer: str
    last_error: str | None = None


class ConfirmPayment:
    """``receiveProviderWebhook``: the synchronous, exactly-once delivery path."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        provider: ProviderResolver,
        top_ups: WebhookTopUpStore,
        events: ProviderEventStore,
        ledger: LedgerService,
        eligibility: EligibilityChecker,
        cache: KeyValueStore,
        unit_of_work: UnitOfWorkFactory,
        pull_thresholds: Mapping[str, int] | None = None,
        fault: FaultHooks | None = None,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._provider = provider
        self._top_ups = top_ups
        self._events = events
        self._ledger = ledger
        self._eligibility = eligibility
        self._cache = cache
        self._unit_of_work = unit_of_work
        self._pull_thresholds = dict(pull_thresholds or {})
        self._fault = fault or NoFaults()

    # --- entry points ---------------------------------------------------------------------
    def run(self, raw: bytes, headers: Mapping[str, str], provider: str) -> WebhookAck:
        """Verify, record once, route, and answer the provider."""
        started = time.perf_counter()
        try:
            ack = self._run(raw, headers, provider)
        finally:
            WEBHOOK_ACK_SECONDS.observe(time.perf_counter() - started)
        try:
            WEBHOOK_EVENTS_TOTAL.labels(provider=provider, outcome=ack.status).inc()
        except Exception:  # noqa: BLE001 - a metric must never fail a delivery
            logger.warning("payments.webhook_metric_failed", extra={"provider": provider})
        return ack

    def _run(self, raw: bytes, headers: Mapping[str, str], provider: str) -> WebhookAck:
        now = self._clock.now()
        # The route's provider decides which secret verifies the delivery: a body signed for
        # one provider must never be accepted on another provider's route.
        gateway = self._provider(provider)
        data = gateway.verify_webhook(headers, raw, now=now)

        if self._hint_says_seen(provider, data.event_id):
            return WebhookAck(status=DUPLICATE, event_id=data.event_id)

        pull = self._pull_decision(gateway, provider, data)
        outcome = retry_transient(lambda: self._record(provider, data, pull))
        if outcome == DUPLICATE:
            # A concurrent delivery of the same event committed first; answer the same way the
            # hint would have, without writing the hint again.
            return WebhookAck(status=DUPLICATE, event_id=data.event_id)

        self._remember(provider, data.event_id)
        self._fault.after_commit()
        return WebhookAck(status=ACCEPTED, event_id=data.event_id)

    def replay(self, event_row_id: UUID, data: VerifiedEvent, provider: str) -> str | None:
        """Re-drive a stored event through the same path (Task 04.10).

        The row already exists and already verified, so this skips insertion and the hint: it
        locks the top-up, routes, and marks the event. The posting keys are the same, so a
        replay after a successful delivery is a no-op rather than a second effect.
        """
        pull = self._pull_decision(self._provider(provider), provider, data)
        now = self._clock.now()

        def once() -> _Dispatched:
            with self._unit_of_work(None) as uow:
                return self._route_and_mark(event_row_id, data, provider, uow, now, pull)

        # The same retry the live path gets: a deadlock victim here is the same deadlock.
        return retry_transient(once).last_error

    # --- the transaction ------------------------------------------------------------------
    def _record(self, provider: str, data: VerifiedEvent, pull: PullOutcome | None) -> str:
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

            self._route_and_mark(event_row_id, data, provider, uow, now, pull)
        return ACCEPTED

    def _route_and_mark(
        self,
        event_row_id: UUID,
        data: VerifiedEvent,
        provider: str,
        uow: UnitOfWork,
        now: datetime,
        pull: PullOutcome | None,
    ) -> _Dispatched:
        """Lock the top-up, dispatch on the event type, and record the outcome once."""
        top_up = self._top_ups.lock_by_provider_ref(provider, str(data.external_reference), uow=uow)
        if top_up is None:
            self._park_unknown_reference(data, event_row_id, uow, now)
            return _Dispatched(answer=ACCEPTED, last_error="unknown_reference")

        if data.type == "payment.failed":
            last_error = self._fail(top_up, data, event_row_id, uow, now)
        elif data.type == "payment.reversed":
            last_error = self._reverse(top_up, data, event_row_id, uow, now)
        else:
            last_error = self._confirm(top_up, data, event_row_id, uow, now, pull)

        # One mark for every routed delivery, so a reason returned by a branch cannot be
        # overwritten by this statement's default (it was, before this was one call).
        self._events.mark(event_row_id, status="processed", processed_at=now, last_error=last_error)
        return _Dispatched(answer=ACCEPTED, last_error=last_error)

    # --- the confirmation -----------------------------------------------------------------
    def _confirm(
        self,
        top_up: TopUpRow,
        data: VerifiedEvent,
        event_row_id: UUID,
        uow: UnitOfWork,
        now: datetime,
        pull: PullOutcome | None,
    ) -> str | None:
        """Credit the wallet, or park the money with the reason that stopped the credit.

        The credit itself is three writes the database's rules force into order:

        1. **The evidence first.** ``hub.post_ledger_transaction`` reads the top-up's stored
           ``provider_event_id`` and refuses a credit whose event disagrees on amount, currency
           or provider transaction id. So the row is pointed at the event before the ledger is
           asked for anything — and a top-up still ``initiated`` also records the
           ``initiated -> pending`` step the guard requires.
        2. **Then the posting.** ``posting_key = topup:<id>:confirm`` is the at-most-once guard:
           a replay returns the same transaction instead of a second effect.
        3. **Then the row.** ``confirmed`` needs its ledger transaction and its instant in the
           same statement (``ck_top_ups_confirmed``), and ``late`` can only be set alongside it
           (``ck_top_ups_late``).

        The posting assumes the wallet exists. It does: ``InitiateTopUp`` calls
        ``ensure_wallet`` in the transaction that inserted the row, and ``create_wallet_account``
        is executed only by roles that hold its grant — which the webhook pool does not.
        """
        payer_ok = _payer_matches(top_up.payer_msisdn, data.payer_msisdn)
        eligibility = self._eligibility.check(top_up.customer_id, top_up.meter_id)
        route = classify_confirmation(
            status=top_up.status,
            top_up_amount_minor=top_up.amount_minor,
            top_up_currency=top_up.currency,
            event_amount_minor=data.amount.amount_minor,
            event_currency=data.amount.currency,
            payer_ok=payer_ok,
            eligibility=eligibility,
            pull=pull,
        )
        if route is not CREDIT:
            return self._to_suspense(
                top_up, data, event_row_id, uow, now, route.reason, payer_ok, pull
            )

        current = TopUp(
            id=top_up.id, status=top_up.status, expires_at=top_up.expires_at, late=top_up.late
        )
        try:
            outcome = current.confirm(now)
        except InvalidTopUpState:
            # `classify_confirmation` already routed every status that cannot be credited, so
            # reaching this means the two disagree — and the database is the authority.
            return self._to_suspense(
                top_up, data, event_row_id, uow, now, "top_up_failed_state", payer_ok, pull
            )

        evidence: dict[str, Any] = {
            "provider_event_id": event_row_id,
            "provider_txn_id": data.provider_reference,
            "payer_matched": payer_ok,
            "updated_at": now,
        }
        if pull is not None:
            # D-09: the amount was confirmed against the provider's own view.
            evidence["status_pull_at"] = now
        if len(outcome.steps) > 1:
            evidence["status"] = outcome.steps[0]
        self._top_ups.update_fields(top_up.id, **evidence)

        amount = top_up.amount
        currency: Currency = amount.currency
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
        self._fault.after_ledger_post()
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

    def _to_suspense(
        self,
        top_up: TopUpRow,
        data: VerifiedEvent,
        event_row_id: UUID,
        uow: UnitOfWork,
        now: datetime,
        reason: str | None,
        payer_ok: bool,
        pull: PullOutcome | None,
    ) -> str:
        """Park the provider's money in ``suspense`` and open an exception for a human (D-05).

        The posted amount is the *event's*, because that is the only figure a disagreement can
        be trusted to state. No wallet is touched, so the customer's balance is unchanged until
        somebody resolves the item.
        """
        if reason is None:  # pragma: no cover - a non-credit route always names a reason
            reason = "statement_only"
        columns: dict[str, Any] = {"payer_matched": payer_ok, "updated_at": now}
        if pull is not None:
            columns["status_pull_at"] = now
        if reason == "pull_disagrees" and top_up.status == "pending":
            # The provider does not agree that this money is settled, so the top-up is no
            # longer simply "pending": the sweeper has to look at it again.
            columns["status"] = "pending_unknown"
        self._top_ups.update_fields(top_up.id, **columns)

        currency: Currency = data.amount.currency
        self._ledger.post_suspense(
            posting_key=f"topup:{top_up.id}:suspense:{data.event_id}",
            provider_event_id=event_row_id,
            clearing=AccountRef(
                kind="provider_clearing", currency=currency, provider=top_up.provider
            ),
            source_top_up_id=top_up.id,
            uow=uow,
        )
        uow.outbox.add(
            reconciliation_exception(
                ids=self._ids,
                occurred_at=now,
                aggregate_id=top_up.id,
                customer_id=top_up.customer_id,
                amount=data.amount,
                reason_code=reason,
                provider_event_id=event_row_id,
                kind=exception_kind(reason),
            )
        )
        return reason

    # --- the other two routes ---------------------------------------------------------------
    def _reverse(
        self,
        top_up: TopUpRow,
        data: VerifiedEvent,
        event_row_id: UUID,
        uow: UnitOfWork,
        now: datetime,
    ) -> str | None:
        """Undo the credit; the wallet gives back what it still holds and the rest is a receivable.

        ``reverse_ledger_transaction`` decides the split (D-06): energy already issued from the
        wallet is not money the customer has, so the shortfall is booked to
        ``chargeback_receivable`` and the event says how much it was.
        """
        if top_up.status != CONFIRMED or top_up.ledger_transaction_id is None:
            # A reversal for money this hub never credited: nothing to post, but still a fact a
            # person has to reconcile.
            uow.outbox.add(
                reconciliation_exception(
                    ids=self._ids,
                    occurred_at=now,
                    aggregate_id=top_up.id,
                    customer_id=top_up.customer_id,
                    amount=data.amount,
                    reason_code="nothing_to_reverse",
                    provider_event_id=event_row_id,
                    kind="statement_only",
                )
            )
            return "nothing_to_reverse"

        result = self._ledger.reverse(
            original_transaction_id=top_up.ledger_transaction_id,
            posting_key=f"topup:{top_up.id}:reverse",
            provider_event_id=event_row_id,
            uow=uow,
        )
        shortfall = result.shortfall.amount_minor
        self._top_ups.update_fields(
            top_up.id,
            status="reversed",
            reversal_transaction_id=result.reversal_transaction_id,
            updated_at=now,
        )
        uow.outbox.add(
            payment_reversed(
                ids=self._ids,
                occurred_at=now,
                top_up_id=top_up.id,
                customer_id=top_up.customer_id,
                currency=top_up.currency,
                reversed_minor=top_up.amount_minor - shortfall,
                shortfall_minor=shortfall,
                ledger_transaction_id=result.reversal_transaction_id,
                chargeback_transaction_id=(
                    result.reversal_transaction_id if shortfall > 0 else None
                ),
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
        del event_row_id
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

    # --- the pull ---------------------------------------------------------------------------
    def _pull_decision(
        self, gateway: ProviderGateway, provider: str, data: VerifiedEvent
    ) -> PullOutcome | None:
        """Whether the provider's own view is needed, and what it said when it was (D-09).

        A pull is *not* free: it is a synchronous call to a third party inside the provider's
        retry budget. So it happens only where the delivery alone cannot be trusted — an amount
        at or above the configured threshold, or a payer number that disagrees with the pending
        top-up. Anything else is decided by the signed delivery, which is what the HMAC is for.
        """
        if data.type != "payment.confirmed":
            return None
        pending = self._top_ups.peek_by_provider_ref(provider, str(data.external_reference))
        if pending is None:
            return None
        payer_ok = _payer_matches(pending.payer_msisdn, data.payer_msisdn)
        threshold = self._pull_thresholds.get(data.amount.currency)
        above = threshold is not None and data.amount.amount_minor >= threshold
        if not above and payer_ok:
            return None
        try:
            status = gateway.fetch_status(data.provider_reference)
        except TimeoutError as exc:
            # docs/03 6.1: no reliable answer means nothing is stored and the provider is told
            # to retry. Guessing here would be guessing about money.
            raise ExternalUnavailable(details={"reason": "pull_unreachable"}) from exc
        return _pull_outcome(status)

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


def _pull_outcome(status: ProviderTxnStatus) -> PullOutcome:
    """The provider's answer, flattened to the three facts routing compares."""
    amount = status.amount
    return PullOutcome(
        status=status.status,
        amount_minor=None if amount is None else amount.amount_minor,
        currency=None if amount is None else str(amount.currency),
    )


def _payer_matches(expected: str | None, delivered: str | None) -> bool:
    """Whether the delivery's payer is the number the top-up was opened for (D-09).

    Only a comparison the code actually made answers ``True``: a delivery that names no payer
    cannot contradict the pending top-up, and the two sides are compared in E.164.
    """
    if delivered is None or expected is None:
        return True
    try:
        return normalise_e164(delivered) == normalise_e164(expected)
    except ValueError:
        return delivered.strip() == expected.strip()


def _provider_txn_id(data: VerifiedEvent) -> str:
    """The provider's transaction id as `hub.provider_events` stores it.

    The envelope only names the provider's reference, and the unique key is
    ``(provider, provider_txn_id)``, so the type is part of the id: a failure is stored under
    ``:failed`` (a later success for the same charge is not swallowed as a duplicate) and a
    reversal under ``-R`` (it is the same charge, undone, and not a second event).
    """
    if data.type == "payment.failed":
        return f"{data.provider_reference}:failed"
    if data.type == "payment.reversed":
        return f"{data.provider_reference}-R"
    return data.provider_reference
