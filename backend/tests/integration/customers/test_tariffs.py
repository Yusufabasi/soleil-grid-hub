"""Plan tariffs: a price version needs a date, a second pair of hands and a cap (Task 02.4, D-08).

Three rules are pinned here and each of them is easy to get wrong in a way a client would not
notice for weeks:

* **A proposal is not a price.** The row is written when the *second* finance lead approves, so
  ``GET /plans/{id}`` keeps answering the old ``current_tariff`` until then — and keeps answering
  the old one after the approval too, until ``effective_from`` actually arrives.
* **The cap belongs to the database.** The 25 percent guard is a trigger; the use case mirrors it
  so the maker hears about it at proposal time, and the executor maps the trigger's own
  ``check_violation`` to the same 422 for the race it cannot see.
* **The approver is not the maker and holds ``finance_lead``.** An administrator passes the
  permission class (they may call ``approveApproval``) and is still refused by the executor,
  which is the difference between "allowed to decide something" and "allowed to decide this".

History predating a test is seeded straight into ``hub.approvals`` and ``hub.plan_tariffs``,
because a tariff dated in the past cannot be proposed any more — the date rule that refuses it
is the subject of another test below.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client

from hub.customers import wiring as customers_wiring
from hub.identity import wiring as identity_wiring
from hub.shared.clock import FrozenClock

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

PLANS = "/api/v1/plans"
APPROVALS = "/api/v1/approvals"
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
NOVEMBER = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
DECEMBER = datetime(2026, 12, 1, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen(redis_store) -> Any:  # noqa: ANN001 - the fixture's job is the store
    """A frozen clock, installed after the session store is ready.

    ``redis_store`` resets `identity`'s wiring, which drops whatever clock was installed
    before it. Depending on it here makes that reset happen first, so the clock this suite
    froze is the one the use cases read; the other order stamps approvals with the wall
    clock while the assertions move a frozen one.

    ``customers_wiring.reset`` also re-registers the ``tariff.change`` executor: the identity
    registry is rebuilt from scratch on reset, and an approval kind nobody can execute would
    turn the approve call into a 403 that looks like a permission bug.
    """
    clock = FrozenClock(NOW)
    identity_wiring.use_clock(clock)
    customers_wiring.reset()
    yield clock
    customers_wiring.reset()
    identity_wiring.use_clock(None)
    identity_wiring.reset()


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


def move_clock_to(instant: datetime) -> None:
    """Move the frozen clock and rebuild both wirings around it (fresh tokens after)."""
    identity_wiring.use_clock(FrozenClock(instant))
    customers_wiring.reset()


def seed_user(*roles: str, finance_lead: bool = False, attributes: tuple[str, ...] = ()) -> UUID:
    """Insert an active staff user holding ``roles``; return its id."""
    from hub.identity.infrastructure.models import User, UserRole  # noqa: PLC0415

    user_id = uuid.uuid4()
    User.objects.create(
        id=user_id,
        email=f"{user_id}@example.test",
        display_name="Test Caller",
        status="active",
        all_sites=True,
        finance_lead=finance_lead or "finance_lead" in attributes,
        token_version=1,
    )
    for role in roles:
        UserRole.objects.create(user_id=user_id, role_code=role)
    return user_id


def token_for(user_id: UUID, *, auth_time: datetime = NOW) -> str:
    """A real access token for a seeded account, as old as the caller asks it to be."""
    principal = identity_wiring.users().build_principal(
        user_id, session_id=uuid.uuid4(), auth_time=auth_time, acr="mfa"
    )
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=auth_time, acr="mfa"
    )


def create_plan(client: Client, token: str, *, code: str = "BASIC", currency: str = "USD") -> Any:
    response = client.post(
        PLANS,
        data={
            "code": code,
            "name": f"{code.title()} plan",
            "currency": currency,
            "min_top_up": {"amount_minor": 100, "currency": currency},
            "max_top_up": {"amount_minor": 100_000, "currency": currency},
        },
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.json()
    return response.json()["data"]


def seed_history(
    plan_id: UUID,
    *,
    price: int,
    effective_from: datetime,
    maker_id: UUID,
    approver_id: UUID,
) -> UUID:
    """A tariff that predates the test: an executed approval and the active row it produced."""
    tariff_id = uuid.uuid4()
    approval_id = uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, payload, "
            "payload_hash, status, approver_user_id, approver_session_id, decided_at, "
            "consumed_at, created_at, expires_at) "
            "VALUES (%s, 'tariff.change', %s, %s, %s::jsonb, %s, 'consumed', %s, %s, %s, %s, "
            "%s, %s)",
            [
                approval_id,
                maker_id,
                uuid.uuid4(),
                f'{{"plan_id": "{plan_id}", "tariff_minor_per_kwh": {price}}}',
                "0" * 64,
                approver_id,
                uuid.uuid4(),
                effective_from - timedelta(days=1),
                effective_from,
                effective_from - timedelta(days=2),
                effective_from + timedelta(days=3),
            ],
        )
        cursor.execute(
            "INSERT INTO hub.plan_tariffs (id, plan_id, tariff_minor_per_kwh, effective_from, "
            "status, proposed_by, approval_id, created_at) "
            "VALUES (%s, %s, %s, %s, 'active', %s, %s, %s)",
            [tariff_id, plan_id, price, effective_from, maker_id, approval_id, effective_from],
        )
    return tariff_id


def propose(
    client: Client,
    token: str,
    plan_id: str,
    *,
    price: int = 1_100,
    effective_from: datetime = NOVEMBER,
    currency: str = "USD",
    reason: str = "Seasonal cost of imported meters",
    **extra: Any,
) -> Any:
    body: dict[str, Any] = {
        "price_per_kwh": {"amount_minor": price, "currency": currency},
        "effective_from": effective_from.isoformat(),
        "reason": reason,
        **extra,
    }
    return client.post(
        f"{PLANS}/{plan_id}/tariffs",
        data=body,
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )


def approve(client: Client, token: str, approval_id: str) -> Any:
    return client.post(
        f"{APPROVALS}/{approval_id}/approve",
        data={},
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )


def get_plan(client: Client, token: str, plan_id: str) -> Any:
    return client.get(f"{PLANS}/{plan_id}", headers={"Authorization": f"Bearer {token}"})


def tariff_rows(plan_id: UUID) -> list[tuple[str, int, str]]:
    """Every version of a plan's price, newest first: ``(status, price, id)``."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, tariff_minor_per_kwh, id::text FROM hub.plan_tariffs "
            " WHERE plan_id = %s ORDER BY effective_from DESC",
            [plan_id],
        )
        return [(str(row[0]), int(row[1]), str(row[2])) for row in cursor.fetchall()]


