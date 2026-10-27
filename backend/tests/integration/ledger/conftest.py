"""Fixtures for the ledger suite: a funded wallet, two finance leads, and a real top-up.

The ledger's only write path is a set of definer functions, and two of them demand evidence a
test cannot fake without building it: ``topup_received`` needs a stored provider success event
that matches the top-up, and ``energy_purchase`` needs a confirmed top-up. This module builds
that evidence the way slice 04 will — a site, a plan with a tariff, a customer, a meter, a
meter assignment, a provider event and a top-up row — so the ledger is not tested against a
shortcut the application never takes.

Everything here runs inside the test's transaction, so it rolls back with it; the concurrency
tests that need real commits seed their own rows and say so.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

import pytest
from django.db import connection
from django.test import Client
from psycopg.types.json import Jsonb

from hub.identity import wiring as identity_wiring
from hub.ledger import wiring
from hub.ledger.api.views import (
    AccountingPeriodCollectionView,
    AccountingPeriodLockView,
    CustomerWalletView,
    LedgerAccountCollectionView,
    LedgerAccountDetailView,
    LedgerAdjustmentView,
    LedgerTransactionCollectionView,
    LedgerTransactionDetailView,
    RefundView,
)
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.shared import clock as kernel_clock
from hub.shared.money import Money
from support.principals import SeededPrincipalAuthentication, SeededUser, seed_staff_user

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
DAY = timedelta(days=1)


@dataclass
class World:
    """One customer with a funded wallet, and the evidence behind the funding."""

    site_id: uuid.UUID
    plan_id: uuid.UUID
    tariff_id: uuid.UUID
    customer_id: uuid.UUID
    wallet_id: uuid.UUID
    meter_id: uuid.UUID
    provider_event_id: uuid.UUID | None = None
    top_up_id: uuid.UUID | None = None
    top_up_transaction_id: uuid.UUID | None = None
    maker: SeededUser | None = None
    checker: SeededUser | None = None

    @property
    def wallet(self) -> AccountRef:
        return AccountRef(kind="customer_wallet", currency="USD", customer_id=self.customer_id)

    @property
    def fees(self) -> AccountRef:
        return AccountRef(kind="fees", currency="USD")

    def account_id(self, code: str) -> uuid.UUID:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM hub.ledger_accounts WHERE code = %s", [code])
            row = cursor.fetchone()
        assert row is not None, code
        return uuid.UUID(str(row[0]))

    def balance(self) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT balance_minor FROM hub.wallet_balances WHERE account_id = %s",
                [self.wallet_id],
            )
            row = cursor.fetchone()
        return int(row[0]) if row else 0


def _seed_registry(*, currency: str = "USD", plan_id: uuid.UUID | None = None) -> tuple[Any, ...]:
    site_id, tariff_id, customer_id, meter_id = (uuid.uuid4() for _ in range(4))
    plan_id = plan_id or uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, %s)",
            [site_id, f"LED-{site_id.hex[:8]}", "Ledger site", currency],
        )
        cursor.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor)"
            " VALUES (%s, %s, %s, %s, 100, 1000000)",
            [plan_id, f"LED-{plan_id.hex[:8]}", "Ledger plan", currency],
        )
        approval_id = uuid.uuid4()
        maker = seed_staff_user("finance_officer", attributes=("finance_lead",)).user_id
        checker = seed_staff_user("finance_officer", attributes=("finance_lead",)).user_id
        # `ck_plan_tariffs_approved`: an active tariff needs the approval that published it.
        cursor.execute(
            "INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, payload,"
            " status, approver_user_id, approver_session_id, decided_at, created_at, expires_at)"
            " VALUES (%s, 'tariff.change', %s, %s, '{}'::jsonb, 'approved', %s, %s, %s, %s, %s)",
            [
                approval_id,
                maker,
                uuid.uuid4(),
                checker,
                uuid.uuid4(),
                NOW - DAY,
                NOW - DAY,
                NOW + 30 * DAY,
            ],
        )
        cursor.execute(
            "INSERT INTO hub.plan_tariffs (id, plan_id, tariff_minor_per_kwh, effective_from,"
            " status, approval_id) VALUES (%s, %s, 500, %s, 'active', %s)",
            [tariff_id, plan_id, NOW - 30 * DAY, approval_id],
        )
        cursor.execute(
            "INSERT INTO hub.customers (id, site_id, plan_id, full_name, status, created_at)"
            " VALUES (%s, %s, %s, %s, 'active', %s)",
            [customer_id, site_id, plan_id, "Ledger Customer", NOW - 30 * DAY],
        )
        # Assigning the meter to the customer is the trigger's job (db/schema.sql line 547):
        # `top_ups` needs the ``(meter_id, customer_id)`` pair it writes, and writing it here
        # too would collide with it.
        cursor.execute(
            "INSERT INTO hub.meters (id, serial_no, site_id, customer_id, status)"
            " VALUES (%s, %s, %s, %s, 'active')",
            [meter_id, f"LED-{meter_id.hex[:10]}", site_id, customer_id],
        )
    return site_id, plan_id, tariff_id, customer_id, meter_id


def store_reversal_event(
    world: World, *, amount_minor: int, provider: str = "mpesa_cd"
) -> uuid.UUID:
    """The stored ``payment.reversed`` event a provider reversal arrives with (D-06)."""
    event_id = uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id,"
            " event_type, amount_minor, currency, payload, status, processed_at)"
            " VALUES (%s, %s, %s, %s, 'payment.reversed', %s, 'USD', '{}'::jsonb,"
            " 'processed', %s)",
            [
                event_id,
                provider,
                f"rev-{event_id.hex[:12]}",
                f"REV-{event_id.hex[:12]}",
                amount_minor,
                NOW,
            ],
        )
    return event_id


def _ensure_wallet(customer_id: uuid.UUID, currency: str = "USD") -> uuid.UUID:
    with wiring.unit_of_work() as uow:
        return wiring.service().ensure_wallet(customer_id, currency, uow=uow)


def fund(
    world: World,
    amount_minor: int,
    *,
    provider: str = "mpesa_cd",
    effective_at: datetime | None = None,
) -> uuid.UUID:
    """Credit the wallet through a real ``topup_received`` posting and confirm the top-up.

    ``effective_at`` is the posting's accounting instant, which the period tests use to place a
    credit in a month before they lock it.
    """
    effective = effective_at or NOW
    event_id, top_up_id = uuid.uuid4(), uuid.uuid4()
    provider_txn = f"TXN-{uuid.uuid4().hex[:12]}"
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id,"
            " event_type, amount_minor, currency, payload, status, processed_at)"
            " VALUES (%s, %s, %s, %s, 'payment.succeeded', %s, 'USD', '{}'::jsonb,"
            " 'processed', %s)",
            [event_id, provider, f"evt-{event_id.hex[:12]}", provider_txn, amount_minor, NOW],
        )
        cursor.execute(
            "INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, provider_txn_id,"
            " amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh, energy_wh,"
            " status, provider_event_id, expires_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'USD', %s, 500, %s, 'initiated', %s, %s)",
            [
                top_up_id,
                world.customer_id,
                world.meter_id,
                provider,
                provider_txn,
                amount_minor,
                world.tariff_id,
                amount_minor * 2,  # amount * 1000 / 500
                event_id,
                NOW + DAY,
            ],
        )
    clearing = world.account_id(f"provider_clearing.{provider}.USD")
    with wiring.unit_of_work() as uow:
        result = wiring.service().post(
            PostingRequest(
                posting_key=f"topup:{top_up_id}:confirm",
                kind="topup_received",
                lines=[
                    PostingLine(
                        account=AccountRef(
                            kind="provider_clearing", currency="USD", provider=provider
                        ),
                        direction="debit",
                        amount=Money(amount_minor, "USD"),
                    ),
                    PostingLine(
                        account=world.wallet, direction="credit", amount=Money(amount_minor, "USD")
                    ),
                ],
                effective_at=effective,
                source_top_up_id=top_up_id,
            ),
            uow=uow,
        )
    with connection.cursor() as cursor:
        # `top_ups_guard` only lets initiated go to pending, failed, expired or cancelled: the
        # push has to be sent before the money can arrive.
        cursor.execute("UPDATE hub.top_ups SET status = 'pending' WHERE id = %s", [top_up_id])
        cursor.execute(
            "UPDATE hub.top_ups SET status = 'confirmed', confirmed_at = %s,"
            " ledger_transaction_id = %s WHERE id = %s",
            [effective, result.transaction_id, top_up_id],
        )
    world.provider_event_id = event_id
    world.top_up_id = top_up_id
    world.top_up_transaction_id = result.transaction_id
    assert clearing
    return top_up_id


def spend(world: World, amount_minor: int, *, posting_key: str | None = None) -> Any:
    """An ``energy_purchase`` debit against the confirmed top-up (the token path of slice 05)."""
    with wiring.unit_of_work() as uow:
        return wiring.service().post(
            PostingRequest(
                posting_key=posting_key or f"token:{uuid.uuid4()}:purchase",
                kind="energy_purchase",
                lines=[
                    PostingLine(
                        account=world.wallet, direction="debit", amount=Money(amount_minor, "USD")
                    ),
                    PostingLine(
                        account=AccountRef(kind="revenue_energy", currency="USD"),
                        direction="credit",
                        amount=Money(amount_minor, "USD"),
                    ),
                ],
                effective_at=NOW,
                source_top_up_id=world.top_up_id,
            ),
            uow=uow,
        )


@pytest.fixture(autouse=True)
def wall_clock() -> Iterator[None]:
    """Measure against the real clock unless a test installs its own.

    Step-up, approval expiry and period dates are all judged against ``hub.shared.clock``, and
    `identity`'s suite freezes that clock to prove its boundaries. A frozen clock left behind
    by an earlier module would make this suite assert the wrong thing in the worst way: a
    session ten minutes old would look *fresh*, because a negative age is still under 300.
    """
    kernel_clock.use_clock(None)
    identity_wiring.use_clock(None)
    yield
    kernel_clock.use_clock(None)


@pytest.fixture
def world() -> World:
    """A customer whose wallet holds 1,000.00 USD, credited by a real top-up."""
    return make_world(funded_minor=100_000)


@pytest.fixture
def make_world_factory() -> Any:
    """The :func:`make_world` helper as a fixture (tests never import a conftest module)."""
    return make_world


@pytest.fixture
def spend_from() -> Any:
    """The :func:`spend` helper: an ``energy_purchase`` debit against the world's top-up."""
    return spend


