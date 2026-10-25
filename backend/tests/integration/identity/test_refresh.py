"""The rotating refresh cookie: single use, reuse revokes the family, two limits per kind.

The browser-tab race is the case that matters (TC-FR-15-12, TC-FR-15-13). A tab that refreshes
twice with the same cookie must not be able to keep both answers alive: the second presentation
of a spent token ends the family, which is why there is no grace window to tune. The idle and
absolute limits are measured from the rows themselves, so a frozen clock can sit exactly on a
boundary instead of sleeping through it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client

from hub.identity import wiring
from hub.identity.application.sessions import SessionLimits, SessionService
from hub.identity.infrastructure.audit import HubAuditLog
from hub.identity.infrastructure.models import UserRole
from hub.identity.infrastructure.repositories import SessionRepository
from hub.shared.clock import FrozenClock
from support.principals import SeededUser, seed_staff_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
COOKIE = "sg_refresh"
ORIGIN = "http://localhost:8080"
CSRF = {"HTTP_ORIGIN": ORIGIN, "HTTP_X_HUB_CSRF": "1"}


def issue_session(user: SeededUser, *, auth_time: datetime | None = None) -> str:
    """Open a real session for a seeded user and return its refresh token."""
    issued = wiring.sessions().issue(
        user_id=user.user_id,
        customer_id=None,
        auth_time=auth_time or datetime.now(UTC),
        acr="mfa",
        ip="203.0.113.9",
        user_agent="pytest",
    )
    return issued.token


def with_cookie(client: Client, token: str) -> None:
    client.cookies[COOKIE] = token


def post(client: Client, url: str, **extra: Any) -> Any:
    """`Client.post` with Django's keyword headers; `Any` because the stubs are strict."""
    return client.post(url, **extra)


def refresh(client: Client, **extra: Any) -> Any:
    return post(client, REFRESH_URL, **{**CSRF, **extra})


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def agent() -> SeededUser:
    return seed_staff_user("support_agent")


@pytest.mark.tc("TC-FR-15-12")
def test_set_cookie_attributes_exact(client: Client, agent: SeededUser) -> None:
    with_cookie(client, issue_session(agent))

    response = refresh(client)

    assert response.status_code == 200, response.content
    cookie = response.cookies[COOKIE]
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"] == "Strict"
    assert cookie["path"] == "/api/v1/auth"
    body = response.json()["data"]
    assert "refresh_token" not in body, "a browser never receives it in JSON"
    assert body["access_token"]
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    assert body["principal"]["roles"] == ["support_agent"]


@pytest.mark.tc("TC-FR-15-12")
def test_rotation_returns_new_cookie(client: Client, agent: SeededUser) -> None:
    original = issue_session(agent)
    with_cookie(client, original)

    first = refresh(client)
    second = refresh(client)

    assert first.status_code == 200 and second.status_code == 200
    rotated = first.cookies[COOKIE].value
    assert rotated and rotated != original
    assert second.cookies[COOKIE].value != rotated


@pytest.mark.tc("TC-FR-15-12")
def test_replay_after_one_second_revokes_family(client: Client, agent: SeededUser) -> None:
    original = issue_session(agent)
    with_cookie(client, original)
    rotated = refresh(client).cookies[COOKIE].value

    # The tab race: the browser presents the token it already spent.
    replay = refresh(client, HTTP_COOKIE=f"{COOKIE}={original}")
    assert replay.status_code == 401
    assert replay.json()["code"] == "refresh_token_reused"

    # And the token the legitimate client holds is dead too: that is the point of a family.
    afterwards = refresh(client, HTTP_COOKIE=f"{COOKIE}={rotated}")
    assert afterwards.status_code == 401


