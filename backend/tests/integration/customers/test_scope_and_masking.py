"""What each caller may see of a customer, and what a 404 has to look like (Task 02.5, D-14).

Four rules are pinned here, and they are the ones a regression would hide:

* **Scope is silence.** A support agent bound to site A asking for a customer of site B gets 404,
  not 403, and the filter `site_id=B` answers an empty page — the existence of the row must not
  leak through the shape of the answer. The refusal is written to the audit trail, because a
  denied read is the signal an auditor looks for (S-8).
* **Support sees a masked number, finance the row, operations a pseudonym.** The projection is
  chosen from the authenticated principal, never from a query parameter, and operations gets
  exactly the keys of `CustomerPseudonymous` — no name, no phone, no email.
* **A full number is a reveal.** Support needs `customer.read_pii`, and every staff read that
  returns an unmasked number is audited; the masked read is routine and is not.
* **The portal reads itself.** A customer token authenticates for `getCustomer` and reaches
  nothing else, not even the customer list.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client

from hub.customers import wiring as customers_wiring
from hub.identity import wiring as identity_wiring
from hub.identity.contracts import Principal
from hub.shared.clock import FrozenClock

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

CUSTOMERS = "/api/v1/customers"
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
PHONE = "+243810000042"
PSEUDONYMOUS_KEYS = {"id", "pseudonym", "site_id", "plan_id", "status", "created_at"}


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


class Fixture:
    """Two sites, one plan, and the customers/agents each test needs."""

    def __init__(self) -> None:
        self.site_a = uuid.uuid4()
        self.site_b = uuid.uuid4()
        self.plan = uuid.uuid4()
        self.customer_a = uuid.uuid4()
        self.customer_b = uuid.uuid4()

    def seed(self) -> Fixture:
        with connection.cursor() as cursor:
            for site_id, code in ((self.site_a, "SCOPE-A"), (self.site_b, "SCOPE-B")):
                cursor.execute(
                    "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, 'USD')",
                    [site_id, code, f"Site {code[-1]}"],
                )
            cursor.execute(
                "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, "
                "max_topup_minor) VALUES (%s, 'SCOPE-P', 'Scope plan', 'USD', 100, 100000)",
                [self.plan],
            )
            for customer_id, site_id, name in (
                (self.customer_a, self.site_a, "Amani Mbuyi"),
                (self.customer_b, self.site_b, "Bintu Kasongo"),
            ):
                # `created_at` is written from the frozen clock, not from the database's
                # `now()`: the creation-window filter is judged against that clock, and a row
                # stamped with the wall clock would be "in the future" on any later day.
                cursor.execute(
                    "INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164, "
                    "email, language, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'fr', 'active', %s)",
                    [
                        customer_id,
                        site_id,
                        self.plan,
                        name,
                        PHONE if customer_id == self.customer_a else "+243810000043",
                        f"{customer_id.hex[:8]}@example.test",
                        NOW,
                    ],
                )
        return self


@pytest.fixture
def fixture() -> Fixture:
    return Fixture().seed()


def seed_user(
    *roles: str, all_sites: bool = False, sites: tuple[UUID, ...] = ()
) -> tuple[UUID, str]:
    """An active staff user with a real token (site scope comes from ``user_sites``)."""
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
    return user_id, token_for(user_id)


def token_for(user_id: UUID) -> str:
    principal = identity_wiring.users().build_principal(
        user_id, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )


def customer_token(customer_id: UUID) -> str:
    """A portal token: the customer is the subject, and the claim names the same row."""
    principal = Principal(subject_id=customer_id, kind="customer", customer_id=customer_id)
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=NOW, acr="otp", amr=("otp",)
    )


def get(client: Client, path: str, token: str) -> Any:
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


def grant(role: str, permission: str) -> None:
    """Give a role one more permission for the length of a test (rolled back with it)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.role_permissions (role_code, permission_code) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            [role, permission],
        )


def audit_rows(action: str) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT details FROM hub.audit_log WHERE action = %s ORDER BY seq DESC", [action]
        )
        rows = cursor.fetchall()
    return [json.loads(row[0]) if isinstance(row[0], str) else dict(row[0]) for row in rows]


@pytest.mark.tc("TC-FR-15-3")
def test_other_site_customer_404_and_list_excludes(client: Client, fixture: Fixture) -> None:
    _agent, token = seed_user("support_agent", sites=(fixture.site_a,))

    read = get(client, f"{CUSTOMERS}/{fixture.customer_b}", token)
    assert read.status_code == 404, read.json()
    assert read.json()["code"] == "not_found"

    filtered = get(client, f"{CUSTOMERS}?site_id={fixture.site_b}", token)
    assert filtered.status_code == 200
    assert filtered.json()["data"] == []
    assert filtered.json()["meta"]["has_next"] is False

    # The refusal is evidence, even though the client only ever saw "not found".
    denied = audit_rows("registry.customer.denied")
    assert denied and denied[0]["outcome"] == "denied"
    assert denied[0]["reason"] == "site_scope"