@pytest.fixture
def fund_wallet() -> Any:
    """The :func:`fund` helper, for a credit placed on a chosen accounting date."""
    return fund


@pytest.fixture
def reversal_event() -> Any:
    """A callable that stores a ``payment.reversed`` provider event (the reversal evidence)."""
    return store_reversal_event


def make_world(*, funded_minor: int = 0, currency: str = "USD") -> World:
    site_id, plan_id, tariff_id, customer_id, meter_id = _seed_registry(currency=currency)
    wallet_id = _ensure_wallet(customer_id, currency)
    world = World(
        site_id=site_id,
        plan_id=plan_id,
        tariff_id=tariff_id,
        customer_id=customer_id,
        wallet_id=wallet_id,
        meter_id=meter_id,
        maker=seed_staff_user("finance_officer", attributes=("finance_lead",)),
        checker=seed_staff_user("finance_officer", attributes=("finance_lead",)),
    )
    if funded_minor:
        fund(world, funded_minor)
    return world


@pytest.fixture
def finance_lead_maker() -> SeededUser:
    return seed_staff_user("finance_officer", attributes=("finance_lead",))


@pytest.fixture
def finance_lead_checker() -> SeededUser:
    return seed_staff_user("finance_officer", attributes=("finance_lead",))


LEDGER_VIEWS = (
    CustomerWalletView,
    LedgerAccountCollectionView,
    LedgerAccountDetailView,
    LedgerTransactionCollectionView,
    LedgerTransactionDetailView,
    LedgerAdjustmentView,
    RefundView,
    AccountingPeriodCollectionView,
    AccountingPeriodLockView,
)