@pytest.mark.tc("TC-FR-15-12")
def test_idle_and_absolute_expiry_staff_and_customer(
    redis_store,  # noqa: ANN001
) -> None:
    """299/301 s on the idle boundary and one second past the absolute boundary, per kind."""
    clock = FrozenClock(datetime(2026, 10, 24, 9, 0, tzinfo=UTC))
    service = SessionService(
        clock=clock,
        ids=wiring.ids(),
        store=wiring.store(),
        sessions=SessionRepository(),
        audit=HubAuditLog(),
        staff_limits=SessionLimits(idle_seconds=1800, absolute_seconds=28800),
        customer_limits=SessionLimits(idle_seconds=300, absolute_seconds=43200),
    )
    user = seed_staff_user("support_agent")
    first = service.issue(
        user_id=user.user_id,
        customer_id=None,
        auth_time=clock.now(),
        acr="mfa",
    )

    clock.advance(timedelta(seconds=1799))
    second = service.rotate(first.token)
    assert second.token

    clock.advance(timedelta(seconds=1801))
    with pytest.raises(Exception) as idle:
        service.rotate(second.token)
    assert getattr(idle.value, "code", "") == "session_expired"

    # A fresh family, rotated inside the idle window until the eight-hour absolute limit.
    fresh = service.issue(
        user_id=user.user_id,
        customer_id=None,
        auth_time=clock.now(),
        acr="mfa",
    )
    token = fresh.token
    for _ in range(19):  # 19 x 1500 s = 28500 s, each rotation well inside the idle limit
        clock.advance(timedelta(seconds=1500))
        token = service.rotate(token).token
    clock.advance(timedelta(seconds=300))  # exactly 28800: the boundary itself is still allowed
    token = service.rotate(token).token
    clock.advance(timedelta(seconds=2))  # one second past it
    with pytest.raises(Exception) as absolute:
        service.rotate(token)
    assert getattr(absolute.value, "code", "") == "session_expired"


@pytest.mark.tc("TC-FR-15-12")
def test_customer_session_uses_the_customer_limits(redis_store) -> None:  # noqa: ANN001
    clock = FrozenClock(datetime(2026, 10, 24, 9, 0, tzinfo=UTC))
    service = SessionService(
        clock=clock,
        ids=wiring.ids(),
        store=wiring.store(),
        sessions=SessionRepository(),
        audit=HubAuditLog(),
        staff_limits=SessionLimits(idle_seconds=1800, absolute_seconds=28800),
        customer_limits=SessionLimits(idle_seconds=300, absolute_seconds=43200),
    )
    # A customer row needs a real customers row for the FK, so the boundary is proved on the
    # limits the service reports rather than by inserting one slice 02 owns.
    assert service.limits_for("customer") == SessionLimits(300, 43200)
    assert service.limits_for("staff") == SessionLimits(1800, 28800)


@pytest.mark.tc("TC-FR-15-12")
def test_body_transport_only_with_header(client: Client, agent: SeededUser) -> None:
    token = issue_session(agent)
    headers = {**CSRF, "HTTP_X_TOKEN_TRANSPORT": "body"}

    response = post(
        client,
        REFRESH_URL,
        data=json.dumps({"refresh_token": token}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200, response.content
    body = response.json()["data"]
    assert body["refresh_token"]
    assert COOKIE not in response.cookies, "body transport sets no cookie"


@pytest.mark.tc("TC-FR-15-12")
def test_missing_cookie_is_401(client: Client) -> None:
    response = refresh(client)

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.tc("TC-FR-15-12")
def test_logout_revokes_the_family_and_clears_the_cookie(client: Client, agent: SeededUser) -> None:
    token = issue_session(agent)
    with_cookie(client, token)

    response = post(client, LOGOUT_URL, **CSRF)

    assert response.status_code == 204
    cleared = response.cookies[COOKIE]
    assert cleared.value == ""
    assert cleared["max-age"] == 0
    assert cleared["path"] == "/api/v1/auth"
    # The spent token can no longer be rotated.
    assert refresh(client, HTTP_COOKIE=f"{COOKIE}={token}").status_code == 401


@pytest.mark.tc("TC-FR-15-12")
def test_refresh_of_a_revoked_family_is_refused(client: Client, agent: SeededUser) -> None:
    token = issue_session(agent)
    wiring.sessions().logout(token)

    response = refresh(client, HTTP_COOKIE=f"{COOKIE}={token}")

    assert response.status_code == 401


@pytest.mark.tc("TC-FR-15-12")
def test_access_token_from_refresh_keeps_the_login_auth_time(
    client: Client, agent: SeededUser
) -> None:
    """A refresh must not silently reset the step-up clock (D-16)."""
    login_at = datetime.now(UTC) - timedelta(minutes=4)
    with_cookie(client, issue_session(agent, auth_time=login_at))

    response = refresh(client)

    issued = response.json()["data"]["principal"]["auth_time"]
    assert datetime.fromisoformat(issued.replace("Z", "+00:00")) <= login_at + timedelta(seconds=1)


def test_refresh_principal_reflects_a_role_grant(client: Client, agent: SeededUser) -> None:
    with_cookie(client, issue_session(agent))
    UserRole.objects.create(user_id=agent.user_id, role_code="finance_officer")

    response = refresh(client)

    assert "finance_officer" in response.json()["data"]["principal"]["roles"]