def test_all_sites_user_sees_all(client: Client, fixture: Fixture) -> None:
    _agent, token = seed_user("support_agent", all_sites=True)

    listed = get(client, CUSTOMERS, token).json()["data"]
    assert {row["id"] for row in listed} == {str(fixture.customer_a), str(fixture.customer_b)}
    assert get(client, f"{CUSTOMERS}/{fixture.customer_b}", token).status_code == 200


def test_support_sees_masked_phone(client: Client, fixture: Fixture) -> None:
    _agent, token = seed_user("support_agent", all_sites=True)

    masked = get(client, f"{CUSTOMERS}/{fixture.customer_a}", token).json()["data"]
    assert masked["phone_e164"] == "+243*******42"
    assert masked["full_name"] == "Amani Mbuyi"
    assert audit_rows("registry.customer.revealed") == [], "a masked read is not a reveal"

    # The same caller with `customer.read_pii`: the number, and an audit row for the reveal.
    grant("support_agent", "customer.read_pii")
    revealed = get(client, f"{CUSTOMERS}/{fixture.customer_a}", token)
    assert revealed.status_code == 200, revealed.json()
    assert revealed.json()["data"]["phone_e164"] == PHONE
    audit = audit_rows("registry.customer.revealed")
    assert len(audit) == 1
    assert audit[0]["outcome"] == "success"


def test_operations_gets_pseudonymous_fields_only(client: Client, fixture: Fixture) -> None:
    _engineer, token = seed_user("operations_engineer", all_sites=True)

    document = get(client, f"{CUSTOMERS}/{fixture.customer_a}", token).json()["data"]
    assert set(document) == PSEUDONYMOUS_KEYS
    assert document["pseudonym"].startswith("c-")
    assert len(document["pseudonym"]) == len("c-") + 12

    listed = get(client, CUSTOMERS, token).json()["data"]
    assert all(set(row) == PSEUDONYMOUS_KEYS for row in listed)
    assert len({row["pseudonym"] for row in listed}) == 2, "the pseudonym is per customer"

    # A stored pseudonym wins over the computed one, and the read never writes it.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE hub.customers SET pseudonym = 'c-stored-0001' WHERE id = %s",
            [fixture.customer_a],
        )
    stored = get(client, f"{CUSTOMERS}/{fixture.customer_a}", token).json()["data"]
    assert stored["pseudonym"] == "c-stored-0001"


def test_operations_cannot_filter_by_phone_or_name(client: Client, fixture: Fixture) -> None:
    _engineer, token = seed_user("operations_engineer", all_sites=True)

    by_phone = get(client, f"{CUSTOMERS}?phone_e164={PHONE}", token)
    assert by_phone.status_code == 422, by_phone.json()
    assert by_phone.json()["errors"][0]["field"] == "/phone_e164"

    by_name = get(client, f"{CUSTOMERS}?q=Amani", token)
    assert by_name.status_code == 422, by_name.json()
    assert by_name.json()["errors"][0]["field"] == "/q"


def test_finance_sees_full_fields(client: Client, fixture: Fixture) -> None:
    _officer, token = seed_user("finance_officer", all_sites=True)

    document = get(client, f"{CUSTOMERS}/{fixture.customer_a}", token).json()["data"]
    assert document["phone_e164"] == PHONE
    assert document["email"].endswith("@example.test")
    assert len(audit_rows("registry.customer.revealed")) == 1


def test_administrator_403_on_customers(client: Client, fixture: Fixture) -> None:
    _administrator, token = seed_user("administrator", all_sites=True)

    assert get(client, CUSTOMERS, token).status_code == 403
    assert get(client, f"{CUSTOMERS}/{fixture.customer_a}", token).status_code == 403


def test_customer_reads_only_self(client: Client, fixture: Fixture) -> None:
    token = customer_token(fixture.customer_a)

    own = get(client, f"{CUSTOMERS}/{fixture.customer_a}", token)
    assert own.status_code == 200, own.json()
    assert own.json()["data"]["id"] == str(fixture.customer_a)

    assert get(client, f"{CUSTOMERS}/{fixture.customer_b}", token).status_code == 404
    assert get(client, CUSTOMERS, token).status_code == 403


def test_list_filters_on_search_and_creation_window(client: Client, fixture: Fixture) -> None:
    _agent, token = seed_user("support_agent", all_sites=True)

    by_name = get(client, f"{CUSTOMERS}?q=Ama", token).json()["data"]
    assert [row["id"] for row in by_name] == [str(fixture.customer_a)]

    by_phone_prefix = get(client, f"{CUSTOMERS}?q=%2B2438100000", token).json()["data"]
    assert len(by_phone_prefix) == 2

    future = quote((NOW + timedelta(days=1)).isoformat(), safe="")
    after_window = get(client, f"{CUSTOMERS}?created_after={future}", token).json()["data"]
    assert after_window == []
    before_window = get(client, f"{CUSTOMERS}?created_before={future}", token).json()["data"]
    assert len(before_window) == 2