@pytest.fixture
def api() -> Iterator[Client]:
    """A client whose caller comes from the ``X-Test-User`` header.

    The authenticator is patched onto the ledger views, not set through ``REST_FRAMEWORK``:
    DRF reads the default once, when ``APIView`` is imported, so a settings override would be
    order-dependent (the note in ``tests/integration/admin/test_audit_view.py``).
    """
    with ExitStack() as stack:
        for view in LEDGER_VIEWS:
            stack.enter_context(
                mock.patch.object(view, "authentication_classes", [SeededPrincipalAuthentication])
            )
        yield Client()


@pytest.fixture
def approve() -> Any:
    """The :func:`approved_approval` helper as a fixture, so tests do not import a conftest."""
    return approved_approval


def approved_approval(
    *,
    action: str,
    payload: Any,
    maker: SeededUser,
    approver: SeededUser,
    customer_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert an approval the posting function will accept (already approved, not consumed).

    Used by the tests that exercise the *database* rule rather than the proposal flow, which
    has its own tests. The guard trigger computes the hash, so the payload is exactly what the
    function will compare. The timestamps are the wall clock's, not :data:`NOW`: the function
    refuses an approval past its ``expires_at``, and a fixed "now" in a fixture file goes stale.
    """
    approval_id = uuid.uuid4()
    moment = datetime.now(UTC)
    # The payload is a `Jsonb` and the rest are scalars of different types, so the parameter
    # list is `Any`: it mirrors the placeholder list the driver receives, nothing more.
    parameters: list[Any] = [
        approval_id,
        action,
        customer_id,
        maker.user_id,
        uuid.uuid4(),
        Jsonb(payload),
        "0" * 64,
        approver.user_id,
        uuid.uuid4(),
        moment,
        moment,
        moment + DAY,
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.approvals (id, action, subject_customer_id, maker_user_id,"
            " maker_session_id, payload, payload_hash, status, approver_user_id,"
            " approver_session_id, decided_at, created_at, expires_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 'approved', %s, %s, %s, %s, %s)",
            parameters,
        )
    return approval_id
