"""Initiation: quote, limits, idempotent insert, then the provider (FR-02, TC-FR-02-3/5).

The order is the requirement, not an implementation detail:

1. **T0 commits before the provider is called.** The row, its quote snapshot and its outbox
   event are durable first. A provider that accepted a collection the hub never recorded would
   take a customer's money for nothing, so the hub asks only once it can survive the answer.
2. **The quote is a snapshot.** ``plan_tariffs`` is immutable and the top-up stores the version
   *and* the price it quoted (D-08). A tariff that changes while the customer is paying must
   not change what their money bought.
3. **The provider call is outside any transaction.** A ten-second HTTP call inside one holds a
   connection and its locks open for ten seconds.
4. **An unknown answer is not a failure.** A timeout or a 5xx leaves the top-up
   ``pending_unknown`` — a state the provider may still resolve — never ``failed``, because
   ``failed`` tells the customer their money did not move.

No staff role may initiate (D-14): the customer's id comes from the token, so a support agent
cannot spend somebody else's wallet.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from hub.customers.contracts import CustomerDirectory, PlanView
from hub.customers.domain.masking import mask_phone
from hub.identity.contracts import Principal
from hub.payments.application.ports import CollectionAck, CollectionRequest, ProviderResolver
from hub.payments.contracts import InitiateTopUp, TopUpRow, TopUpView
from hub.payments.domain.errors import (
    AmountOutOfRange,
    InsufficientScope,
    MeterNotActive,
    PaymentNotFound,
    TopUpLimit,
)
from hub.payments.domain.top_up import INITIATED, TopUp
from hub.payments.events import top_up_initiated
from hub.shared.contracts import UnitOfWork
from hub.shared.errors import PermissionDenied, Unauthenticated
from hub.shared.money import CurrencyMismatch, Money
from hub.shared.ports import Clock, IdGenerator

__all__ = [
    "InitiateTopUpService",
    "MeterFacts",
    "MeterStatusLookup",
    "TopUpStore",
    "WalletPort",
    "top_up_view",
]

UnitOfWorkFactory = Callable[[Principal | None], UnitOfWork]


class MeterFacts(Protocol):
    """The one meter fact initiation reads: may this meter receive a collection?"""

    @property
    def status(self) -> str: ...


class MeterStatusLookup(Protocol):
    """The meter registry, seen through the payments module's own port.

    ``payments`` may not import ``hub.meters`` (module DAG), so the registry arrives at the
    composition root through the kernel registry and is typed here by what these use cases
    actually use: a status, and the registry's own credit verdict for the credit path.
    ``meters.contracts.MeterRegistry`` satisfies it structurally.
    """

    def get(self, meter_id: UUID) -> MeterFacts: ...

    def assert_usable_for_credit(self, meter_id: UUID, customer_id: UUID) -> None:
        """Raise ``MeterNotUsable`` (``meter_decommissioned`` / ``meter_not_owned``)."""
        ...


# The statuses the guard knows. `initiated -> pending_unknown` is not one of its edges, so a
# timeout walks the two-step path the domain derives from the same table.
PENDING = "pending"
PENDING_UNKNOWN = "pending_unknown"
FAILED = "failed"


class TopUpStore(Protocol):
    """The writes and reads initiation needs of ``hub.top_ups``."""

    def add(self, row: TopUpRow) -> None: ...

    def get(self, top_up_id: UUID) -> TopUpRow | None: ...

    def update_fields(self, top_up_id: UUID, **columns: Any) -> None: ...

    def count_for_meter_since(self, meter_id: UUID, *, since: datetime) -> int: ...

    def count_for_payer_since(self, payer_msisdn: str, *, since: datetime) -> int: ...


class WalletPort(Protocol):
    """The one ledger call initiation makes: the wallet must exist before a posting can name it."""

    def ensure_wallet(self, customer_id: UUID, currency: str, *, uow: UnitOfWork) -> UUID: ...


def top_up_view(row: TopUpRow) -> TopUpView:
    """``TopUpView`` of a row, with the payer's number masked (FR-02)."""
    return TopUpView(
        id=row.id,
        customer_id=row.customer_id,
        meter_id=row.meter_id,
        provider=row.provider,
        # The slice maps the row's own reference: it is the id the provider was told. The
        # provider's transaction id stays internal until a reconciliation batch asks for it.
        provider_reference=row.provider_ref,
        amount=row.amount,
        status=row.status,
        late=row.late,
        quoted_tariff_minor_per_kwh=row.tariff_minor_per_kwh,
        quoted_energy_wh=row.energy_wh,
        payer_msisdn_masked=mask_phone(row.payer_msisdn),
        initiated_at=row.initiated_at,
        expires_at=row.expires_at,
        confirmed_at=row.confirmed_at,
        ledger_transaction_id=row.ledger_transaction_id,
        reversal_transaction_id=row.reversal_transaction_id,
    )


