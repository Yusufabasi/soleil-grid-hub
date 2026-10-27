"""Domain events produced by `payments`: one frozen dataclass per event of docs/03 section 4.

Each event has a builder the use case stages in its own transaction and a ``from_payload`` a
consumer uses to read one strictly: an event a consumer does not fully understand is refused
rather than half-read. The names are the contract's — ``topup.initiated``,
``payment.confirmed``, ``payment.failed``, ``payment.reversed`` and
``reconciliation.exception`` — because a downstream filter matches on them.

``PaymentConfirmed`` carries the amount *and* the quoted energy: the token saga (slice 06)
issues energy from this event, and asking it to re-read the tariff would let a price change
between the payment and the tokens it bought (D-08).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from hub.shared.context import correlation_id_var
from hub.shared.events import DomainEvent
from hub.shared.money import Money

__all__ = [
    "PaymentConfirmed",
    "PaymentFailed",
    "PaymentReversed",
    "ReconciliationExceptionRaised",
    "TopUpInitiated",
    "payment_confirmed",
    "payment_failed",
    "payment_reversed",
    "reconciliation_exception",
    "top_up_initiated",
]

AGGREGATE_TOP_UP = "top_up"


@dataclass(frozen=True, slots=True)
class TopUpInitiated:
    """``topup.initiated``: a collection was asked for; no money has moved."""

    top_up_id: str
    customer_id: str
    meter_id: str
    amount_minor: int
    currency: str
    provider: str
    expires_at: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TopUpInitiated:
        return cls(
            top_up_id=str(payload["top_up_id"]),
            customer_id=str(payload["customer_id"]),
            meter_id=str(payload["meter_id"]),
            amount_minor=int(payload["amount_minor"]),
            currency=str(payload["currency"]),
            provider=str(payload["provider"]),
            expires_at=str(payload["expires_at"]),
        )


@dataclass(frozen=True, slots=True)
class PaymentConfirmed:
    """``payment.confirmed``: the wallet was credited once, and which transaction did it."""

    top_up_id: str
    customer_id: str
    meter_id: str
    amount_minor: int
    currency: str
    provider: str
    provider_txn_id: str
    confirmed_at: str
    late: bool
    quoted_energy_wh: int
    tariff_version: str
    ledger_transaction_id: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> PaymentConfirmed:
        return cls(
            top_up_id=str(payload["top_up_id"]),
            customer_id=str(payload["customer_id"]),
            meter_id=str(payload["meter_id"]),
            amount_minor=int(payload["amount_minor"]),
            currency=str(payload["currency"]),
            provider=str(payload["provider"]),
            provider_txn_id=str(payload["provider_txn_id"]),
            confirmed_at=str(payload["confirmed_at"]),
            late=bool(payload["late"]),
            quoted_energy_wh=int(payload["quoted_energy_wh"]),
            tariff_version=str(payload["tariff_version"]),
            ledger_transaction_id=str(payload["ledger_transaction_id"]),
        )


@dataclass(frozen=True, slots=True)
class PaymentFailed:
    """``payment.failed``: the provider refused the collection; nothing was posted."""

    top_up_id: str
    customer_id: str
    provider: str
    failure_code: str | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> PaymentFailed:
        code = payload["failure_code"]
        return cls(
            top_up_id=str(payload["top_up_id"]),
            customer_id=str(payload["customer_id"]),
            provider=str(payload["provider"]),
            failure_code=None if code is None else str(code),
        )


@dataclass(frozen=True, slots=True)
class PaymentReversed:
    """``payment.reversed``: the credit was undone, and what the wallet could not return."""

    top_up_id: str
    customer_id: str
    currency: str
    reversed_minor: int
    shortfall_minor: int
    ledger_transaction_id: str
    chargeback_transaction_id: str | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> PaymentReversed:
        chargeback = payload["chargeback_transaction_id"]
        return cls(
            top_up_id=str(payload["top_up_id"]),
            customer_id=str(payload["customer_id"]),
            currency=str(payload["currency"]),
            reversed_minor=int(payload["reversed_minor"]),
            shortfall_minor=int(payload["shortfall_minor"]),
            ledger_transaction_id=str(payload["ledger_transaction_id"]),
            chargeback_transaction_id=None if chargeback is None else str(chargeback),
        )


@dataclass(frozen=True, slots=True)
class ReconciliationExceptionRaised:
    """``reconciliation.exception``: money the automatic path refused to allocate itself.

    ``item_id`` and ``batch_id`` are null here because slice 14 creates the reconciliation
    item from this event; carrying an id nobody minted would be a lie a report would print.
    """

    customer_id: str | None
    amount_minor: int
    currency: str
    reason_code: str
    provider_event_id: str | None
    kind: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ReconciliationExceptionRaised:
        customer = payload["customer_id"]
        event = payload["provider_event_id"]
        return cls(
            customer_id=None if customer is None else str(customer),
            amount_minor=int(payload["amount_minor"]),
            currency=str(payload["currency"]),
            reason_code=str(payload["reason_code"]),
            provider_event_id=None if event is None else str(event),
            kind=str(payload["kind"]),
        )


def _correlation(fallback: UUID) -> UUID:
    """The request's correlation id when there is one, so the event joins its trace."""
    raw = correlation_id_var.get()
    if not raw:
        return fallback
    try:
        return UUID(raw)
    except ValueError:
        return fallback


