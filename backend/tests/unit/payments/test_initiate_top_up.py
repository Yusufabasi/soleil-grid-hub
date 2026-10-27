"""Initiation, without a database (Task 04.2).

Every collaborator is a fake, so these tests are about the *rules* rather than about SQL: the
quote that is stored is the one that was taken, the limits count what the store holds, and the
provider is asked once, after the row is durable. The integration suite proves the same rules
through HTTP against PostgreSQL.
"""

from __future__ import annotations

import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from hub.customers.contracts import CustomerView, EnergyQuote, PlanView
from hub.customers.domain.errors import RegistryNotFound
from hub.identity.contracts import Principal
from hub.meters.contracts import MeterRef
from hub.payments.application.initiate_top_up import InitiateTopUpService
from hub.payments.application.ports import CollectionAck
from hub.payments.contracts import InitiateTopUp, TopUpRow
from hub.payments.domain.errors import (
    AmountOutOfRange,
    MeterNotActive,
    PaymentNotFound,
    TopUpLimit,
)
from hub.payments.infrastructure.fake_provider import FakeProviderGateway
from hub.shared.clock import FrozenClock
from hub.shared.ids import SequentialIdGenerator

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
SITE = uuid.uuid4()
PLAN = uuid.uuid4()
TARIFF = uuid.uuid4()
CUSTOMER = uuid.uuid4()
METER = uuid.uuid4()
PAYER = "+243810000111"


class FakeOutbox:
    """Collects the events a unit of work stages."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def add(self, event: Any) -> None:
        self.events.append(event)


class FakeUnitOfWork(AbstractContextManager["FakeUnitOfWork"]):
    """A unit of work that only records: the transaction is the integration suite's job."""

    def __init__(self) -> None:
        self.outbox = FakeOutbox()
        self.commits = 0
        self.active = False

    def __enter__(self) -> FakeUnitOfWork:
        self.active = True
        return self

    def __exit__(self, *exc: Any) -> None:
        self.active = False
        if exc[0] is None:
            self.commits += 1

    def savepoint(self) -> AbstractContextManager[None]:
        raise NotImplementedError

    def require_active(self) -> None:
        if not self.active:
            raise RuntimeError("no transaction is open")

    def on_commit(self, fn: Any) -> None:
        fn()


class FakeTopUps:
    """``hub.top_ups`` in a dictionary."""

    def __init__(self) -> None:
        self.rows: dict[UUID, TopUpRow] = {}

    def add(self, row: TopUpRow) -> None:
        self.rows[row.id] = row

    def get(self, top_up_id: UUID) -> TopUpRow | None:
        return self.rows.get(top_up_id)

    def update_fields(self, top_up_id: UUID, **columns: Any) -> None:
        self.rows[top_up_id] = replace(self.rows[top_up_id], **columns)

    def count_for_meter_since(self, meter_id: UUID, *, since: datetime) -> int:
        return sum(
            1
            for row in self.rows.values()
            if row.meter_id == meter_id and row.initiated_at >= since
        )

    def count_for_payer_since(self, payer_msisdn: str, *, since: datetime) -> int:
        return sum(
            1
            for row in self.rows.values()
            if row.payer_msisdn == payer_msisdn and row.initiated_at >= since
        )


class FakeCustomers:
    """``CustomerDirectory`` over one customer and one plan."""

    def __init__(
        self,
        *,
        meter: UUID = METER,
        status: str = "active",
        min_minor: int = 100,
        max_minor: int = 100_000,
    ) -> None:
        self.meter = meter
        self.status = status
        self.min_minor = min_minor
        self.max_minor = max_minor
        self.quotes: list[tuple[int, datetime]] = []
        self.ensure_calls: list[tuple[UUID, str]] = []

    def get(self, customer_id: UUID) -> CustomerView:
        if customer_id != CUSTOMER:
            raise RegistryNotFound()
        return CustomerView(
            id=CUSTOMER,
            site_id=SITE,
            plan_id=PLAN,
            full_name="Amani Mbuyi",
            phone_e164="+243810000042",
            email=None,
            locale="fr",
            status=self.status,
            version=1,
        )

    def current_plan(self, customer_id: UUID) -> PlanView:
        self.get(customer_id)
        return PlanView(
            id=PLAN,
            code="P-1",
            name="Plan",
            currency="USD",
            min_topup_minor=self.min_minor,
            max_topup_minor=self.max_minor,
            status="active",
            tariff_minor_per_kwh=25_000,
            plan_tariff_id=TARIFF,
            effective_from=NOW - timedelta(days=1),
        )

    def quote_energy(self, customer_id: UUID, amount_minor: int, *, at: datetime) -> EnergyQuote:
        self.get(customer_id)
        self.quotes.append((amount_minor, at))
        tariff = 25_000
        return EnergyQuote(
            plan_tariff_id=TARIFF,
            tariff_minor_per_kwh=tariff,
            # Whole watt-hours, floored: the same arithmetic the directory uses.
            energy_wh=amount_minor * 1000 // tariff,
        )

    def meter_for_customer(self, customer_id: UUID) -> UUID | None:
        return self.meter

    def customer_for_meter(self, meter_id: UUID) -> UUID | None:
        return CUSTOMER if meter_id == self.meter else None