class InitiateTopUpService:
    """``createTopUp``: one collection, quoted once and asked for once."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        customers: CustomerDirectory,
        meters: MeterStatusLookup,
        top_ups: TopUpStore,
        wallet: WalletPort,
        provider: ProviderResolver,
        unit_of_work: UnitOfWorkFactory,
        expiry_minutes: int,
        max_per_meter_24h: int,
        max_per_payer_1h: int,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._customers = customers
        self._meters = meters
        self._top_ups = top_ups
        self._wallet = wallet
        self._provider = provider
        self._unit_of_work = unit_of_work
        self._expiry_minutes = expiry_minutes
        self._max_per_meter_24h = max_per_meter_24h
        self._max_per_payer_1h = max_per_payer_1h

    def __call__(self, cmd: InitiateTopUp, principal: Principal) -> TopUpView:
        customer_id = self._customer_for(principal, cmd)
        now = self._clock.now()
        amount = Money(amount_minor=cmd.amount_minor, currency=cmd.currency)  # type: ignore[arg-type]
        top_up_id = self._ids.new_id()
        expires_at = now + timedelta(minutes=self._expiry_minutes)

        with self._unit_of_work(principal) as uow:
            # 404 before 422: a meter that is not this customer's must not be confirmed to
            # exist, so ownership is decided before its status is even looked at.
            self._require_own_meter(customer_id, cmd.meter_id)
            status = self._meters.get(cmd.meter_id).status
            if status != "active":
                raise MeterNotActive(details={"meter_id": str(cmd.meter_id), "status": status})
            plan = self._customers.current_plan(customer_id)
            self._require_amount(amount, plan)
            self._require_limits(cmd, now)
            self._wallet.ensure_wallet(customer_id, amount.currency, uow=uow)
            quote = self._customers.quote_energy(customer_id, amount.amount_minor, at=now)
            row = TopUpRow(
                id=top_up_id,
                customer_id=customer_id,
                meter_id=cmd.meter_id,
                provider=cmd.provider,
                provider_ref=str(top_up_id),
                provider_txn_id=None,
                payer_msisdn=cmd.payer_msisdn,
                payer_matched=None,
                status_pull_at=None,
                amount_minor=amount.amount_minor,
                currency=amount.currency,
                plan_tariff_id=quote.plan_tariff_id,
                tariff_minor_per_kwh=quote.tariff_minor_per_kwh,
                energy_wh=quote.energy_wh,
                status=INITIATED,
                late=False,
                provider_event_id=None,
                ledger_transaction_id=None,
                reversal_transaction_id=None,
                initiated_at=now,
                expires_at=expires_at,
                confirmed_at=None,
                updated_at=now,
            )
            self._top_ups.add(row)
            uow.outbox.add(
                top_up_initiated(
                    ids=self._ids,
                    occurred_at=now,
                    top_up_id=top_up_id,
                    customer_id=customer_id,
                    meter_id=cmd.meter_id,
                    amount=amount,
                    provider=cmd.provider,
                    expires_at=expires_at,
                )
            )

        ack = self._initiate(row)
        return top_up_view(self._record_answer(top_up_id, ack, now, principal=principal))

    # --- provider -----------------------------------------------------------------------
    def _initiate(self, row: TopUpRow) -> CollectionAck | None:
        """Ask the provider (outside any transaction); ``None`` means the answer is unknown."""
        request = CollectionRequest(
            top_up_id=row.id,
            customer_id=row.customer_id,
            meter_id=row.meter_id,
            amount=row.amount,
            payer_msisdn=str(row.payer_msisdn),
            reference=str(row.provider_ref),
            expires_at=row.expires_at,
        )
        try:
            # The adapter of *this row's* provider: the name the collection will arrive under.
            return self._provider(row.provider).initiate_collection(request)
        except TimeoutError:
            # The collection may exist on the provider's side; only a pull can say.
            return None
        except Exception:  # noqa: BLE001 - any transport failure is an unknown outcome
            return None

    def _record_answer(
        self, top_up_id: UUID, ack: CollectionAck | None, now: datetime, *, principal: Principal
    ) -> TopUpRow:
        """Store what the provider said, in a second short transaction of its own."""
        if ack is None or ack.unknown:
            return self._transition(
                top_up_id, PENDING_UNKNOWN, now, provider_txn_id=None, principal=principal
            )
        if ack.accepted:
            return self._transition(
                top_up_id, PENDING, now, provider_txn_id=ack.provider_txn_id, principal=principal
            )
        return self._transition(top_up_id, FAILED, now, provider_txn_id=None, principal=principal)

    def _transition(
        self,
        top_up_id: UUID,
        target: str,
        now: datetime,
        *,
        provider_txn_id: str | None,
        principal: Principal,
    ) -> TopUpRow:
        """Walk the guard's edges to ``target`` and return the stored row.

        The second transaction announces the same caller as the first one: `hub.top_ups` is
        forced-RLS and its `hub_api` policy asks whether the customer is visible, so a write that
        announced nobody would match no row, update nothing and still return a row that never
        moved — the failure this test found as "initiated" after a 202.
        """
        row = self._top_ups.get(top_up_id)
        if row is None:  # pragma: no cover - the row was inserted in this same call
            raise PaymentNotFound()
        current = TopUp(id=row.id, status=row.status, expires_at=row.expires_at, late=row.late)
        with self._unit_of_work(principal):
            for step in current.path_to(target):
                columns: dict[str, Any] = {"status": step, "updated_at": now}
                if step == PENDING and provider_txn_id is not None:
                    # Stored on the transition that says "the provider accepted", so a pull can
                    # find the transaction later without a second lookup.
                    columns["provider_txn_id"] = provider_txn_id
                self._top_ups.update_fields(top_up_id, **columns)
        stored = self._top_ups.get(top_up_id)
        if stored is None:  # pragma: no cover - an update never deletes a row
            raise PaymentNotFound()
        return stored

    # --- checks -------------------------------------------------------------------------
    def _customer_for(self, principal: Principal, cmd: InitiateTopUp) -> UUID:
        """The customer whose wallet is credited: the token's, or the key scope's."""
        if principal.kind == "customer":
            if principal.customer_id is None:
                raise Unauthenticated()
            # D-14: a portal request that names another customer is ignored, not honoured.
            return principal.customer_id
        if principal.kind == "integrator":
            if "topups:write" not in principal.scopes:
                raise InsufficientScope(details={"required": "topups:write"})
            if cmd.customer_id is None:
                # An integrator key works for a named customer; without one there is no wallet
                # to credit, and guessing would be spending a stranger's money.
                raise PaymentNotFound()
            return cmd.customer_id
        # No staff role may initiate a top-up (D-14), and the permission class says so first.
        raise PermissionDenied()

    def _require_own_meter(self, customer_id: UUID, meter_id: UUID) -> None:
        """404 rather than 403: another customer's meter must not be confirmed to exist."""
        if self._customers.meter_for_customer(customer_id) != meter_id:
            raise PaymentNotFound()

    def _require_amount(self, amount: Money, plan: PlanView) -> None:
        """The plan's currency and its floor and ceiling, in that order."""
        if amount.currency != plan.currency:
            raise CurrencyMismatch(
                details={
                    "errors": [
                        {
                            "field": "/amount/currency",
                            "code": "currency_mismatch",
                            "message": f"The plan bills in {plan.currency}.",
                        }
                    ]
                }
            )
        boundary = (
            ("amount_below_minimum", plan.min_topup_minor, "minimum")
            if amount.amount_minor < plan.min_topup_minor
            else (
                ("amount_above_maximum", plan.max_topup_minor, "maximum")
                if amount.amount_minor > plan.max_topup_minor
                else None
            )
        )
        if boundary is None:
            return
        code, limit, label = boundary
        raise AmountOutOfRange(
            details={
                "errors": [
                    {
                        "field": "/amount/amount_minor",
                        "code": code,
                        "message": f"The {label} is {limit} minor units.",
                    }
                ]
            }
        )

    def _require_limits(self, cmd: InitiateTopUp, now: datetime) -> None:
        """The two ceilings: per meter per day, and per payer per hour."""
        per_meter = self._top_ups.count_for_meter_since(
            cmd.meter_id, since=now - timedelta(hours=24)
        )
        if per_meter >= self._max_per_meter_24h:
            raise TopUpLimit(
                details={"limit": "meter_24h", "max": self._max_per_meter_24h},
                retry_after_seconds=3600,
            )
        per_payer = self._top_ups.count_for_payer_since(
            cmd.payer_msisdn, since=now - timedelta(hours=1)
        )
        if per_payer >= self._max_per_payer_1h:
            raise TopUpLimit(
                details={"limit": "payer_1h", "max": self._max_per_payer_1h},
                retry_after_seconds=600,
            )
