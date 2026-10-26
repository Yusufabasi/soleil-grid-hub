"""Customer CRUD with `If-Match` (Task 02.3, TC-FR-01-1, TC-FR-01-4).

The write path is where a registry loses data quietly, so these tests pin the three answers
the contract distinguishes — 428 for no header, 412 for the wrong version, 409 for a race the
repository loses — plus the phone rules that decide whether one person is one customer.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from django.db import connection, transaction
from django.test import Client

from hub.customers import wiring as customers_wiring
from hub.customers.domain.errors import VersionConflict
from hub.customers.infrastructure.repositories import CustomerRepository
from hub.identity import wiring as identity_wiring
from hub.identity.infrastructure.models import User, UserRole
from hub.shared.clock import FrozenClock

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

CUSTOMERS = "/api/v1/customers"
SITE_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0401")
PLAN_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0402")
NOW = datetime(2026, 10, 26, 11, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def registry() -> Any:
    """A frozen clock, a wired customers module, and the site and plan every write needs."""
    clock = FrozenClock(NOW)
    identity_wiring.use_clock(clock)
    customers_wiring.reset()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency, timezone, status) "
            "VALUES (%s, 'KIN', 'Kinshasa', 'USD', 'Africa/Kinshasa', 'active')",
            [SITE_ID],
        )
        cursor.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor, "
            "status) VALUES (%s, 'BASIC', 'Basic', 'USD', 100, 100000, 'active')",
            [PLAN_ID],
        )
    yield clock
    customers_wiring.reset()
    identity_wiring.use_clock(None)
    identity_wiring.reset()


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


def token_for(*roles: str, all_sites: bool = True) -> str:
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
    principal = identity_wiring.users().build_principal(
        user_id, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "full_name": "Amani Mbuyi",
        "phone_e164": "+243810000301",
        "site_id": str(SITE_ID),
        "plan_id": str(PLAN_ID),
    }
    payload.update(overrides)
    return payload


def create(client: Client, token: str, **overrides: Any) -> Any:
    return client.post(
        CUSTOMERS, data=body(**overrides), content_type="application/json", headers=auth(token)
    )


@pytest.mark.tc("TC-FR-01-1")
def test_create_read_back_with_status_and_etag(client: Client) -> None:
    token = token_for("support_agent")
    created = create(client, token)
    assert created.status_code == 201, created.json()
    document = created.json()["data"]
    assert document["status"] == "active"
    assert document["version"] == 1
    assert document["customer_number"]
    assert created["ETag"] == 'W/"v1"'
    assert created["Location"] == f"/api/v1/customers/{document['id']}"

    read = client.get(f"{CUSTOMERS}/{document['id']}", headers=auth(token))
    assert read.status_code == 200
    assert read.headers["ETag"] == 'W/"v1"'
    assert read.json()["data"]["full_name"] == "Amani Mbuyi"


@pytest.mark.tc("TC-FR-01-1")
def test_phone_normalisation_and_duplicate_409(client: Client) -> None:
    token = token_for("support_agent")
    first = create(client, token, phone_e164="+243 81 234 5678")
    assert first.status_code == 201, first.json()
    assert first.json()["data"]["phone_e164"] == "+243812345678"

    duplicate = create(client, token, phone_e164="+243812345678", full_name="Same Person")
    assert duplicate.status_code == 409, duplicate.json()
    assert duplicate.json()["code"] == "duplicate_resource"

    national = create(client, token, phone_e164="0812345679", full_name="National Form")
    assert national.status_code == 201, national.json()
    assert national.json()["data"]["phone_e164"] == "+243812345679"

    nonsense = create(client, token, phone_e164="abc")
    assert nonsense.status_code == 422, nonsense.json()


@pytest.mark.tc("TC-FR-01-4")
def test_stale_if_match_412_and_missing_428(client: Client) -> None:
    token = token_for("support_agent")
    customer_id = create(client, token).json()["data"]["id"]

    missing = client.patch(
        f"{CUSTOMERS}/{customer_id}",
        data={"full_name": "No Header"},
        content_type="application/json",
        headers=auth(token),
    )
    assert missing.status_code == 428, missing.json()
    assert missing.json()["code"] == "precondition_required"

    stale = client.patch(
        f"{CUSTOMERS}/{customer_id}",
        data={"full_name": "Stale"},
        content_type="application/json",
        headers={**auth(token), "If-Match": 'W/"v7"'},
    )
    assert stale.status_code == 412, stale.json()
    assert stale.json()["code"] == "precondition_failed"

    fresh = client.patch(
        f"{CUSTOMERS}/{customer_id}",
        data={"full_name": "Fresh"},
        content_type="application/json",
        headers={**auth(token), "If-Match": 'W/"v1"'},
    )
    assert fresh.status_code == 200, fresh.json()


@pytest.mark.tc("TC-FR-01-4")
def test_update_bumps_etag(client: Client) -> None:
    token = token_for("support_agent")
    customer_id = create(client, token).json()["data"]["id"]

    updated = client.patch(
        f"{CUSTOMERS}/{customer_id}",
        data={"full_name": "Renamed", "locale": "en"},
        content_type="application/json",
        headers={**auth(token), "If-Match": 'W/"v1"'},
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["data"]["version"] == 2
    assert updated.headers["ETag"] == 'W/"v2"'

    # The old validator is now stale: the client must re-read before writing again.
    again = client.patch(
        f"{CUSTOMERS}/{customer_id}",
        data={"full_name": "Again"},
        content_type="application/json",
        headers={**auth(token), "If-Match": 'W/"v1"'},
    )
    assert again.status_code == 412, again.json()


def test_repository_race_is_a_version_conflict() -> None:
    """The layer under ``If-Match``: two writers that both passed it cannot both win."""
    token = token_for("support_agent")
    assert token  # the wiring under test only needs a database row shape
    repository = CustomerRepository()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164, language, "
            "status) VALUES (%s, %s, %s, 'Amani', '+243810000302', 'fr', 'active')",
            [uuid.uuid4(), SITE_ID, PLAN_ID],
        )
    row = repository.find_by_phone("+243810000302")
    assert row is not None

    repository.update_with_version(replace(row, full_name="First"), expected_version=row.version)
    with pytest.raises(VersionConflict), transaction.atomic():
        repository.update_with_version(
            replace(row, full_name="Second"), expected_version=row.version
        )


@pytest.mark.tc("TC-FR-01-1")
def test_close_customer_requires_zero_balance(client: Client) -> None:
    token = token_for("support_agent")
    customer_id = create(client, token).json()["data"]["id"]

    class Owing:
        def balance_minor(self, customer_id: UUID) -> int:  # noqa: ARG002 - the port's shape
            return 5

    customers_wiring.use_balance_probe(Owing())
    refused = client.delete(f"{CUSTOMERS}/{customer_id}", headers=auth(token))
    assert refused.status_code == 409, refused.json()
    assert refused.json()["code"] == "customer_has_balance"

    customers_wiring.use_balance_probe(customers_wiring._NoWallet())  # noqa: SLF001
    closed = client.delete(f"{CUSTOMERS}/{customer_id}", headers=auth(token))
    assert closed.status_code == 204
    status = client.get(f"{CUSTOMERS}/{customer_id}", headers=auth(token)).json()["data"]["status"]
    assert status == "closed"


def test_update_status_only_active_or_suspended(client: Client) -> None:
    token = token_for("support_agent")
    customer_id = create(client, token).json()["data"]["id"]

    suspended = client.patch(
        f"{CUSTOMERS}/{customer_id}",
        data={"status": "suspended"},
        content_type="application/json",
        headers={**auth(token), "If-Match": 'W/"v1"'},
    )
    assert suspended.status_code == 200, suspended.json()
    assert suspended.json()["data"]["status"] == "suspended"

    illegal = client.patch(
        f"{CUSTOMERS}/{customer_id}",
        data={"status": "closed"},
        content_type="application/json",
        headers={**auth(token), "If-Match": 'W/"v2"'},
    )
    # `closed` is not in `CustomerUpdate`'s enum: the contract refuses it before the use case.
    assert illegal.status_code == 422, illegal.json()


def test_locale_defaults_to_fr_and_accepts_en(client: Client) -> None:
    token = token_for("support_agent")
    default = create(client, token).json()["data"]
    assert default["locale"] == "fr"

    english = create(client, token, phone_e164="+243810000303", locale="en").json()["data"]
    assert english["locale"] == "en"
