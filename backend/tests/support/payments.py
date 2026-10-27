"""Helpers for the payments suite: a seeded customer, tokens, and row counts.

They live in ``tests/support`` because two integration modules and the conftest need them, and
a relative import of a conftest module is not importable (the test tree has no packages). The
seeding is plain SQL on purpose: these rows belong to slices 01 and 02, and building them
through four other modules' HTTP routes would make every payment test depend on their
behaviour as well as their data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import connection

from hub.identity import wiring as identity_wiring
from hub.identity.contracts import Principal
from hub.shared.clock import FrozenClock

__all__ = [
    "NOW",
    "PAYER",
    "TARIFF_MINOR_PER_KWH",
    "World",
    "customer_token",
    "make_world",
    "staff_token",
    "top_up_count",
]

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
DAY = timedelta(days=1)
TARIFF_MINOR_PER_KWH = 25_000
PAYER = "+243810000111"


@dataclass
class World:
    """One customer, one active meter, and the tariff their plan is on."""

    site_id: UUID
    plan_id: UUID
    tariff_id: UUID
    customer_id: UUID
    meter_id: UUID
    clock: FrozenClock
    extra: dict[str, Any] = field(default_factory=dict)


def _seed_registry(*, meter_status: str = "active", currency: str = "USD") -> tuple[Any, ...]:
    """Site, plan, approved tariff, customer and an assigned meter."""
    site_id, plan_id, tariff_id, customer_id, meter_id = (uuid.uuid4() for _ in range(5))
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, %s)",
            [site_id, f"PAY-{site_id.hex[:8]}", "Payments site", currency],
        )
        cursor.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor)"
            " VALUES (%s, %s, %s, %s, 100, 1000000)",
            [plan_id, f"PAY-{plan_id.hex[:8]}", "Payments plan", currency],
        )
        # `ck_plan_tariffs_approved`: an active tariff needs the approval that published it.
        approval_id = uuid.uuid4()
        maker, checker = uuid.uuid4(), uuid.uuid4()
        for user_id in (maker, checker):
            cursor.execute(
                "INSERT INTO hub.users (id, email, display_name, status, token_version)"
                " VALUES (%s, %s, %s, 'active', 1)",
                [user_id, f"{user_id}@example.test", "Finance lead"],
            )
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
            " status, approval_id) VALUES (%s, %s, %s, %s, 'active', %s)",
            [tariff_id, plan_id, TARIFF_MINOR_PER_KWH, NOW - 30 * DAY, approval_id],
        )
        cursor.execute(
            "INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164, language,"
            " status, created_at) VALUES (%s, %s, %s, %s, %s, 'fr', 'active', %s)",
            [
                customer_id,
                site_id,
                plan_id,
                "Amani Mbuyi",
                # `uq_customers_phone` is global, so a second seeded world needs its own
                # number rather than a constant a test forgot to vary.
                f"+243{customer_id.int % 10**9:09d}",
                NOW - 30 * DAY,
            ],
        )
        # The assignment row is the trigger's job: `top_ups` has a composite foreign key to
        # `hub.meter_assignments`, and writing it here too would collide with the trigger.
        cursor.execute(
            "INSERT INTO hub.meters (id, serial_no, site_id, customer_id, status)"
            " VALUES (%s, %s, %s, %s, %s)",
            [meter_id, f"PAY-{meter_id.hex[:10]}", site_id, customer_id, meter_status],
        )
    return site_id, plan_id, tariff_id, customer_id, meter_id


def make_world(*, meter_status: str = "active", clock: FrozenClock | None = None) -> World:
    """Seed the registry and wrap it with the clock the test uses."""
    site_id, plan_id, tariff_id, customer_id, meter_id = _seed_registry(meter_status=meter_status)
    return World(
        site_id=site_id,
        plan_id=plan_id,
        tariff_id=tariff_id,
        customer_id=customer_id,
        meter_id=meter_id,
        clock=clock or FrozenClock(NOW),
    )


def staff_token(
    roles: tuple[str, ...] = ("finance_officer",),
    *,
    all_sites: bool = True,
    sites: tuple[UUID, ...] = (),
) -> str:
    """A real EdDSA token for a seeded staff account."""
    user_id = uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.users (id, email, display_name, status, all_sites, token_version)"
            " VALUES (%s, %s, 'Test Staff', 'active', %s, 1)",
            [user_id, f"{user_id}@example.test", all_sites],
        )
        for role in roles:
            cursor.execute(
                "INSERT INTO hub.user_roles (user_id, role_code) VALUES (%s, %s)",
                [user_id, role],
            )
        for site_id in sites:
            cursor.execute(
                "INSERT INTO hub.user_sites (user_id, site_id) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                [user_id, site_id],
            )
    principal = identity_wiring.users().build_principal(
        user_id, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )


def customer_token(customer_id: UUID) -> str:
    """A portal token: the customer is the subject and the claim names the same row."""
    principal = Principal(subject_id=customer_id, kind="customer", customer_id=customer_id)
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=NOW, acr="otp", amr=("otp",)
    )


def top_up_count(*, customer_id: UUID | None = None) -> int:
    """How many top-up rows exist, for the assertions that nothing was written."""
    query = "SELECT count(*) FROM hub.top_ups"
    params: list[Any] = []
    if customer_id is not None:
        query += " WHERE customer_id = %s"
        params.append(customer_id)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return int(cursor.fetchone()[0])
