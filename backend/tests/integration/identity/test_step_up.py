"""Step-up: the five-minute boundary, a fresh token, and every operation the contract guards.

Two of the guarded operations exist in this slice — regenerating recovery codes and registering
a security key — and they are exercised end to end. The rest arrive in 01.9 to 01.11, so the
rule itself is proved for all of them through ``step_up_is_fresh``: a rule that accepts a stale
principal would fail here before its handler is even written.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pyotp
import pytest
from django.test import Client

from hub.identity import wiring
from hub.shared.api.permissions import operation_rules, step_up_is_fresh
from hub.shared.clock import FrozenClock
from support.login import PASSWORD, seed_local_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

STEP_UP_URL = "/api/v1/auth/step-up"
REGENERATE = "/api/v1/auth/staff/mfa/recovery-codes"
REGISTER_KEY = "/api/v1/auth/webauthn/registration-options"
NOW = datetime(2026, 10, 24, 9, 0, 15, tzinfo=UTC)

GUARDED = sorted(
    operation_id
    for operation_id, rule in operation_rules().items()
    if rule.step_up_max_age_seconds is not None
)
MOUNTED = ["regenerateRecoveryCodes", "registerWebauthnCredential"]


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def clock() -> FrozenClock:
    frozen = FrozenClock(NOW)
    wiring.use_clock(frozen)
    return frozen


def access_token(user: Any, *, auth_time: datetime, session_id: Any = None) -> str:
    return wiring.tokens().issue(
        user.principal,
        session_id=session_id or uuid.uuid4(),
        auth_time=auth_time,
        acr="mfa",
    )


def call(client: Client, url: str, token: str, **kwargs: Any) -> Any:
    return client.post(url, HTTP_AUTHORIZATION=f"Bearer {token}", **kwargs)


def test_step_up_boundary_with_fake_clock(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    fresh = access_token(user, auth_time=clock.now() - timedelta(seconds=299))
    stale = access_token(user, auth_time=clock.now() - timedelta(seconds=301))

    accepted = call(client, REGENERATE, fresh)
    assert accepted.status_code == 201, accepted.content

    refused = call(client, REGENERATE, stale)
    assert refused.status_code == 401
    assert refused.json()["code"] == "step_up_required"
    assert refused.json()["step_up"] == {"max_age": 300}
    assert (
        refused["WWW-Authenticate"]
        == 'Bearer error="insufficient_user_authentication", max_age=300'
    )


def test_step_up_with_totp_returns_new_token_with_fresh_auth_time(
    client: Client, clock: FrozenClock
) -> None:
    user = seed_local_user("support_agent")
    session_id = uuid.uuid4()
    stale = access_token(user, auth_time=clock.now() - timedelta(minutes=10), session_id=session_id)

    response = call(
        client,
        STEP_UP_URL,
        stale,
        data={
            "method": "totp",
            "totp_code": pyotp.TOTP(user.secret).at(clock.now()),
        },
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["principal"]["acr"] == "mfa"
    assert data["principal"]["auth_time"] == NOW.isoformat().replace("+00:00", "Z")
    # The session is unchanged, so no second refresh cookie is minted.
    assert "sg_refresh" not in response.cookies

    stepped = call(client, REGENERATE, data["access_token"])
    assert stepped.status_code == 201, stepped.content


def test_step_up_code_cannot_be_replayed(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    token = access_token(user, auth_time=clock.now() - timedelta(minutes=10))
    body = {"method": "totp", "totp_code": pyotp.TOTP(user.secret).at(clock.now())}

    first = call(client, STEP_UP_URL, token, data=body, content_type="application/json")
    second = call(client, STEP_UP_URL, token, data=body, content_type="application/json")

    assert first.status_code == 200, first.content
    assert second.status_code == 401
    assert second.json()["code"] == "invalid_code"


def test_step_up_token_of_a_revoked_session_refused(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    stale = access_token(user, auth_time=clock.now() - timedelta(minutes=10))
    stepped = call(
        client,
        STEP_UP_URL,
        stale,
        data={"method": "totp", "totp_code": pyotp.TOTP(user.secret).at(clock.now())},
        content_type="application/json",
    )
    assert stepped.status_code == 200, stepped.content
    fresh = stepped.json()["data"]["access_token"]

    # A role change (or a suspension, or a sign-out everywhere) bumps the version and every
    # token of that session dies, including the one step-up just minted.
    wiring.users().bump_token_version(user.user_id)

    refused = call(client, REGENERATE, fresh)
    assert refused.status_code == 401
    assert refused.json()["code"] == "token_revoked"


@pytest.mark.parametrize("operation_id", MOUNTED)
def test_stale_authentication_blocks_each_mounted_guarded_operation(
    client: Client, clock: FrozenClock, operation_id: str
) -> None:
    user = seed_local_user("support_agent")
    stale = access_token(user, auth_time=clock.now() - timedelta(seconds=301))
    url = REGENERATE if operation_id == "regenerateRecoveryCodes" else REGISTER_KEY

    refused = call(client, url, stale, data={}, content_type="application/json")

    assert refused.status_code == 401
    assert refused.json()["code"] == "step_up_required"
    assert refused["WWW-Authenticate"].startswith("Bearer error=")


@pytest.mark.parametrize("operation_id", GUARDED)
def test_every_guarded_rule_refuses_a_stale_principal(
    clock: FrozenClock, operation_id: str
) -> None:
    user = seed_local_user("support_agent")
    rule = operation_rules()[operation_id]
    stale_time = clock.now() - timedelta(seconds=(rule.step_up_max_age_seconds or 300) + 1)

    stale = _with_auth_time(user.principal, stale_time)
    fresh = _with_auth_time(user.principal, clock.now() - timedelta(seconds=1))

    assert step_up_is_fresh(stale, rule, now=clock.now()) is False
    assert step_up_is_fresh(fresh, rule, now=clock.now()) is True


def _with_auth_time(principal: Any, auth_time: datetime) -> Any:
    return replace(principal, auth_time=auth_time)


def test_login_returns_no_token_and_step_up_needs_an_account(
    client: Client, clock: FrozenClock
) -> None:
    user = seed_local_user("support_agent")
    login = client.post(
        "/api/v1/auth/staff/login",
        data={"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert login.status_code == 200
    assert "access_token" not in login.content.decode()
