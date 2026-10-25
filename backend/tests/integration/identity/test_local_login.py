"""Break-glass login: indistinguishable failures, lockout, one-time codes (TC-NFR-06-2).

The flows that matter are the ones an attacker would probe: does a wrong password differ from
an unknown address; does the fifth failure stop the sixth; can a TOTP code be replayed inside
its own step; can a recovery code be used twice. Each is asserted against the HTTP surface,
with a frozen clock so the thirty-second step can be moved exactly one and two steps.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pyotp
import pytest
from django.test import Client

from hub.identity import wiring
from hub.identity.infrastructure.models import MfaRecoveryCode, User
from hub.shared.clock import FrozenClock
from hub.shared.security import hash_recovery_code
from support.login import PASSWORD, LocalUser, seed_local_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

LOGIN_URL = "/api/v1/auth/staff/login"
VERIFY_URL = "/api/v1/auth/staff/mfa/verify"
ENROLL_URL = "/api/v1/auth/staff/mfa/enrollments"
CONFIRM_URL = "/api/v1/auth/staff/mfa/enrollments/confirm"
CODES_URL = "/api/v1/auth/staff/mfa/recovery-codes"
NOW = datetime(2026, 10, 24, 9, 0, 15, tzinfo=UTC)


def post(client: Client, url: str, **extra: Any) -> Any:
    return client.post(url, **extra)


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def clock() -> FrozenClock:
    frozen = FrozenClock(NOW)
    wiring.use_clock(frozen)
    return frozen


def start(client: Client, email: str, password: str = PASSWORD, **extra: Any) -> Any:
    return post(
        client,
        LOGIN_URL,
        data={"email": email, "password": password},
        content_type="application/json",
        **extra,
    )


def code_for(user: LocalUser, clock: FrozenClock) -> str:
    return pyotp.TOTP(user.secret).at(clock.now())


def verify(client: Client, mfa_token: str, **extra: Any) -> Any:
    return post(
        client,
        VERIFY_URL,
        data={"mfa_token": mfa_token, **extra},
        content_type="application/json",
    )


def mfa_token_for(client: Client, user: LocalUser) -> str:
    response = start(client, user.email)
    assert response.status_code == 200, response.content
    return str(response.json()["data"]["mfa_token"])


@pytest.mark.tc("TC-NFR-06-2")
def test_wrong_password_and_unknown_email_identical(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    # Every request gets its own source address, so this test measures the response rather
    # than tripping the per-address lockout it is not about.
    counter = iter(range(1, 32))

    def timed(email: str) -> tuple[Any, float]:
        address = f"198.51.100.{next(counter)}"
        started = time.perf_counter()
        response = start(client, email, "not-the-right-password", REMOTE_ADDR=address)
        return response, time.perf_counter() - started

    timed("warmup@example.test")  # the first request pays Django's lazy imports
    wrong = [timed(user.email) for _ in range(3)]
    unknown = [timed("nobody@example.test") for _ in range(3)]

    assert all(response.status_code == 401 for response, _ in wrong + unknown)
    assert {response.json()["code"] for response, _ in wrong + unknown} == {"invalid_credentials"}
    details = {response.json()["detail"] for response, _ in wrong + unknown}
    assert len(details) == 1

    # Both paid a full Argon2id verification, which dominates either request: the two must be
    # in the same league, or the response is a signal about whether the account exists.
    mean_wrong = sum(seconds for _, seconds in wrong) / len(wrong)
    mean_unknown = sum(seconds for _, seconds in unknown) / len(unknown)
    assert abs(mean_wrong - mean_unknown) < 0.5 * max(mean_wrong, mean_unknown)


@pytest.mark.tc("TC-NFR-06-2")
def test_progressive_lockout_per_account_and_ip(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    for _ in range(5):
        assert start(client, user.email, "wrong-password-value").status_code == 401

    limited = start(client, user.email, "wrong-password-value")
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]

    # Even the right password is refused while the window is closed.
    assert start(client, user.email, PASSWORD).status_code == 429


@pytest.mark.tc("TC-NFR-06-2")
def test_login_never_returns_access_token(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")

    response = start(client, user.email)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mfa_required"] is True
    assert data["mfa_token_expires_in"] == 300
    assert data["enrollment_required"] is False
    assert "totp" in data["methods"]
    assert "access_token" not in data
    assert "refresh_token" not in data


@pytest.mark.tc("TC-NFR-06-2")
def test_mfa_verify_returns_tokens(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("finance_officer")
    token = mfa_token_for(client, user)

    response = verify(client, token, totp_code=code_for(user, clock))

    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["access_token"]
    assert data["token_type"] == "Bearer"
    assert data["principal"]["roles"] == ["finance_officer"]
    assert data["principal"]["acr"] == "mfa"
    assert client.cookies["sg_refresh"].value


@pytest.mark.tc("TC-NFR-06-2")
def test_totp_replay_refused(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    code = pyotp.TOTP(user.secret).at(clock.now())

    first = verify(client, mfa_token_for(client, user), totp_code=code)
    assert first.status_code == 200

    # A second challenge, the same six digits: the counter was already spent.
    second = verify(client, mfa_token_for(client, user), totp_code=code)
    assert second.status_code == 401
    assert second.json()["code"] == "invalid_code"


@pytest.mark.tc("TC-NFR-06-2")
def test_totp_drift_one_step_accepted_two_refused(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    totp = pyotp.TOTP(user.secret)

    one_ahead = totp.at(clock.now() + timedelta(seconds=30))
    assert verify(client, mfa_token_for(client, user), totp_code=one_ahead).status_code == 200

    two_ahead = totp.at(clock.now() + timedelta(seconds=60))
    refused = verify(client, mfa_token_for(client, user), totp_code=two_ahead)
    assert refused.status_code == 401
    assert refused.json()["code"] == "invalid_code"


@pytest.mark.tc("TC-NFR-06-2")
def test_recovery_code_single_use(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    wiring.users().replace_recovery_codes(
        user.user_id, [hash_recovery_code("ABCDEF2345"), hash_recovery_code("ZZZZZZ9999")]
    )

    first = verify(client, mfa_token_for(client, user), recovery_code="abcdef 2345")
    assert first.status_code == 200, first.content

    replay = verify(client, mfa_token_for(client, user), recovery_code="ABCDEF2345")
    assert replay.status_code == 401
    assert replay.json()["code"] == "invalid_code"


@pytest.mark.tc("TC-NFR-06-2")
def test_regenerate_codes_invalidates_old_and_returns_ten(
    client: Client, clock: FrozenClock
) -> None:
    user = seed_local_user("support_agent")
    wiring.users().replace_recovery_codes(user.user_id, [hash_recovery_code("OLDC0DE123")])
    token = wiring.tokens().issue(
        user.principal,
        session_id=uuid.uuid4(),
        auth_time=clock.now(),
        acr="mfa",
    )

    response = post(
        client, CODES_URL, HTTP_AUTHORIZATION=f"Bearer {token}", content_type="application/json"
    )

    assert response.status_code == 201, response.content
    codes = response.json()["data"]["recovery_codes"]
    assert len(codes) == 10
    assert wiring.users().count_unused_recovery_codes(user.user_id) == 10
    old = MfaRecoveryCode.objects.filter(
        user_id=user.user_id, code_hash=hash_recovery_code("OLDC0DE123")
    )
    assert not old.exists()


@pytest.mark.tc("TC-NFR-06-2")
def test_enrollment_confirm_stores_encrypted_secret(client: Client, clock: FrozenClock) -> None:
    newcomer = seed_local_user("support_agent", mfa=False)

    started = start(client, newcomer.email)
    assert started.status_code == 200
    challenge = started.json()["data"]
    assert challenge["enrollment_required"] is True
    mfa_token = challenge["mfa_token"]

    enrollment = post(
        client,
        ENROLL_URL,
        data={"mfa_token": mfa_token},
        content_type="application/json",
    )
    assert enrollment.status_code == 201, enrollment.content
    issued = enrollment.json()["data"]
    assert issued["otpauth_uri"].startswith("otpauth://totp/")
    assert len(issued["recovery_codes"]) == 10

    secret = pyotp.parse_uri(issued["otpauth_uri"]).secret
    confirmed = post(
        client,
        CONFIRM_URL,
        data={"mfa_token": mfa_token, "totp_code": pyotp.TOTP(secret).at(clock.now())},
        content_type="application/json",
    )

    assert confirmed.status_code == 200, confirmed.content
    assert confirmed.json()["data"]["access_token"]
    row = User.objects.get(id=newcomer.user_id)
    assert row.mfa_secret_enc is not None
    assert bytes(row.mfa_secret_enc) != secret.encode(), "the secret is encrypted at rest"
    assert wiring.users().count_unused_recovery_codes(newcomer.user_id) == 10


@pytest.mark.tc("TC-NFR-06-2")
def test_suspended_account_refused_with_the_same_answer(client: Client, clock: FrozenClock) -> None:
    suspended = seed_local_user("support_agent", status="suspended")

    response = start(client, suspended.email)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_verify_with_an_unknown_challenge_refused(client: Client, clock: FrozenClock) -> None:
    response = verify(client, "mfa_not-a-challenge", totp_code="123456")

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_code"
