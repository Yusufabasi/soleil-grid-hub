"""Domain events produced by `ledger` (docs/03 section 4).

Three payloads, one builder each. ``ledger.posted`` is staged by the service in the same
transaction as the posting, so a consumer never sees a transaction that does not exist.
``wallet.low_balance`` exists so notifications can warn a customer *before* the meter refuses a
purchase, and it carries an amount, never the customer's name.

``ledger.chargeback_opened`` is the exception: the reversal function writes that outbox row
itself, inside the same transaction and with the shortfall it computed. Re-emitting it here
would risk two rows for one shortfall, and the row the SQL writes is the one the invariant
tests read. The dataclass below exists so a consumer's strict ``from_payload`` has a partner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from hub.shared.context import correlation_id_var
from hub.shared.events import DomainEvent

__all__ = [
    "ChargebackOpened",
    "LedgerPosted",
    "WalletLowBalance",
    "chargeback_opened_payload",
    "ledger_posted",
    "wallet_low_balance",
]

AGGREGATE_TRANSACTION = "ledger_transaction"


@dataclass(frozen=True, slots=True)
class LedgerPosted:
    """The fields ``ledger.posted`` carries: which posting, of what kind, under which key."""

    transaction_id: str
    kind: str
    posting_key: str
    effective_at: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> LedgerPosted:
        """Strict construction: a consumer refuses an event it does not fully understand."""
        return cls(
            transaction_id=str(payload["transaction_id"]),
            kind=str(payload["kind"]),
            posting_key=str(payload["posting_key"]),
            effective_at=str(payload["effective_at"]),
        )


@dataclass(frozen=True, slots=True)
class WalletLowBalance:
    """A wallet fell below the warning threshold (not below zero: that is a refused debit)."""

    customer_id: str
    currency: str
    balance_minor: int

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> WalletLowBalance:
        return cls(
            customer_id=str(payload["customer_id"]),
            currency=str(payload["currency"]),
            balance_minor=int(payload["balance_minor"]),
        )


@dataclass(frozen=True, slots=True)
class ChargebackOpened:
    """Written by ``hub.reverse_ledger_transaction``; the shortfall the wallet could not cover."""

    original_transaction_id: str
    shortfall_minor: int
    currency: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ChargebackOpened:
        return cls(
            original_transaction_id=str(payload["original_transaction_id"]),
            shortfall_minor=int(payload["shortfall_minor"]),
            currency=str(payload["currency"]),
        )


def _correlation(fallback: UUID) -> UUID:
    raw = correlation_id_var.get()
    if not raw:
        return fallback
    try:
        return UUID(raw)
    except ValueError:
        return fallback


def ledger_posted(
    *,
    ids: Any,
    occurred_at: datetime,
    transaction_id: UUID,
    kind: str,
    posting_key: str,
    effective_at: datetime,
) -> DomainEvent:
    """``ledger.posted``: one balanced transaction now exists."""
    event_id = ids.new_id()
    return DomainEvent(
        event_id=event_id,
        name="ledger.posted",
        version=1,
        occurred_at=occurred_at,
        correlation_id=_correlation(event_id),
        causation_id=None,
        aggregate_type=AGGREGATE_TRANSACTION,
        aggregate_id=transaction_id,
        payload={
            "transaction_id": str(transaction_id),
            "kind": kind,
            "posting_key": posting_key,
            "effective_at": effective_at.isoformat(),
        },
    )


def wallet_low_balance(
    *,
    ids: Any,
    occurred_at: datetime,
    customer_id: UUID,
    account_id: UUID,
    currency: str,
    balance_minor: int,
) -> DomainEvent:
    """``wallet.low_balance``: the wallet is nearly empty, warn before the meter stops."""
    event_id = ids.new_id()
    return DomainEvent(
        event_id=event_id,
        name="wallet.low_balance",
        version=1,
        occurred_at=occurred_at,
        correlation_id=_correlation(event_id),
        causation_id=None,
        aggregate_type="ledger_account",
        aggregate_id=account_id,
        payload={
            "customer_id": str(customer_id),
            "currency": currency,
            "balance_minor": balance_minor,
        },
    )


def chargeback_opened_payload(
    shortfall_minor: int, currency: str, original: UUID
) -> dict[str, Any]:
    """The document the SQL function writes, for a consumer test that does not run SQL."""
    return {
        "original_transaction_id": str(original),
        "shortfall_minor": shortfall_minor,
        "currency": currency,
    }
