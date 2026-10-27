"""`/sites` and `/plans` through the real permission class (Task 02.2, TC-FR-01-1).

Every call carries a real EdDSA token for a real ``hub.users`` row, so the verdict under test
is the one ``api/permissions.yaml`` gives that role — not a stub. Two things are easy to get
wrong and are pinned here: a role the contract does not list is refused (support creating a
site, administrator reading one), and a support agent holding one site never sees another.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client

from hub.customers import wiring as customers_wiring
from hub.identity import wiring as identity_wiring
from hub.shared.clock import FrozenClock

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

SITES = "/api/v1/sites"
PLANS = "/api/v1/plans"
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)

SITE_A = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0301")
SITE_B = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0302")


@pytest.fixture(autouse=True)
def frozen(redis_store) -> Any:  # noqa: ANN001 - the fixture's job is the store
    """A frozen clock, installed after the session store is ready.

    ``redis_store`` resets `identity`'s wiring, which drops whatever clock was installed
    before it. Depending on it here makes that reset happen first, so the clock this suite
    froze is the one the use cases read; the other order stamps approvals with the wall
    clock while the assertions move a frozen one.
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


def seed_user(*roles: str, all_sites: bool = False, sites: tuple[UUID, ...] = ()) -> Any:
    """Insert an active staff user, its roles and its sites; return a token for it."""
    from hub.identity.infrastructure.models import User, UserRole  # noqa: PLC0415

    user_id = uuid.uuid4()
    User.objects.create(
        id=user_id,
        email=f"{user_id}@example.test",
        display_name="Test Caller",
        status="active",
        all_sites=all_sites,
        token_version=1,
    )
    for role in roles:
        UserRole.objects.create(user_id=user_id, role_code=role)
    for site_id in sites:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.user_sites (user_id, site_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                [user_id, site_id],
            )
    record = identity_wiring.users().get(user_id)
    assert record is not None
    principal = identity_wiring.users().build_principal(
        user_id,
        session_id=uuid.uuid4(),
        auth_time=NOW,
        acr="mfa",
    )
    token = identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )
    return token


def seed_site(site_id: UUID, code: str, name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency, timezone, status) "
            "VALUES (%s, %s, %s, 'USD', 'Africa/Kinshasa', 'active')",
            [site_id, code, name],
        )


def get(client: Client, path: str, token: str) -> Any:
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


def post(client: Client, path: str, token: str, body: dict[str, Any]) -> Any:
    return client.post(
        path,
        data=body,
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )


def patch(client: Client, path: str, token: str, body: dict[str, Any]) -> Any:
    return client.patch(
        path,
        data=body,
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.tc("TC-FR-01-1")
def test_operations_creates_site_and_reads_back(client: Client) -> None:
    token = seed_user("operations_engineer", all_sites=True)

    created = post(
        client,
        SITES,
        token,
        {"code": "LUB", "name": "Lubumbashi", "timezone": "Africa/Lubumbashi"},
    )
    assert created.status_code == 201, created.json()
    document = created.json()["data"]
    assert document["code"] == "LUB"
    assert document["health"] == "operational"
    assert document["meter_count"] == 0
    assert created["Location"] == f"/api/v1/sites/{document['id']}"

    read = get(client, f"{SITES}/{document['id']}", token)
    assert read.status_code == 200
    assert read.json()["data"]["name"] == "Lubumbashi"


@pytest.mark.tc("TC-FR-15-3")
def test_support_cannot_create_site_and_administrator_cannot_read_them(client: Client) -> None:
    seed_site(SITE_A, "KIN", "Kinshasa")

    support = seed_user("support_agent", all_sites=True)
    administrator = seed_user("administrator", all_sites=True)

    refused = post(
        client, SITES, support, {"code": "NEW", "name": "New", "timezone": "Africa/Kinshasa"}
    )
    assert refused.status_code == 403, refused.json()

    assert get(client, SITES, administrator).status_code == 403
    assert get(client, f"{SITES}/{SITE_A}", administrator).status_code == 403


def test_site_code_unique_409_duplicate_resource(client: Client) -> None:
    seed_site(SITE_A, "KIN", "Kinshasa")
    token = seed_user("operations_engineer", all_sites=True)

    clash = post(
        client, SITES, token, {"code": "KIN", "name": "Another", "timezone": "Africa/Kinshasa"}
    )
    assert clash.status_code == 409, clash.json()
    assert clash.json()["code"] == "duplicate_resource"


@pytest.mark.tc("TC-FR-01-1")
def test_site_list_limited_to_user_sites(client: Client) -> None:
    seed_site(SITE_A, "KIN", "Kinshasa")
    seed_site(SITE_B, "LUB", "Lubumbashi")
    bound = seed_user("support_agent", all_sites=False, sites=(SITE_A,))
    wide = seed_user("support_agent", all_sites=True)

    limited = get(client, SITES, bound)
    assert limited.status_code == 200
    assert [row["code"] for row in limited.json()["data"]] == ["KIN"]
    assert get(client, f"{SITES}/{SITE_B}", bound).status_code == 404
    assert len(get(client, SITES, wide).json()["data"]) == 2


def test_list_sites_cursor_pagination(client: Client) -> None:
    for index, code in enumerate(("AAA", "BBB", "CCC")):
        seed_site(UUID(int=index + 1), code, code)
    token = seed_user("support_agent", all_sites=True)

    first = get(client, f"{SITES}?limit=2", token).json()
    assert len(first["data"]) == 2
    assert first["meta"]["has_next"] is True

    second = get(client, f"{SITES}?limit=2&cursor={first['meta']['next_cursor']}", token).json()
    assert len(second["data"]) == 1
    assert second["meta"]["has_next"] is False
    assert {row["id"] for row in first["data"]} & {row["id"] for row in second["data"]} == set()


@pytest.mark.tc("TC-FR-01-1")
def test_finance_creates_plan_with_money_limits(client: Client) -> None:
    token = seed_user("finance_officer", all_sites=True)
    body: dict[str, Any] = {
        "code": "BASIC",
        "name": "Basic",
        "currency": "USD",
        "min_top_up": {"amount_minor": 100, "currency": "USD"},
        "max_top_up": {"amount_minor": 100_000, "currency": "USD"},
    }

    created = post(client, PLANS, token, body)
    assert created.status_code == 201, created.json()
    document = created.json()["data"]
    assert document["min_top_up"] == {"amount_minor": 100, "currency": "USD"}
    assert document["max_top_up"] == {"amount_minor": 100_000, "currency": "USD"}
    assert document["status"] == "draft"

    inverted = post(
        client,
        PLANS,
        token,
        {
            **body,
            "code": "BAD",
            "min_top_up": {"amount_minor": 500, "currency": "USD"},
            "max_top_up": {"amount_minor": 100, "currency": "USD"},
        },
    )
    assert inverted.status_code == 422, inverted.json()


def test_plan_currency_mismatch_422(client: Client) -> None:
    token = seed_user("finance_officer", all_sites=True)
    response = post(
        client,
        PLANS,
        token,
        {
            "code": "CDF1",
            "name": "CDF plan",
            "currency": "CDF",
            "min_top_up": {"amount_minor": 100, "currency": "USD"},
            "max_top_up": {"amount_minor": 1000, "currency": "CDF"},
        },
    )
    assert response.status_code == 422, response.json()


def test_limits_of_an_active_plan_are_locked(client: Client) -> None:
    token = seed_user("finance_officer", all_sites=True)
    created = post(
        client,
        PLANS,
        token,
        {
            "code": "GROW",
            "name": "Growth",
            "currency": "USD",
            "min_top_up": {"amount_minor": 100, "currency": "USD"},
            "max_top_up": {"amount_minor": 5_000, "currency": "USD"},
        },
    ).json()["data"]

    activated = patch(client, f"{PLANS}/{created['id']}", token, {"status": "active"})
    assert activated.status_code == 200, activated.json()
    assert activated.json()["data"]["status"] == "active"

    locked = patch(
        client,
        f"{PLANS}/{created['id']}",
        token,
        {"max_top_up": {"amount_minor": 9_000, "currency": "USD"}},
    )
    assert locked.status_code == 422, locked.json()
    assert locked.json()["code"] == "plan_locked"

    # A retired plan is not revived: the lifecycle is one-way.
    assert (
        patch(client, f"{PLANS}/{created['id']}", token, {"status": "retired"}).status_code == 200
    )
    revived = patch(client, f"{PLANS}/{created['id']}", token, {"status": "active"})
    assert revived.status_code == 409, revived.json()
