"""`CustomerDirectoryImpl`: the read surface the neighbours in the slice use (Task 02.9).

Three claims, each of which a regression would hide:

* **A quote floors to whole watt-hours** (D-08). The energy a customer is sold is the amount
  divided by the tariff, rounded *down*: the hub never grants a watt-hour it was not paid for,
  and a fractional remainder is dropped rather than carried.
* **The tariff in force is chosen by instant, not by "the newest row".** A version whose
  ``effective_from`` is in the future must not be quoted before its date even though it is the
  newest thing in `hub.plan_tariffs`.
* **The meter link reads from either direction** and answers ``None`` instead of raising when
  there is no meter or no owner — the callers (top-ups, tokens) ask a question whose honest
  answer is sometimes "none".

The directory is built through `customers.wiring`, so these tests exercise the registration
(the meter link installed by `meters`) and not a hand-assembled copy of the adapters.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection

from hub.customers import wiring as customers_wiring
from hub.customers.application.directory import CustomerDirectoryImpl
from hub.identity import wiring as identity_wiring
from hub.shared.clock import FrozenClock
from hub.shared.errors import NotFound, ValidationFailed
from support.principals import SeededUser, seed_staff_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
EARLIER = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 12, 1, 0, 0, tzinfo=UTC)
# Before the first version is in force: the instant a quote must refuse rather than guess.
BEFORE_ANY = datetime(2025, 12, 1, 0, 0, tzinfo=UTC)

PHONE_A = "+243810000901"
PHONE_B = "+243810000902"


class Registry:
    """A site, a plan with two tariff versions, two customers and two meters."""

    def __init__(self) -> None:
        self.site = uuid.uuid4()
        self.plan = uuid.uuid4()
        self.tariff_now = uuid.uuid4()
        self.tariff_later = uuid.uuid4()
        self.customer_a = uuid.uuid4()
        self.customer_b = uuid.uuid4()
        self.meter_a = uuid.uuid4()
        self.meter_free = uuid.uuid4()
        self.maker: SeededUser | None = None
        self.approver: SeededUser | None = None

    def seed(self) -> Registry:
        self.maker = seed_staff_user("finance_officer", attributes=("finance_lead",))
        self.approver = seed_staff_user("finance_officer", attributes=("finance_lead",))
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, 'USD')",
                [self.site, "DIR-A", "Directory site"],
            )
            cursor.execute(
                "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, "
                "max_topup_minor) VALUES (%s, %s, %s, 'USD', 100, 1000000)",
                [self.plan, "DIR-P", "Directory plan"],
            )
            # A tariff version may only be `active` with an approval behind it
            # (`ck_plan_tariffs_approved`), so each row gets a decided approval of its own.
            for tariff_id, price, effective in (
                (self.tariff_now, 1000, EARLIER),
                (self.tariff_later, 1250, LATER),
            ):
                approval_id = uuid.uuid4()
                cursor.execute(
                    "INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, "
                    "payload, status, approver_user_id, approver_session_id, decided_at, "
                    "created_at, expires_at) VALUES (%s, 'tariff.change', %s, %s, '{}'::jsonb, "
                    "'approved', %s, %s, %s, %s, %s)",
                    [
                        approval_id,
                        self.maker.user_id,
                        uuid.uuid4(),
                        self.approver.user_id,
                        uuid.uuid4(),
                        effective - timedelta(hours=1),
                        effective - timedelta(days=1),
                        effective + timedelta(days=2),
                    ],
                )
                cursor.execute(
                    "INSERT INTO hub.plan_tariffs (id, plan_id, tariff_minor_per_kwh, "
                    "effective_from, status, proposed_by, approval_id, created_at) "
                    "VALUES (%s, %s, %s, %s, 'active', %s, %s, %s)",
                    [
                        tariff_id,
                        self.plan,
                        price,
                        effective,
                        self.maker.user_id,
                        approval_id,
                        effective,
                    ],
                )
            for customer_id, phone, name in (
                (self.customer_a, PHONE_A, "Directory A"),
                (self.customer_b, PHONE_B, "Directory B"),
            ):
                cursor.execute(
                    "INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164, "
                    "language) VALUES (%s, %s, %s, %s, %s, 'fr')",
                    [customer_id, self.site, self.plan, name, phone],
                )
            # Assigning `customer_id` on insert lets the schema trigger write the history row.
            for meter_id, serial, owner in (
                (self.meter_a, "DIR-SN-1", self.customer_a),
                (self.meter_free, "DIR-SN-2", None),
            ):
                cursor.execute(
                    "INSERT INTO hub.meters (id, serial_no, site_id, customer_id, status) "
                    "VALUES (%s, %s, %s, %s, 'active')",
                    [meter_id, serial, self.site, owner],
                )
        return self


@pytest.fixture
def frozen() -> Any:
    """A frozen clock: `current_plan` reads it, so the tariff in force is reproducible."""
    clock = FrozenClock(NOW)
    identity_wiring.use_clock(clock)
    customers_wiring.reset()
    yield clock
    customers_wiring.reset()
    identity_wiring.use_clock(None)
    identity_wiring.reset()


@pytest.fixture
def registry() -> Registry:
    return Registry().seed()


@pytest.fixture
def directory(frozen: Any) -> CustomerDirectoryImpl:  # noqa: ARG001 - installs the clock
    return customers_wiring.customer_directory()


def test_quote_energy_floors_to_wh_and_uses_tariff_in_force(
    registry: Registry, directory: CustomerDirectoryImpl
) -> None:
    """1 minor at 1,000/kWh is 1 Wh, and a remainder is dropped rather than rounded up."""
    quote = directory.quote_energy(registry.customer_a, 1, at=NOW)

    assert quote.tariff_minor_per_kwh == 1000
    assert quote.plan_tariff_id == registry.tariff_now
    assert quote.energy_wh == 1, "1 minor buys 1/1000 kWh = exactly 1 Wh"
    assert isinstance(quote.energy_wh, int)

    bigger = directory.quote_energy(registry.customer_a, 2500, at=NOW)
    assert bigger.energy_wh == 2500

    # The newer version is in force only from its own date; before that the old one still is.
    assert directory.quote_energy(registry.customer_a, 2500, at=LATER).tariff_minor_per_kwh == 1250
    later = directory.quote_energy(registry.customer_a, 1001, at=LATER)
    assert later.energy_wh == 800, "floor(1001 * 1000 / 1250); the 0.8 Wh remainder is dropped"
    assert 800 * 1250 <= 1001 * 1000 < 801 * 1250, "the quote never rounds up"

    # Nothing in force yet is a refusal, not a zero: a top-up cannot be priced without a price.
    with pytest.raises(ValidationFailed):
        directory.quote_energy(registry.customer_a, 100, at=BEFORE_ANY)


def test_current_plan(registry: Registry, directory: CustomerDirectoryImpl) -> None:
    """The plan document carries the tariff version actually in force at the frozen instant."""
    plan = directory.current_plan(registry.customer_a)

    assert plan.id == registry.plan
    assert (plan.code, plan.currency, plan.status) == ("DIR-P", "USD", "active")
    assert (plan.min_topup_minor, plan.max_topup_minor) == (100, 1000000)
    assert plan.plan_tariff_id == registry.tariff_now, (
        "the version dated 2026-01-01, not 2026-12-01"
    )
    assert (plan.tariff_minor_per_kwh, plan.effective_from) == (1000, EARLIER)

    view = directory.get(registry.customer_a)
    assert (view.id, view.locale, view.status) == (registry.customer_a, "fr", "active")
    with pytest.raises(NotFound):
        directory.get(uuid.uuid4())


def test_meter_for_customer_and_customer_for_meter(
    registry: Registry, directory: CustomerDirectoryImpl
) -> None:
    """The link answers from either direction, and "none" is a value, not an error."""
    assert directory.meter_for_customer(registry.customer_a) == registry.meter_a
    assert directory.customer_for_meter(registry.meter_a) == registry.customer_a

    assert directory.customer_for_meter(registry.meter_free) is None
    assert directory.meter_for_customer(registry.customer_b) is None
    assert directory.meter_for_customer(uuid.uuid4()) is None
    assert directory.customer_for_meter(uuid.uuid4()) is None


def test_directory_binds_identity_customer_lookup(
    registry: Registry, directory: CustomerDirectoryImpl
) -> None:
    """The same object serves identity's sign-in lookup, so a dormant cut-off stays honest.

    `last_login_at` is identity's session fact; a customer with no session yet reads ``None``
    rather than a made-up instant, and an unknown or suspended customer is invisible.
    """
    by_phone = directory.find_active_by_phone(PHONE_A)
    assert by_phone is not None
    assert by_phone.id == registry.customer_a
    assert by_phone.locale == "fr"
    assert by_phone.last_login_at is None

    assert directory.by_id(registry.customer_b) is not None
    assert directory.find_active_by_phone("+243810009999") is None
    assert directory.by_id(uuid.uuid4()) is None

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE hub.customers SET status = 'suspended' WHERE id = %s", [registry.customer_a]
        )
    assert directory.by_id(registry.customer_a) is None, "a suspended customer cannot sign in"


def test_the_registry_demo_seed_is_readable_through_the_directory(
    frozen: Any,  # noqa: ARG001 - installs the clock `current_plan` reads
    directory: CustomerDirectoryImpl,
) -> None:
    """`load_fixtures` loads `db/seed/registry_demo.json`, and the directory can read it.

    The seed is the slice demo's data, so it has to be real: two sites, two plans each with an
    active tariff, six customers with six meters (one unassigned) and one conflict-of-interest
    link. Reading it back through the directory also proves the two halves meet.
    """
    call_command("load_fixtures", verbosity=0)

    demo = next(c for c in _demo_customers() if c["external_ref"] == "EXT-001")
    plan = directory.current_plan(demo["id"])
    assert (plan.code, plan.tariff_minor_per_kwh) == ("BASIC", 1200)
    assert plan.plan_tariff_id is not None, (
        "the seed's tariff must exist as a row, not just a number"
    )
    assert directory.meter_for_customer(demo["id"]) == demo["meter_id"]
    assert directory.customer_for_meter(demo["meter_id"]) == demo["id"]

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM hub.sites")
        assert cursor.fetchone()[0] == 2
        cursor.execute("SELECT count(*) FROM hub.plans")
        assert cursor.fetchone()[0] == 2
        cursor.execute("SELECT count(*) FROM hub.meters")
        assert cursor.fetchone()[0] == 6
        cursor.execute("SELECT count(*) FROM hub.meters WHERE customer_id IS NULL")
        assert cursor.fetchone()[0] == 1, "the demo keeps one meter unassigned"
        cursor.execute("SELECT count(*) FROM hub.staff_customer_link")
        assert cursor.fetchone()[0] == 1


def _demo_customers() -> list[dict[str, Any]]:
    """The seeded demo customers with the meter the seed gives each of them."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.id, c.external_ref, m.id FROM hub.customers c "
            "LEFT JOIN hub.meters m ON m.customer_id = c.id "
            "WHERE c.external_ref IS NOT NULL ORDER BY c.external_ref"
        )
        rows = cursor.fetchall()
    return [{"id": row[0], "external_ref": str(row[1]), "meter_id": row[2]} for row in rows]