def _event(
    *,
    ids: Any,
    name: str,
    occurred_at: datetime,
    aggregate_id: UUID,
    payload: Mapping[str, Any],
) -> DomainEvent:
    event_id = ids.new_id()
    return DomainEvent(
        event_id=event_id,
        name=name,
        version=1,
        occurred_at=occurred_at,
        correlation_id=_correlation(event_id),
        causation_id=None,
        aggregate_type=AGGREGATE_TOP_UP,
        aggregate_id=aggregate_id,
        payload=payload,
    )


def top_up_initiated(
    *,
    ids: Any,
    occurred_at: datetime,
    top_up_id: UUID,
    customer_id: UUID,
    meter_id: UUID,
    amount: Money,
    provider: str,
    expires_at: datetime,
) -> DomainEvent:
    """``topup.initiated``: a collection exists and is waiting for its provider."""
    return _event(
        ids=ids,
        name="topup.initiated",
        occurred_at=occurred_at,
        aggregate_id=top_up_id,
        payload={
            "top_up_id": str(top_up_id),
            "customer_id": str(customer_id),
            "meter_id": str(meter_id),
            "amount_minor": amount.amount_minor,
            "currency": amount.currency,
            "provider": provider,
            "expires_at": expires_at.isoformat(),
        },
    )


def payment_confirmed(
    *,
    ids: Any,
    occurred_at: datetime,
    top_up_id: UUID,
    customer_id: UUID,
    meter_id: UUID,
    amount: Money,
    provider: str,
    provider_txn_id: str,
    confirmed_at: datetime,
    late: bool,
    quoted_energy_wh: int,
    plan_tariff_id: UUID,
    ledger_transaction_id: UUID,
) -> DomainEvent:
    """``payment.confirmed``: credit the wallet once, with the energy the money bought."""
    return _event(
        ids=ids,
        name="payment.confirmed",
        occurred_at=occurred_at,
        aggregate_id=top_up_id,
        payload={
            "top_up_id": str(top_up_id),
            "customer_id": str(customer_id),
            "meter_id": str(meter_id),
            "amount_minor": amount.amount_minor,
            "currency": amount.currency,
            "provider": provider,
            "provider_txn_id": provider_txn_id,
            "confirmed_at": confirmed_at.isoformat(),
            "late": late,
            "quoted_energy_wh": quoted_energy_wh,
            # D-08: the version (not the price) is the audit trail of which quote was used.
            "tariff_version": str(plan_tariff_id),
            "ledger_transaction_id": str(ledger_transaction_id),
        },
    )


def payment_failed(
    *,
    ids: Any,
    occurred_at: datetime,
    top_up_id: UUID,
    customer_id: UUID,
    provider: str,
    failure_code: str | None,
) -> DomainEvent:
    """``payment.failed``: the customer should be told, but nothing was posted."""
    return _event(
        ids=ids,
        name="payment.failed",
        occurred_at=occurred_at,
        aggregate_id=top_up_id,
        payload={
            "top_up_id": str(top_up_id),
            "customer_id": str(customer_id),
            "provider": provider,
            "failure_code": failure_code,
        },
    )


def payment_reversed(
    *,
    ids: Any,
    occurred_at: datetime,
    top_up_id: UUID,
    customer_id: UUID,
    currency: str,
    reversed_minor: int,
    shortfall_minor: int,
    ledger_transaction_id: UUID,
    chargeback_transaction_id: UUID | None,
) -> DomainEvent:
    """``payment.reversed``: the reversal posted, and the shortfall a receivable covers."""
    return _event(
        ids=ids,
        name="payment.reversed",
        occurred_at=occurred_at,
        aggregate_id=top_up_id,
        payload={
            "top_up_id": str(top_up_id),
            "customer_id": str(customer_id),
            "currency": currency,
            "reversed_minor": reversed_minor,
            "shortfall_minor": shortfall_minor,
            "ledger_transaction_id": str(ledger_transaction_id),
            "chargeback_transaction_id": (
                str(chargeback_transaction_id) if chargeback_transaction_id else None
            ),
        },
    )


def reconciliation_exception(
    *,
    ids: Any,
    occurred_at: datetime,
    aggregate_id: UUID,
    customer_id: UUID | None,
    amount: Money,
    reason_code: str,
    provider_event_id: UUID | None,
    kind: str,
) -> DomainEvent:
    """``reconciliation.exception``: a human has to decide what this money is."""
    event_id = ids.new_id()
    return DomainEvent(
        event_id=event_id,
        name="reconciliation.exception",
        version=1,
        occurred_at=occurred_at,
        correlation_id=_correlation(event_id),
        causation_id=None,
        aggregate_type="reconciliation_exception",
        aggregate_id=aggregate_id,
        payload={
            "item_id": None,
            "batch_id": None,
            "customer_id": str(customer_id) if customer_id is not None else None,
            "amount_minor": amount.amount_minor,
            "currency": amount.currency,
            "reason_code": reason_code,
            "provider_event_id": (
                str(provider_event_id) if provider_event_id is not None else None
            ),
            "kind": kind,
        },
    )
