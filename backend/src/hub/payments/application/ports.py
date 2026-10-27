"""The ports `payments.application` talks to (docs/03 section 5, the slice's Interfaces block).

Three of them are the provider: it collects money, it signs deliveries, and it can be asked
what it thinks happened. The fourth is the job queue, re-exported from the kernel so a use case
never imports `hub.shared.jobs`' concrete registry.

The DTOs are frozen and carry no Django types, because the application layer may not import
an outer layer: an adapter maps its HTTP response into :class:`CollectionAck` and its envelope
into :class:`VerifiedEvent`, and nothing above this file knows what speaks to the provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from hub.shared.money import Money

__all__ = [
    "CollectionAck",
    "CollectionRequest",
    "EligibilityChecker",
    "ProviderGateway",
    "ProviderTxnStatus",
    "VerifiedEvent",
]

# The envelope types a provider may send; anything else is a malformed delivery (422).
EventType = Literal["payment.confirmed", "payment.failed", "payment.reversed"]
TxnStatus = Literal["successful", "pending", "failed", "reversed", "not_found"]


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    """One request to pull money from a payer's wallet.

    ``reference`` is *our* id (the top-up's): the provider is idempotent on it, so a retried
    initiation cannot create a second collection.
    """

    top_up_id: UUID
    customer_id: UUID
    meter_id: UUID
    amount: Money
    payer_msisdn: str
    reference: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CollectionAck:
    """What the provider said about an initiation.

    ``unknown`` is the third outcome the schema names: a timeout or a 5xx leaves the top-up
    ``pending_unknown``, because pretending it failed would let the customer pay for energy the
    hub refuses to credit.
    """

    accepted: bool
    provider_txn_id: str | None = None
    failure_code: str | None = None
    unknown: bool = False


@dataclass(frozen=True, slots=True)
class VerifiedEvent:
    """A delivery whose signature verified, flattened out of the provider's envelope."""

    event_id: str
    type: EventType
    occurred_at: datetime
    provider_reference: str
    external_reference: UUID
    amount: Money
    payer_msisdn: str | None = None
    failure_code: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderTxnStatus:
    """What the provider's status API says about one transaction (the pull of Task 04.8)."""

    status: TxnStatus
    amount: Money | None = None
    payer_msisdn: str | None = None
    currency: str | None = None


class ProviderGateway(Protocol):
    """The provider as the payment paths use it."""

    def initiate_collection(self, req: CollectionRequest) -> CollectionAck:
        """Ask for the money; idempotent on ``req.reference``."""
        ...

    def verify_webhook(
        self, headers: Mapping[str, str], body: bytes, *, now: datetime
    ) -> VerifiedEvent:
        """Verify the signature over ``body`` and parse the envelope."""
        ...

    def fetch_status(self, provider_txn_id: str) -> ProviderTxnStatus:
        """Pull the provider's own view of a transaction."""
        ...


class EligibilityChecker(Protocol):
    """Whether the money may be credited to the meter it was meant for (Task 04.8).

    Declared here because the routing of a confirmation needs it; slice 02's registry and
    slice 03's wallet are the implementations it is composed from.
    """

    def check(self, customer_id: UUID, meter_id: UUID) -> str | None:
        """``None`` when the credit may proceed, else the reason it may not."""
        ...
