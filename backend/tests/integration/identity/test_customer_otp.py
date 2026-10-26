"""Customer sign-in by one-time code, and the abuse controls in front of it (TC-FR-15-16).

The endpoint is public and costs an SMS per call, so the tests here are the ones an attacker
would run: does a known number differ from an unknown one, does a decoy send nothing, can a
solved puzzle be spent twice, does an unserved country get through, do the three hourly ceilings
hold, does guessing five times kill the challenge, and does a customer dormant for ninety days
have to start over. All of it is asserted on the HTTP surface, with a frozen clock so the
customer's five-minute idle limit can be crossed exactly.

The proof difficulty is dropped to two zero bits: eighteen is right for a browser and absurd in
a test, and the scheme is the same either way.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client

from hub.identity import wiring
from hub.identity.application.proof import leading_zero_bits
from hub.shared.clock import FrozenClock

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

CHALLENGE_URL = "/api/v1/auth/proof-challenge"
REQUEST_URL = "/api/v1/auth/customer/otp/request"
VERIFY_URL = "/api/v1/auth/customer/otp/verify"
REFRESH_URL = "/api/v1/auth/refresh"
COOKIE = "sg_refresh"
ORIGIN = "http://localhost:8080"
CSRF = {"HTTP_ORIGIN": ORIGIN, "HTTP_X_HUB_CSRF": "1"}
NAMESPACE = uuid.UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0200")
NOW = datetime(2026, 10, 24, 9, 0, 0, tzinfo=UTC)

PHONE = "+243810000001"
UNKNOWN = "+243819999999"
SITE_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0201")
PLAN_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0202")


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def clock(settings: Any) -> FrozenClock:
    """A frozen clock, and a puzzle cheap enough to solve in the test process."""
    settings.HUB_POW_DIFFICULTY_BITS = 2
    frozen = FrozenClock(NOW)
    wiring.use_clock(frozen)
    return frozen


def solve(client: Client) -> tuple[str, str]:
    """Fetch a challenge and return ``(solved_token, challenge_secret)``."""
    response = client.get(CHALLENGE_URL)
    assert response.status_code == 200, response.content
    data = response.json()["data"]
    secret, difficulty = str(data["challenge"]), int(data["difficulty"])
    nonce = 0
    while leading_zero_bits(hashlib.sha256(f"{secret}.{nonce}".encode()).digest()) < difficulty:
        nonce += 1
    return f"{secret}.{nonce}", secret


def request_code(
    client: Client,
    *,
    phone: str = PHONE,
    proof: str | None = None,
    ip: str = "203.0.113.7",
    locale: str | None = None,
) -> Any:
    """``POST /auth/customer/otp/request`` with a fresh solve unless one is supplied."""
    body: dict[str, Any] = {"phone_e164": phone, "proof_token": proof or solve(client)[0]}
    if locale is not None:
        body["locale"] = locale
    return client.post(
        REQUEST_URL, data=body, content_type="application/json", headers={"X-Forwarded-For": ip}
    )


def verify(client: Client, challenge_id: str, code: str) -> Any:
    return client.post(
        VERIFY_URL,
        data={"challenge_id": challenge_id, "code": code},
        content_type="application/json",
    )


def refresh(client: Client) -> Any:
    """`Client.post` with Django's keyword headers; `Any` because the stubs are strict."""
    extra: Any = dict(CSRF)
    return client.post(REFRESH_URL, **extra)


def seed_customer(*, phone: str | None = PHONE) -> UUID:
    """An active customer on its own site and plan; ``phone`` is the e164 column."""
    customer_id = uuid.uuid5(NAMESPACE, phone or "no-phone")
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            [SITE_ID, "SITE-OTP", "Site OTP", "USD"],
        )
        cursor.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [PLAN_ID, "PLAN-OTP", "Plan OTP", "USD", 100, 100000],
        )
        cursor.execute(
            "INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164, language) "
            "VALUES (%s, %s, %s, %s, %s, 'fr') ON CONFLICT (id) DO NOTHING",
            [customer_id, SITE_ID, PLAN_ID, "Customer OTP", phone],
        )
    return customer_id


