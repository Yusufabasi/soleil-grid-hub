"""WebAuthn: real attestation, real signatures, and the roles that may not use TOTP.

The software authenticator in ``support.authenticator`` builds the CBOR structures a browser
would, with a genuine P-256 key, so every check the server performs is exercised: challenge,
origin, relying-party id, the user-verification flag, and the signature over the authenticator
data. The enforcement test is the one that changes behaviour: with
``HUB_WEBAUTHN_ENFORCED=True`` a finance officer's TOTP is refused while a support agent's is
still accepted (TC-FR-15-10).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pyotp
import pytest
from django.test import Client
from webauthn.helpers import base64url_to_bytes

from hub.identity import wiring
from hub.identity.infrastructure.models import WebauthnCredential
from hub.shared.clock import FrozenClock
from support.authenticator import SoftwareAuthenticator
from support.login import PASSWORD, LocalUser, seed_local_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

ORIGIN = "http://localhost:8080"
RP_ID = "localhost"
REGISTRATION_OPTIONS = "/api/v1/auth/webauthn/registration-options"
REGISTRATIONS = "/api/v1/auth/webauthn/registrations"
ASSERTION_OPTIONS = "/api/v1/auth/webauthn/assertion-options"
ASSERTIONS = "/api/v1/auth/webauthn/assertions"
LOGIN = "/api/v1/auth/staff/login"
VERIFY = "/api/v1/auth/staff/mfa/verify"
NOW = datetime(2026, 10, 24, 9, 0, 15, tzinfo=UTC)


def post(client: Client, url: str, body: dict[str, Any] | None = None, **extra: Any) -> Any:
    return client.post(url, data=body, content_type="application/json", **extra)


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def clock() -> FrozenClock:
    frozen = FrozenClock(NOW)
    wiring.use_clock(frozen)
    return frozen


def access_token(user: LocalUser, clock: FrozenClock) -> str:
    return wiring.tokens().issue(
        user.principal,
        session_id=uuid.uuid4(),
        auth_time=clock.now(),
        acr="mfa",
    )


def register(
    client: Client,
    user: LocalUser,
    authenticator: SoftwareAuthenticator,
    clock: FrozenClock,
    *,
    user_verified: bool = True,
) -> Any:
    token = access_token(user, clock)
    options = post(client, REGISTRATION_OPTIONS, {}, HTTP_AUTHORIZATION=f"Bearer {token}")
    assert options.status_code == 200, options.content
    issued = options.json()["data"]
    challenge = _challenge_of(issued["options"], "registration")
    credential = authenticator.registration(challenge, user_verified=user_verified)
    return post(
        client,
        REGISTRATIONS,
        {
            "challenge_id": issued["challenge_id"],
            "credential": credential,
            "name": "test key",
        },
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def _challenge_of(options: dict[str, Any], _purpose: str) -> bytes:
    return base64url_to_bytes(options["challenge"])


def mfa_token(client: Client, user: LocalUser) -> str:
    response = post(client, LOGIN, {"email": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.content
    return str(response.json()["data"]["mfa_token"])


def assert_with(
    client: Client,
    user: LocalUser,
    authenticator: SoftwareAuthenticator,
    *,
    user_verified: bool = True,
    sign_count: int | None = None,
) -> Any:
    token = mfa_token(client, user)
    options = post(client, ASSERTION_OPTIONS, {"mfa_token": token})
    assert options.status_code == 200, options.content
    issued = options.json()["data"]
    challenge = _challenge_of(issued["options"], "assertion")
    credential = authenticator.assertion(
        challenge, user_verified=user_verified, sign_count=sign_count
    )
    return post(
        client,
        ASSERTIONS,
        {"mfa_token": token, "challenge_id": issued["challenge_id"], "credential": credential},
    )


@pytest.mark.tc("TC-FR-15-10")
def test_register_then_assert_logs_in(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("finance_officer")
    authenticator = SoftwareAuthenticator(origin=ORIGIN, rp_id=RP_ID, user_id=user.user_id)

    registered = register(client, user, authenticator, clock)
    assert registered.status_code == 201, registered.content
    stored = registered.json()["data"]
    assert stored["name"] == "test key"
    assert WebauthnCredential.objects.filter(user_id=user.user_id).count() == 1

    response = assert_with(client, user, authenticator)

    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["access_token"]
    assert data["principal"]["acr"] == "phr"
    assert client.cookies["sg_refresh"].value


@pytest.mark.tc("TC-FR-15-10")
def test_user_verification_required(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    authenticator = SoftwareAuthenticator(origin=ORIGIN, rp_id=RP_ID, user_id=user.user_id)

    refused = register(client, user, authenticator, clock, user_verified=False)

    assert refused.status_code == 422
    assert WebauthnCredential.objects.filter(user_id=user.user_id).count() == 0


@pytest.mark.tc("TC-FR-15-10")
def test_sign_count_regression_refused(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    authenticator = SoftwareAuthenticator(origin=ORIGIN, rp_id=RP_ID, user_id=user.user_id)
    assert register(client, user, authenticator, clock).status_code == 201

    ahead = assert_with(client, user, authenticator, sign_count=5)
    assert ahead.status_code == 200, ahead.content

    # A cloned authenticator replaying an older counter: refused, never repaired.
    regression = assert_with(client, user, authenticator, sign_count=3)
    assert regression.status_code == 401
    assert regression.json()["code"] == "invalid_code"


@pytest.mark.tc("TC-FR-15-10")
def test_finance_officer_totp_refused_when_enforced(
    client: Client, clock: FrozenClock, settings: Any
) -> None:
    settings.HUB_WEBAUTHN_ENFORCED = True
    wiring.reset()
    wiring.use_clock(clock)
    officer = seed_local_user("finance_officer")
    agent = seed_local_user("support_agent")

    officer_token = mfa_token(client, officer)
    officer_try = post(
        client,
        VERIFY,
        {"mfa_token": officer_token, "totp_code": pyotp.TOTP(officer.secret).at(clock.now())},
    )
    assert officer_try.status_code == 403
    assert officer_try.json()["code"] == "webauthn_required"

    agent_token = mfa_token(client, agent)
    agent_try = post(
        client,
        VERIFY,
        {"mfa_token": agent_token, "totp_code": pyotp.TOTP(agent.secret).at(clock.now())},
    )
    assert agent_try.status_code == 200, agent_try.content


@pytest.mark.tc("TC-FR-15-10")
def test_enforced_role_challenge_offers_webauthn_only(
    client: Client, clock: FrozenClock, settings: Any
) -> None:
    settings.HUB_WEBAUTHN_ENFORCED = True
    wiring.reset()
    wiring.use_clock(clock)
    officer = seed_local_user("finance_officer")

    response = post(client, LOGIN, {"email": officer.email, "password": PASSWORD})

    assert response.status_code == 200
    assert response.json()["data"]["methods"] == ["webauthn"]
    assert response.json()["data"]["enrollment_required"] is True


@pytest.mark.tc("TC-FR-15-10")
def test_challenge_is_single_use(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    authenticator = SoftwareAuthenticator(origin=ORIGIN, rp_id=RP_ID, user_id=user.user_id)
    token = access_token(user, clock)
    options = post(client, REGISTRATION_OPTIONS, {}, HTTP_AUTHORIZATION=f"Bearer {token}")
    issued = options.json()["data"]
    credential = authenticator.registration(_challenge_of(issued["options"], "registration"))

    first = post(
        client,
        REGISTRATIONS,
        {"challenge_id": issued["challenge_id"], "credential": credential},
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert first.status_code == 201
    replay = post(
        client,
        REGISTRATIONS,
        {"challenge_id": issued["challenge_id"], "credential": credential},
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert replay.status_code == 401


@pytest.mark.tc("TC-FR-15-10")
def test_registration_requires_a_bearer(client: Client) -> None:
    response = post(client, REGISTRATION_OPTIONS, {})

    assert response.status_code == 401