def current_tariff_at(plan_id: UUID, at: datetime) -> UUID | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT plan_tariff_id FROM hub.current_tariff(%s, %s)", [plan_id, at])
        row = cursor.fetchone()
    return row[0] if row is not None else None


@pytest.mark.tc("TC-FR-01-1")
def test_propose_returns_202_and_no_current_tariff_change(client: Client) -> None:
    maker, approver = (
        seed_user("finance_officer", finance_lead=True),
        seed_user("finance_officer", finance_lead=True),
    )
    plan = create_plan(client, token_for(maker))
    history = seed_history(
        UUID(plan["id"]),
        price=1_000,
        effective_from=OCTOBER,
        maker_id=maker,
        approver_id=approver,
    )

    proposed = propose(client, token_for(maker), plan["id"], price=1_100, effective_from=NOVEMBER)

    assert proposed.status_code == 202, proposed.json()
    approval = proposed.json()["data"]
    assert approval["kind"] == "tariff_change"
    assert approval["status"] == "pending"
    assert approval["maker_id"] == str(maker)
    assert approval["payload"]["tariff_minor_per_kwh"] == 1_100
    assert approval["payload"]["plan_id"] == plan["id"]
    assert approval["payload"]["reason"] == "Seasonal cost of imported meters"
    assert len(approval["payload_hash"]) == 64
    assert proposed["Location"] == f"/api/v1/approvals/{approval['id']}"

    # A proposal is not a price: the plan still quotes the old one.
    still_old = get_plan(client, token_for(maker), plan["id"])
    assert still_old.json()["data"]["current_tariff"]["id"] == str(history)
    assert [row[0] for row in tariff_rows(UUID(plan["id"]))] == ["active"]