def record_sign_in(customer_id: UUID, at: datetime) -> None:
    """A finished login: the session row is what the dormancy rule reads."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.refresh_tokens "
            "(id, customer_id, family_id, token_hash, issued_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                uuid.uuid4(),
                customer_id,
                uuid.uuid4(),
                uuid.uuid4().bytes + uuid.uuid4().bytes,
                at,
                at + timedelta(seconds=43200),
            ],
        )


def purpose_of(challenge_id: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT purpose FROM hub.customer_otp WHERE id = %s", [challenge_id])
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def test_unknown_and_known_phone_indistinguishable(client: Client, clock: FrozenClock) -> None:
    """The same status and the same body shape, whether or not anybody owns the number."""
    seed_customer()

    known = request_code(client)
    unknown = request_code(client, phone=UNKNOWN)

    assert known.status_code == unknown.status_code == 202
    assert set(known.json()["data"]) == set(unknown.json()["data"])
    known_body, unknown_body = known.json()["data"], unknown.json()["data"]
    assert known_body["challenge_id"] != unknown_body["challenge_id"]
    assert known_body["expires_in"] == unknown_body["expires_in"]
    assert known_body["resend_after"] == unknown_body["resend_after"]


def test_no_sms_for_unknown_number(client: Client, clock: FrozenClock) -> None:
    """A decoy row is written; nothing leaves the process."""
    seed_customer()

    request_code(client, phone=UNKNOWN)

    assert wiring.notifier().sent == []
    # The decoy row exists, with the attempt budget that makes it unverifiable.
    with connection.cursor() as cursor:
        cursor.execute("SELECT max_attempts FROM hub.customer_otp ORDER BY created_at DESC LIMIT 1")
        row = cursor.fetchone()
    assert row is not None and int(row[0]) == 0


def test_known_number_gets_exactly_one_message(client: Client, clock: FrozenClock) -> None:
    seed_customer()

    request_code(client, locale="fr")

    sent = wiring.notifier().sent
    assert len(sent) == 1
    assert sent[0].phone == PHONE
    assert sent[0].locale == "fr"
    assert sent[0].code.isdigit() and len(sent[0].code) == 6


@pytest.mark.tc("TC-FR-15-16")
def test_proof_required_and_replay_refused(client: Client, clock: FrozenClock) -> None:
    """An unsolved puzzle and a spent one are the same answer: solve a new one."""
    seed_customer()

    forged = request_code(client, proof="not-a-challenge.0")
    assert forged.status_code == 422
    assert forged.json()["code"] == "proof_required"

    solved, _secret = solve(client)
    assert request_code(client, proof=solved).status_code == 202
    replayed = request_code(client, proof=solved)
    assert replayed.status_code == 422
    assert replayed.json()["code"] == "proof_required"


def test_prefix_not_in_allow_list_blocked(client: Client, clock: FrozenClock) -> None:
    """A country the product does not serve never pays for a puzzle or reaches the database."""
    refused = request_code(client, phone="+99912345678")

    assert refused.status_code == 422
    assert refused.json()["errors"][0]["code"] == "prefix_not_allowed"
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM hub.customer_otp")
        assert int(cursor.fetchone()[0]) == 0


def test_burst_per_phone_per_ip_per_network_429(client: Client, clock: FrozenClock) -> None:
    """Three ceilings, in the order the service checks them: number, address, /24."""
    # Per phone: 3 an hour. Each attempt uses a different address so only the phone counter bites.
    for index in range(3):
        allowed = request_code(client, phone="+243820000001", ip=f"198.18.0.{index + 1}")
        assert allowed.status_code == 202, allowed.content
    refused = request_code(client, phone="+243820000001", ip="198.18.0.9")
    assert refused.status_code == 429
    assert refused.json()["code"] == "rate_limited"
    assert refused["Retry-After"] == "3600"

    # Per address: 10 an hour, however many numbers are tried.
    for index in range(10):
        allowed = request_code(client, phone=f"+2438300000{index:02d}", ip="198.18.1.1")
        assert allowed.status_code == 202, allowed.content
    refused = request_code(client, phone="+243830000099", ip="198.18.1.1")
    assert refused.status_code == 429

    # Per /24: 30 an hour, spread over three addresses of the same subnet.
    for host in (2, 3, 4):
        for index in range(10):
            number = f"+24384000{host - 2}{index:03d}"
            allowed = request_code(client, phone=number, ip=f"198.18.2.{host}")
            assert allowed.status_code == 202, allowed.content
    refused = request_code(client, phone="+243849999999", ip="198.18.2.5")
    assert refused.status_code == 429


@pytest.mark.tc("TC-FR-15-16")
def test_five_wrong_codes_kill_challenge(client: Client, clock: FrozenClock) -> None:
    """The fifth wrong guess spends the challenge; the sixth attempt is refused even if right."""
    seed_customer()
    challenge_id = request_code(client).json()["data"]["challenge_id"]
    right = wiring.notifier().sent[-1].code

    for _ in range(5):
        wrong = verify(client, challenge_id, "000000" if right != "000000" else "111111")
        assert wrong.status_code == 401
        assert wrong.json()["code"] == "otp_invalid"

    late = verify(client, challenge_id, right)
    assert late.status_code == 401
    assert late.json()["code"] == "otp_invalid"


def test_dormant_customer_requires_reverify(client: Client, clock: FrozenClock) -> None:
    """Ninety days without a sign-in: the challenge asks to re-verify, and a login one is dead."""
    dormant_id = seed_customer(phone="+243810000002")
    record_sign_in(dormant_id, NOW - timedelta(days=91))

    reverify = request_code(client, phone="+243810000002")
    assert reverify.status_code == 202
    challenge_id = reverify.json()["data"]["challenge_id"]
    assert purpose_of(challenge_id) == "reverify"
    code = wiring.notifier().sent[-1].code
    assert verify(client, challenge_id, code).status_code == 200

    # A challenge requested before dormancy is a login challenge, and cannot sign in now.
    stale_id = seed_customer(phone="+243810000003")
    stale = request_code(client, phone="+243810000003").json()["data"]["challenge_id"]
    stale_code = wiring.notifier().sent[-1].code
    record_sign_in(stale_id, NOW - timedelta(days=91))
    refused = verify(client, stale, stale_code)
    assert refused.status_code == 401
    assert refused.json()["code"] == "otp_invalid"


@pytest.mark.tc("TC-FR-15-16")
def test_customer_session_idle_300s(client: Client, clock: FrozenClock) -> None:
    """The customer idle limit is five minutes, not the staff thirty."""
    seed_customer()
    challenge_id = request_code(client).json()["data"]["challenge_id"]
    code = wiring.notifier().sent[-1].code

    signed_in = verify(client, challenge_id, code)
    assert signed_in.status_code == 200, signed_in.content
    document = signed_in.json()["data"]
    assert document["principal"]["type"] == "customer"
    assert document["expires_in"] == 600
    assert COOKIE in signed_in.cookies

    clock.advance(timedelta(seconds=299))
    fresh = refresh(client)
    assert fresh.status_code == 200, fresh.content

    clock.advance(timedelta(seconds=301))
    expired = refresh(client)
    assert expired.status_code == 401
    assert expired.json()["code"] == "session_expired"