class FakeMeters:
    """``MeterRegistry`` with one meter whose status a test chooses."""

    def __init__(self, *, meter: UUID = METER, status: str = "active") -> None:
        self.meter = meter
        self.status = status

    def get(self, meter_id: UUID) -> MeterRef:
        if meter_id != self.meter:
            raise PaymentNotFound()
        return MeterRef(
            id=meter_id,
            serial_no="SN-1",
            site_id=SITE,
            customer_id=CUSTOMER,
            status=self.status,
            version=1,
        )

    def assert_usable_for_credit(self, meter_id: UUID, customer_id: UUID) -> None:
        return None


class FakeWallet:
    """The one ledger call initiation makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    def ensure_wallet(self, customer_id: UUID, currency: str, *, uow: Any) -> UUID:
        self.calls.append((customer_id, currency))
        return uuid.uuid4()


@dataclass
class Harness:
    """The service under test, wired to fakes a test can inspect."""

    service: InitiateTopUpService
    top_ups: FakeTopUps
    customers: FakeCustomers
    meters: FakeMeters
    wallet: FakeWallet
    provider: FakeProviderGateway
    units: list[FakeUnitOfWork]
    clock: FrozenClock

    def command(self, **overrides: Any) -> InitiateTopUp:
        base = InitiateTopUp(
            customer_id=None,
            meter_id=METER,
            amount_minor=50_000,
            currency="USD",
            payer_msisdn=PAYER,
            provider="synthetic",
        )
        return replace(base, **overrides)


def harness(
    *,
    meter_status: str = "active",
    min_minor: int = 100,
    max_minor: int = 100_000,
    answer: CollectionAck | None = None,
    failure: BaseException | None = None,
) -> Harness:
    clock = FrozenClock(NOW)
    top_ups = FakeTopUps()
    customers = FakeCustomers(min_minor=min_minor, max_minor=max_minor)
    meters = FakeMeters(status=meter_status)
    wallet = FakeWallet()
    provider = FakeProviderGateway()
    provider.answer = answer
    provider.failure = failure
    units: list[FakeUnitOfWork] = []

    def unit_of_work(principal: Principal | None) -> FakeUnitOfWork:
        unit = FakeUnitOfWork()
        units.append(unit)
        return unit

    service = InitiateTopUpService(
        clock=clock,
        ids=SequentialIdGenerator(),
        customers=customers,
        meters=meters,
        top_ups=top_ups,
        wallet=wallet,
        # The service resolves an adapter by the provider's name; this build has one.
        provider=lambda name: provider,
        unit_of_work=unit_of_work,  # type: ignore[arg-type]
        expiry_minutes=15,
        max_per_meter_24h=10,
        max_per_payer_1h=5,
    )
    return Harness(
        service=service,
        top_ups=top_ups,
        customers=customers,
        meters=meters,
        wallet=wallet,
        provider=provider,
        units=units,
        clock=clock,
    )


def principal() -> Principal:
    return Principal(subject_id=CUSTOMER, kind="customer", customer_id=CUSTOMER)


def stored(h: Harness) -> TopUpRow:
    return next(iter(h.top_ups.rows.values()))


def test_stores_quote_snapshot() -> None:
    """The stored row carries the tariff version and the energy that amount bought."""
    h = harness()

    view = h.service(h.command(amount_minor=50_000), principal())

    row = stored(h)
    assert (row.tariff_minor_per_kwh, row.energy_wh, row.plan_tariff_id) == (25_000, 2_000, TARIFF)
    assert view.quoted_energy_wh == 2_000
    assert view.quoted_tariff_minor_per_kwh == 25_000
    assert h.customers.quotes == [(50_000, NOW)]


def test_energy_rounds_down() -> None:
    """1,001 minor units at 3,000/kWh is 333 Wh: the remainder is never given away."""
    h = harness()
    h.customers.quote_energy = (  # type: ignore[method-assign]
        lambda customer_id, amount_minor, *, at: EnergyQuote(
            plan_tariff_id=TARIFF,
            tariff_minor_per_kwh=3_000,
            energy_wh=amount_minor * 1000 // 3_000,
        )
    )
    h.service(h.command(amount_minor=1_001), principal())

    assert stored(h).energy_wh == 333


def test_amount_below_plan_minimum_raises_amount_out_of_range() -> None:
    h = harness(min_minor=1_000)

    with pytest.raises(AmountOutOfRange) as raised:
        h.service(h.command(amount_minor=999), principal())

    assert raised.value.code == "amount_out_of_range"
    assert h.top_ups.rows == {}
    assert h.provider.calls == []


def test_meter_not_active_raises() -> None:
    h = harness(meter_status="decommissioned")

    with pytest.raises(MeterNotActive) as raised:
        h.service(h.command(), principal())

    assert raised.value.code == "meter_not_active"
    assert h.top_ups.rows == {}


def test_meter_of_another_customer_raises_not_found() -> None:
    h = harness()

    with pytest.raises(PaymentNotFound):
        h.service(h.command(meter_id=uuid.uuid4()), principal())

    assert h.top_ups.rows == {}


def test_eleventh_top_up_for_meter_in_24h_raises_topup_limit() -> None:
    """The meter's ceiling is reached by eleven *different* payers: one payer stops at five."""
    h = harness()
    for attempt in range(10):
        h.service(h.command(payer_msisdn=f"+24381000{attempt:04d}"), principal())

    with pytest.raises(TopUpLimit) as raised:
        h.service(h.command(payer_msisdn="+243810009999"), principal())

    assert raised.value.code == "topup_limit"
    assert raised.value.retry_after_seconds == 3600
    assert len(h.top_ups.rows) == 10