def test_second_finance_lead_approval_creates_active_tariff(client: Client) -> None:
    maker, approver = (
        seed_user("finance_officer", finance_lead=True),
        seed_user("finance_officer", finance_lead=True),
    )
    plan = create_plan(client, token_for(maker))
    seed_history(
        UUID(plan["id"]),
        price=1_000,
        effective_from=OCTOBER,
        maker_id=maker,
        approver_id=approver,
    )
    approval_id = propose(client, token_for(maker), plan["id"], price=1_100).json()["data"]["id"]

    decided = approve(client, token_for(approver), approval_id)

    assert decided.status_code == 200, decided.json()
    assert decided.json()["data"]["status"] == "executed"

    rows = tariff_rows(UUID(plan["id"]))
    assert [row[:2] for row in rows] == [("active", 1_100), ("superseded", 1_000)]
    new_id = UUID(rows[0][2])

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT proposed_by, approval_id FROM hub.plan_tariffs WHERE id = %s", [new_id]
        )
        assert cursor.fetchone() == (maker, UUID(approval_id))

    # The new price becomes the plan's tariff on its own date, not on the day it was approved.
    assert current_tariff_at(UUID(plan["id"]), NOVEMBER + timedelta(minutes=1)) == new_id
    assert current_tariff_at(UUID(plan["id"]), NOW) == UUID(rows[1][2])


def test_tariff_not_current_before_effective_from(client: Client) -> None:
    maker, approver = (
        seed_user("finance_officer", finance_lead=True),
        seed_user("finance_officer", finance_lead=True),
    )
    plan = create_plan(client, token_for(maker))
    history = seed_history(
        UUID(plan["id"]),
        price=1_000,
        effective_from=OCTOBER,
        maker_id=maker,
        approver_id=approver,
    )
    approval_id = propose(client, token_for(maker), plan["id"], price=1_100).json()["data"]["id"]
    assert approve(client, token_for(approver), approval_id).status_code == 200

    before = get_plan(client, token_for(maker), plan["id"]).json()["data"]["current_tariff"]
    assert before["id"] == str(history)
    assert current_tariff_at(UUID(plan["id"]), NOVEMBER - timedelta(seconds=1)) == history

    activated = tariff_rows(UUID(plan["id"]))[0]
    assert activated[:2] == ("active", 1_100)
    assert current_tariff_at(UUID(plan["id"]), NOVEMBER + timedelta(seconds=1)) == UUID(
        activated[2]
    )


def test_tariff_effective_from_in_past_422(client: Client) -> None:
    maker = seed_user("finance_officer", finance_lead=True)
    plan = create_plan(client, token_for(maker))

    refused = propose(client, token_for(maker), plan["id"], effective_from=OCTOBER)

    assert refused.status_code == 422, refused.json()
    assert refused.json()["code"] == "invalid_tariff"
    assert refused.json()["errors"][0]["field"] == "/effective_from"
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM hub.approvals WHERE action = 'tariff.change'")
        assert cursor.fetchone() == (0,)


