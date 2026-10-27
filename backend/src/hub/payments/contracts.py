"""Public surface of `payments`: Protocols, DTOs and domain errors other modules may use.

Only this module is importable from outside the package (docs/03 section 0). The Protocols are
what a later slice injects — slice 14 asks `confirmed_between` and `provider_evidence`, slice 06
asks for the ledger transaction of a confirmation — and the row DTOs are what the repository
returns, so an adapter never leaks a Django model across a boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from hub.shared.money import Money

__all__ = [
    "ConfirmedPaymentRef",
    "InitiateTopUp",
    "PaymentService",
    "ProviderEventRow",
    "ProviderEvidence",
    "TopUpRow",
    "TopUpView",
]


@dataclass(frozen=True, slots=True)
class TopUpRow:
    """One row of ``hub.top_ups``, as the repositories read and write it."""

    id: UUID
    customer_id: UUID
    meter_id: UUID
    provider: str
    provider_ref: str | None
    provider_txn_id: str | None
    payer_msisdn: str | None
    payer_matched: bool | None
    status_pull_at: datetime | None
    amount_minor: int
    currency: str
    plan_tariff_id: UUID
    tariff_minor_per_kwh: int
    energy_wh: int
    status: str
    late: bool
    provider_event_id: UUID | None
    ledger_transaction_id: UUID | None
    reversal_transaction_id: UUID | None
    initiated_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None
    updated_at: datetime

    @property
    def amount(self) -> Money:
        return Money(self.amount_minor, self.currency)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ProviderEventRow:
    """One row of ``hub.provider_events``; the payload is the envelope that was verified."""

    id: UUID
    provider: str
    event_id: str
    provider_txn_id: str
    event_type: str
    amount_minor: int | None
    currency: str | None
    payload: Mapping[str, Any]
    status: str
    last_error: str | None
    received_at: datetime
    processed_at: datetime | None


@dataclass(frozen=True, slots=True)
class TopUpView:
    """``TopUp`` of the contract: what the portal and the staff console read."""

    id: UUID
    customer_id: UUID
    meter_id: UUID
    provider: str
    provider_reference: str | None
    amount: Money
    status: str
    late: bool
    quoted_tariff_minor_per_kwh: int
    quoted_energy_wh: int
    payer_msisdn_masked: str | None
    initiated_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None
    ledger_transaction_id: UUID | None
    reversal_transaction_id: UUID | None

    def document(self) -> dict[str, Any]:
        """The JSON body of the contract, with the payer's number masked (FR-02)."""
        return {
            "id": str(self.id),
            "customer_id": str(self.customer_id),
            "meter_id": str(self.meter_id),
            "provider": self.provider,
            "provider_reference": self.provider_reference,
            "amount": self.amount.document(),
            "status": self.status,
            "late": self.late,
            "quoted_tariff": {
                "amount_minor": self.quoted_tariff_minor_per_kwh,
                "currency": self.amount.currency,
            },
            "quoted_energy_wh": self.quoted_energy_wh,
            "payer_msisdn_masked": self.payer_msisdn_masked,
            "fee": {"amount_minor": 0, "currency": self.amount.currency},
            "initiated_at": _iso(self.initiated_at),
            "expires_at": _iso(self.expires_at),
            "confirmed_at": _iso(self.confirmed_at) if self.confirmed_at is not None else None,
            "ledger_transaction_id": (
                str(self.ledger_transaction_id) if self.ledger_transaction_id else None
            ),
            "reversal_transaction_id": (
                str(self.reversal_transaction_id) if self.reversal_transaction_id else None
            ),
        }


@dataclass(frozen=True, slots=True)
class InitiateTopUp:
    """``TopUpCreate`` of the contract: the amount is the only money the caller chooses."""

    customer_id: UUID | None
    meter_id: UUID
    amount_minor: int
    currency: str
    payer_msisdn: str


@dataclass(frozen=True, slots=True)
class ConfirmedPaymentRef:
    """A confirmed payment a report or a reconciliation batch points at."""

    top_up_id: UUID
    customer_id: UUID
    provider: str
    provider_txn_id: str | None
    amount: Money
    confirmed_at: datetime
    ledger_transaction_id: UUID


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    """What the provider's own API said about a payment, for a reconciliation batch."""

    top_up_id: UUID
    status: str
    amount: Money | None
    payer_msisdn: str | None
    pulled_at: datetime


class PaymentService(Protocol):
    """What other modules may ask of `payments`."""

    def get_top_up(self, top_up_id: UUID) -> TopUpView: ...

    def confirmed_between(
        self, start: datetime, end: datetime, provider: str
    ) -> Sequence[ConfirmedPaymentRef]: ...

    def provider_evidence(self, top_up_id: UUID) -> ProviderEvidence: ...


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