def test_sixth_top_up_for_payer_in_1h_raises_topup_limit() -> None:
    """The per-payer ceiling is independent of the meter: one phone, five attempts an hour."""
    h = harness()
    for _ in range(5):
        h.service(h.command(), principal())

    with pytest.raises(TopUpLimit) as raised:
        h.service(h.command(), principal())

    assert raised.value.details["limit"] == "payer_1h"
    # The per-meter ceiling is 10, so it was the payer's that stopped this one.
    assert len(h.top_ups.rows) == 5


def test_provider_refusal_sets_failed() -> None:
    h = harness(answer=CollectionAck(accepted=False, failure_code="insufficient_funds"))

    h.service(h.command(), principal())

    assert stored(h).status == "failed"


def test_provider_timeout_sets_pending_unknown() -> None:
    """No answer is not a refusal: the money may have moved, so the row says so."""
    h = harness(failure=TimeoutError("no answer"))

    h.service(h.command(), principal())

    assert stored(h).status == "pending_unknown"


def test_provider_accept_sets_pending_and_provider_called_once() -> None:
    h = harness(answer=CollectionAck(accepted=True, provider_txn_id="txn-1"))

    view = h.service(h.command(), principal())

    row = stored(h)
    assert row.status == "pending"
    assert row.provider_txn_id == "txn-1"
    assert len(h.provider.calls) == 1
    assert h.provider.calls[0].reference == str(row.id)

    # The row and its event committed before the provider was asked.
    first = h.units[0]
    assert first.commits == 1
    assert first.outbox.events[0].name == "topup.initiated"
    assert view.status == "pending"


def test_wallet_is_created_before_the_row() -> None:
    h = harness()

    h.service(h.command(), principal())

    assert h.wallet.calls == [(CUSTOMER, "USD")]


def test_integrator_without_scope_is_refused() -> None:
    from hub.payments.domain.errors import InsufficientScope  # noqa: PLC0415

    h = harness()
    integrator = Principal(
        subject_id=uuid.uuid4(), kind="integrator", scopes=frozenset({"topups:read"})
    )

    with pytest.raises(InsufficientScope):
        h.service(h.command(customer_id=CUSTOMER), integrator)

    assert h.top_ups.rows == {}