def test_change_over_25_percent_422_tariff_change_too_large(client: Client) -> None:
    maker, approver = (
        seed_user("finance_officer", finance_lead=True),
        seed_user("finance_officer", finance_lead=True),
    )
    plan = create_plan(client, token_for(maker))
    seed_history(
        UUID(plan["id"]),
        price=1_000,
        effective_from=OCTOBER,
        maker_id=maker,
        approver_id=approver,
    )

    refused = propose(client, token_for(maker), plan["id"], price=1_300)
    assert refused.status_code == 422, refused.json()
    assert refused.json()["code"] == "tariff_change_too_large"

    # The cap is "more than 25 percent": exactly a quarter is allowed.
    allowed = propose(client, token_for(maker), plan["id"], price=1_250)
    assert allowed.status_code == 202, allowed.json()


def test_proposal_price_must_be_in_the_plan_currency_422(client: Client) -> None:
    maker = seed_user("finance_officer", finance_lead=True)
    plan = create_plan(client, token_for(maker), currency="USD")

    refused = propose(client, token_for(maker), plan["id"], price=1_100, currency="CDF")

    assert refused.status_code == 422, refused.json()
    assert refused.json()["code"] == "invalid_tariff"
    assert refused.json()["errors"][0]["field"] == "/price_per_kwh/currency"


def test_proposal_requires_fresh_step_up(client: Client) -> None:
    maker = seed_user("finance_officer", finance_lead=True)
    plan = create_plan(client, token_for(maker))

    stale = token_for(maker, auth_time=NOW - timedelta(minutes=10))
    refused = propose(client, stale, plan["id"])

    assert refused.status_code == 401, refused.json()
    assert refused.json()["code"] == "step_up_required"


def test_approver_without_finance_lead_403(client: Client) -> None:
    maker = seed_user("finance_officer", finance_lead=True)
    colleague = seed_user("finance_officer")
    plan = create_plan(client, token_for(maker))
    approval_id = propose(client, token_for(maker), plan["id"]).json()["data"]["id"]

    refused = approve(client, token_for(colleague), approval_id)

    assert refused.status_code == 403, refused.json()
    assert refused.json()["code"] == "forbidden"
    assert tariff_rows(UUID(plan["id"])) == []


def test_administrator_cannot_approve_tariff(client: Client) -> None:
    maker = seed_user("finance_officer", finance_lead=True)
    administrator = seed_user("administrator", attributes=("dp_admin",))
    plan = create_plan(client, token_for(maker))

    assert propose(client, token_for(administrator), plan["id"]).status_code == 403

    approval_id = propose(client, token_for(maker), plan["id"]).json()["data"]["id"]
    refused = approve(client, token_for(administrator), approval_id)

    assert refused.status_code == 403, refused.json()
    assert refused.json()["code"] == "forbidden"


def test_maker_cannot_approve_own_tariff(client: Client) -> None:
    maker = seed_user("finance_officer", finance_lead=True)
    plan = create_plan(client, token_for(maker))
    approval_id = propose(client, token_for(maker), plan["id"]).json()["data"]["id"]

    refused = approve(client, token_for(maker), approval_id)

    assert refused.status_code == 403, refused.json()
    assert refused.json()["code"] == "self_approval_forbidden"


def test_previous_tariff_becomes_superseded_at_activation(client: Client) -> None:
    maker, approver = (
        seed_user("finance_officer", finance_lead=True),
        seed_user("finance_officer", finance_lead=True),
    )
    plan = create_plan(client, token_for(maker))
    first = seed_history(
        UUID(plan["id"]),
        price=1_000,
        effective_from=OCTOBER,
        maker_id=maker,
        approver_id=approver,
    )
    approval_id = propose(
        client, token_for(maker), plan["id"], price=1_100, effective_from=NOVEMBER
    ).json()["data"]["id"]

    assert approve(client, token_for(approver), approval_id).status_code == 200

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM hub.plan_tariffs WHERE id = %s ORDER BY effective_from", [first]
        )
        assert cursor.fetchone() == ("superseded",)
    assert [row[0] for row in tariff_rows(UUID(plan["id"]))] == ["active", "superseded"]


