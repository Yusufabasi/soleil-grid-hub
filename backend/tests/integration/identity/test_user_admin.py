"""``/users`` and ``/roles``: four proposals, one direct write, two administrators (FR-15).

The tests follow one shape because the contract has one shape: a privileged change is proposed
(202, nothing written), refused to its own maker, and applied when a different administrator
approves it. What the approvals tests prove about the service in general, these prove about the
four user actions in particular — that they write nothing before the decision, and that what
they write afterwards is complete (roles, attributes, sites, token version, sessions).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client

from hub.identity import wiring
from hub.shared import clock as kernel_clock
from hub.shared.clock import FrozenClock
from support.login import seed_local_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

USERS = "/api/v1/users"
ROLES = "/api/v1/roles"
NOW = datetime(2026, 10, 24, 9, 0, 15, tzinfo=UTC)


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def clock() -> Iterator[FrozenClock]:
    frozen = FrozenClock(NOW)
    wiring.use_clock(frozen)
    yield frozen
    wiring.reset()
    kernel_clock.use_clock(None)


def token_for(user: Any, clock: FrozenClock) -> str:
    return wiring.tokens().issue(
        user.principal, session_id=uuid.uuid4(), auth_time=clock.now(), acr="mfa"
    )


def call(
    client: Client,
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
) -> Any:
    return getattr(client, method)(
        path,
        data=body,
        content_type="application/json" if body is not None else None,
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def approve(client: Client, token: str, approval_id: str) -> Any:
    return call(client, "post", f"/api/v1/approvals/{approval_id}/approve", token, {})


def user_document(client: Client, token: str, user_id: UUID) -> Any:
    return call(client, "get", f"{USERS}/{user_id}", token)


def seed_site() -> UUID:
    site_id = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0201")
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency, timezone, status) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [site_id, "SITE-U", "Site U", "USD", "Africa/Kinshasa", "active"],
        )
    return site_id


@pytest.mark.tc("TC-FR-15-10")
def test_create_user_returns_202_and_no_user_until_second_admin_approves(
    client: Client, clock: FrozenClock
) -> None:
    proposer = seed_local_user("administrator")
    approver = seed_local_user("administrator")
    email = f"new-{uuid.uuid4()}@example.test"

    created = call(
        client,
        "post",
        USERS,
        token_for(proposer, clock),
        {
            "email": email,
            "full_name": "New Support Agent",
            "roles": ["support_agent"],
            "site_ids": [str(seed_site())],
            "reason": "Second support agent for the Lubumbashi queue",
        },
    )

    assert created.status_code == 202, created.content
    approval = created.json()["data"]
    assert approval["kind"] == "user_creation"
    assert approval["status"] == "pending"
    assert created["Location"] == f"/api/v1/approvals/{approval['id']}"
    listed = call(client, "get", USERS, token_for(approver, clock))
    assert email not in [row["email"] for row in listed.json()["data"]]

    # The maker cannot be the checker, even from another browser.
    refused = approve(client, token_for(proposer, clock), approval["id"])
    assert refused.status_code == 403
    assert refused.json()["code"] == "self_approval_forbidden"

    granted = approve(client, token_for(approver, clock), approval["id"])
    assert granted.status_code == 200, granted.content
    assert granted.json()["data"]["status"] == "executed"

    listed = call(client, "get", USERS, token_for(approver, clock))
    rows = {row["email"]: row for row in listed.json()["data"]}
    assert email in rows
    assert rows[email]["status"] == "invited"
    assert rows[email]["roles"] == ["support_agent"]
    assert rows[email]["full_name"] == "New Support Agent"


def test_create_user_with_a_taken_email_is_refused(client: Client, clock: FrozenClock) -> None:
    proposer = seed_local_user("administrator")
    existing = seed_local_user("support_agent")

    refused = call(
        client,
        "post",
        USERS,
        token_for(proposer, clock),
        {
            "email": existing.email,
            "full_name": "Duplicate",
            "roles": ["support_agent"],
            "reason": "Someone typed an address that already exists",
        },
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "duplicate_resource"


@pytest.mark.tc("TC-FR-15-12")
def test_role_grant_executes_on_approval_and_bumps_token_version(
    client: Client, clock: FrozenClock
) -> None:
    proposer = seed_local_user("administrator")
    approver = seed_local_user("administrator")
    target = seed_local_user("support_agent")
    old_token = token_for(target, clock)

    created = call(
        client,
        "put",
        f"{USERS}/{target.user_id}/roles",
        token_for(proposer, clock),
        {
            "roles": ["finance_officer"],
            "attributes": ["finance_lead"],
            "reason": "Moving to the reconciliation desk",
        },
    )
    assert created.status_code == 202, created.content
    approval_id = created.json()["data"]["id"]
    # Nothing changed yet: the old token still authenticates and the role is still the old one.
    assert user_document(client, token_for(approver, clock), target.user_id).json()["data"][
        "roles"
    ] == ["support_agent"]

    granted = approve(client, token_for(approver, clock), approval_id)
    assert granted.status_code == 200, granted.content

    after = user_document(client, token_for(approver, clock), target.user_id).json()["data"]
    assert after["roles"] == ["finance_officer"]
    assert after["attributes"] == ["finance_lead"]

    # Every access token the target held is dead, not merely less powerful (D-17).
    stale = user_document(client, old_token, target.user_id)
    assert stale.status_code == 401
    assert stale.json()["code"] == "token_revoked"


@pytest.mark.tc("TC-FR-15-12")
def test_replace_user_sites_requires_approval(client: Client, clock: FrozenClock) -> None:
    proposer = seed_local_user("administrator")
    approver = seed_local_user("administrator")
    target = seed_local_user("support_agent")
    site_id = seed_site()

    created = call(
        client,
        "put",
        f"{USERS}/{target.user_id}/sites",
        token_for(proposer, clock),
        {"site_ids": [str(site_id)], "reason": "Now covers the second site too"},
    )
    assert created.status_code == 202, created.content
    before = user_document(client, token_for(approver, clock), target.user_id).json()["data"]
    assert before["site_ids"] == []

    granted = approve(client, token_for(approver, clock), created.json()["data"]["id"])
    assert granted.status_code == 200, granted.content

    after = user_document(client, token_for(approver, clock), target.user_id).json()["data"]
    assert after["site_ids"] == [str(site_id)]


@pytest.mark.tc("TC-FR-15-11")
def test_mfa_reset_has_no_effect_until_second_administrator_approves(
    client: Client, clock: FrozenClock
) -> None:
    proposer = seed_local_user("administrator")
    approver = seed_local_user("administrator")
    target = seed_local_user("finance_officer")
    _seed_recovery_code(target.user_id)
    session = wiring.sessions().issue(
        user_id=target.user_id, customer_id=None, auth_time=clock.now(), acr="mfa"
    )

    created = call(
        client,
        "post",
        f"{USERS}/{target.user_id}/mfa-reset",
        token_for(proposer, clock),
        {"reason": "Lost both the phone and the recovery codes"},
    )
    assert created.status_code == 202, created.content
    approval_id = created.json()["data"]["id"]
    assert created.json()["data"]["kind"] == "mfa_reset"

    # Still enrolled, still usable, one approval short.
    still = user_document(client, token_for(approver, clock), target.user_id).json()["data"]
    assert still["mfa_enrolled"] is True
    assert _secret_present(target.user_id) is True
    assert _recovery_codes(target.user_id) == 1
    assert _session_revoked(session.session_id) is False

    granted = approve(client, token_for(approver, clock), approval_id)
    assert granted.status_code == 200, granted.content

    after = user_document(client, token_for(approver, clock), target.user_id).json()["data"]
    assert after["mfa_enrolled"] is False
    assert after["webauthn_enrolled"] is False
    assert _secret_present(target.user_id) is False
    assert _recovery_codes(target.user_id) == 0
    assert _session_revoked(session.session_id) is True


@pytest.mark.tc("TC-FR-15-11")
def test_mfa_reset_requires_two_administrators(client: Client, clock: FrozenClock) -> None:
    proposer = seed_local_user("administrator")
    target = seed_local_user("support_agent")
    _seed_recovery_code(target.user_id)

    created = call(
        client,
        "post",
        f"{USERS}/{target.user_id}/mfa-reset",
        token_for(proposer, clock),
        {"reason": "Phone replaced, factor must be re-enrolled"},
    )
    assert created.status_code == 202, created.content

    refused = approve(client, token_for(proposer, clock), created.json()["data"]["id"])
    assert refused.status_code == 403
    assert refused.json()["code"] == "self_approval_forbidden"
    assert _secret_present(target.user_id) is True
    assert _recovery_codes(target.user_id) == 1


def test_update_user_writes_directly_and_suspension_revokes_tokens(
    client: Client, clock: FrozenClock
) -> None:
    actor = seed_local_user("administrator")
    target = seed_local_user("support_agent")
    old_token = token_for(target, clock)

    renamed = call(
        client,
        "patch",
        f"{USERS}/{target.user_id}",
        token_for(actor, clock),
        {"full_name": "Renamed Agent", "locale": "fr"},
    )
    assert renamed.status_code == 200, renamed.content
    assert renamed.json()["data"]["full_name"] == "Renamed Agent"
    assert renamed.json()["data"]["locale"] == "fr"

    suspended = call(
        client,
        "patch",
        f"{USERS}/{target.user_id}",
        token_for(actor, clock),
        {"status": "suspended"},
    )
    assert suspended.status_code == 200, suspended.content
    assert suspended.json()["data"]["status"] == "suspended"
    stale = user_document(client, old_token, target.user_id)
    assert stale.status_code == 401
    assert stale.json()["code"] == "token_revoked"


@pytest.mark.tc("TC-FR-15-13")
def test_last_administrator_cannot_be_demoted(client: Client, clock: FrozenClock) -> None:
    only_admin = seed_local_user("administrator")

    refused = call(
        client,
        "put",
        f"{USERS}/{only_admin.user_id}/roles",
        token_for(only_admin, clock),
        {"roles": ["support_agent"], "reason": "There is nobody else left to approve"},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "conflict"


@pytest.mark.tc("TC-FR-15-13")
def test_at_most_three_administrators(client: Client, clock: FrozenClock) -> None:
    proposer = seed_local_user("administrator")
    seed_local_user("administrator")
    seed_local_user("administrator")
    fourth = seed_local_user("finance_officer")

    refused = call(
        client,
        "put",
        f"{USERS}/{fourth.user_id}/roles",
        token_for(proposer, clock),
        {"roles": ["finance_officer", "administrator"], "reason": "A fourth administrator"},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "conflict"


def test_list_roles_and_users(client: Client, clock: FrozenClock) -> None:
    admin = seed_local_user("administrator")
    token = token_for(admin, clock)

    roles = call(client, "get", ROLES, token)
    assert roles.status_code == 200
    catalogue = {row["name"]: row for row in roles.json()["data"]}
    assert set(catalogue) == {
        "support_agent",
        "finance_officer",
        "operations_engineer",
        "administrator",
    }
    assert "user.admin" in catalogue["administrator"]["permissions"]
    assert "ledger.adjust" not in catalogue["administrator"]["permissions"]

    users = call(client, "get", f"{USERS}?role=administrator&limit=10", token)
    assert users.status_code == 200
    assert users.json()["meta"]["limit"] == 1
    assert [row["id"] for row in users.json()["data"]] == [str(admin.user_id)]


def _seed_recovery_code(user_id: UUID) -> None:
    """One unused code, so a reset has something to delete (the seeder writes none)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.mfa_recovery_codes (id, user_id, code_hash) VALUES (%s, %s, %s)",
            [uuid.uuid4(), user_id, b"\x00" * 32],
        )


def _secret_present(user_id: UUID) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT mfa_secret_enc IS NOT NULL FROM hub.users WHERE id = %s", [user_id])
        row = cursor.fetchone()
    return bool(row is not None and row[0])


def _recovery_codes(user_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM hub.mfa_recovery_codes WHERE user_id = %s AND used_at IS NULL",
            [user_id],
        )
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def _session_revoked(session_id: UUID) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT revoked_at FROM hub.refresh_tokens WHERE id = %s", [session_id])
        row = cursor.fetchone()
    return row is not None and row[0] is not None