def test_list_tariffs_history_ordered_desc(client: Client) -> None:
    maker, approver = (
        seed_user("finance_officer", finance_lead=True),
        seed_user("finance_officer", finance_lead=True),
    )
    plan = create_plan(client, token_for(maker))
    older = seed_history(
        UUID(plan["id"]),
        price=1_000,
        effective_from=OCTOBER,
        maker_id=maker,
        approver_id=approver,
    )
    approval_id = propose(
        client, token_for(maker), plan["id"], price=1_100, effective_from=NOVEMBER
    ).json()["data"]["id"]
    assert approve(client, token_for(approver), approval_id).status_code == 200

    listed = client.get(
        f"{PLANS}/{plan['id']}/tariffs?limit=1",
        headers={"Authorization": f"Bearer {token_for(maker)}"},
    )
    assert listed.status_code == 200, listed.json()
    body = listed.json()
    assert len(body["data"]) == 1
    newest = body["data"][0]
    assert newest["price_per_kwh"] == {"amount_minor": 1_100, "currency": "USD"}
    assert newest["status"] == "active"
    assert newest["approval_id"] == approval_id
    assert datetime.fromisoformat(newest["effective_from"].replace("Z", "+00:00")) == NOVEMBER
    assert body["meta"]["has_next"] is True

    second = client.get(
        f"{PLANS}/{plan['id']}/tariffs?limit=1&cursor={body['meta']['next_cursor']}",
        headers={"Authorization": f"Bearer {token_for(maker)}"},
    ).json()
    assert [row["id"] for row in second["data"]] == [str(older)]
    assert second["meta"]["has_next"] is False


def test_two_pending_proposals_same_effective_from_second_fails_409(client: Client) -> None:
    maker, other = (
        seed_user("finance_officer", finance_lead=True),
        seed_user("finance_officer", finance_lead=True),
    )
    plan = create_plan(client, token_for(maker))

    first = propose(client, token_for(maker), plan["id"], price=1_100, effective_from=NOVEMBER)
    assert first.status_code == 202, first.json()

    second = propose(client, token_for(other), plan["id"], price=1_150, effective_from=NOVEMBER)
    assert second.status_code == 409, second.json()
    assert second.json()["code"] == "duplicate_resource"

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM hub.approvals WHERE action = 'tariff.change' AND status = 'pending'"
        )
        assert cursor.fetchone() == (1,)


def test_expired_proposal_does_not_hold_the_date(client: Client) -> None:
    maker = seed_user("finance_officer", finance_lead=True)
    plan = create_plan(client, token_for(maker))
    assert propose(client, token_for(maker), plan["id"], effective_from=NOVEMBER).status_code == 202

    # Nobody decided it and the 72-hour window is gone: the date is free again, because an
    # approval that can no longer be decided must not block the proposal that replaces it.
    later = NOW + timedelta(days=4)
    move_clock_to(later)
    again = propose(client, token_for(maker, auth_time=later), plan["id"], effective_from=NOVEMBER)

    assert again.status_code == 202, again.json()


def test_tariff_whose_date_passed_before_approval_is_refused_422(client: Client) -> None:
    maker, approver = (
        seed_user("finance_officer", finance_lead=True),
        seed_user("finance_officer", finance_lead=True),
    )
    plan = create_plan(client, token_for(maker))
    starts_at = NOW + timedelta(hours=1)
    approval_id = propose(
        client, token_for(maker), plan["id"], price=1_100, effective_from=starts_at
    ).json()["data"]["id"]

    # The approver decides the next morning: the approval is still pending (72-hour window),
    # but its date has gone by, and inserting it now would put a price in force retroactively.
    later = starts_at + timedelta(days=1)
    move_clock_to(later)
    refused = approve(client, token_for(approver, auth_time=later), approval_id)

    assert refused.status_code == 422, refused.json()
    assert refused.json()["code"] == "invalid_tariff"
    assert tariff_rows(UUID(plan["id"])) == []
    with connection.cursor() as cursor:
        cursor.execute("SELECT status FROM hub.approvals WHERE id = %s", [approval_id])
        assert cursor.fetchone() == ("pending",)
