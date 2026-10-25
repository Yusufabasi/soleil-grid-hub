"""The cookie guard: same Origin and the explicit header, or nothing (D-46, TC-FR-15-17).

Django's CSRF middleware is not installed, so this decorator *is* the CSRF control on the two
endpoints that read a cookie. Four ways of failing it are checked — no Origin, a foreign
Origin, no ``X-Hub-CSRF`` and ``X-Hub-CSRF: 0`` — and the bearer operations must ignore a
cookie entirely, so a stolen cookie alone never authenticates a normal request.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from django.test import Client

from hub.identity import wiring
from support.principals import SeededUser, seed_staff_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
COOKIE = "sg_refresh"
ORIGIN = "http://localhost:8080"


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def agent() -> SeededUser:
    return seed_staff_user("support_agent")


def post(client: Client, url: str, **extra: Any) -> Any:
    """`Client.post` with Django's keyword headers; `Any` because the stubs are strict."""
    return client.post(url, **extra)


def session_token(user: SeededUser) -> str:
    return (
        wiring.sessions()
        .issue(
            user_id=user.user_id,
            customer_id=None,
            auth_time=datetime.now(UTC),
            acr="mfa",
        )
        .token
    )


@pytest.mark.tc("TC-FR-15-17")
@pytest.mark.parametrize("url", [REFRESH_URL, LOGOUT_URL])
@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_X_HUB_CSRF": "1"},  # no Origin at all
        {"HTTP_ORIGIN": "https://evil.example", "HTTP_X_HUB_CSRF": "1"},
        {"HTTP_ORIGIN": ORIGIN},  # no header
        {"HTTP_ORIGIN": ORIGIN, "HTTP_X_HUB_CSRF": "0"},
    ],
    ids=["no-origin", "foreign-origin", "no-header", "header-zero"],
)
def test_refresh_and_logout_reject_a_missing_guard(
    client: Client, agent: SeededUser, url: str, headers: dict[str, str]
) -> None:
    token = session_token(agent)
    client.cookies[COOKIE] = token

    response = post(client, url, **headers)

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_check_failed"
    # Nothing was spent: the session still works through the guarded route.
    assert post(
        client,
        REFRESH_URL,
        HTTP_ORIGIN=ORIGIN,
        HTTP_X_HUB_CSRF="1",
        HTTP_COOKIE=f"{COOKIE}={token}",
    ).status_code in (200, 401)


@pytest.mark.tc("TC-FR-15-17")
def test_bearer_endpoint_ignores_cookie(client: Client, agent: SeededUser) -> None:
    """A bearer operation must not accept a cookie as a credential."""
    client.cookies[COOKIE] = session_token(agent)

    response = client.get("/api/v1/audit-log?stream=auth")

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.tc("TC-FR-15-17")
def test_guard_passes_with_origin_and_header(client: Client, agent: SeededUser) -> None:
    client.cookies[COOKIE] = session_token(agent)

    response = post(client, REFRESH_URL, HTTP_ORIGIN=ORIGIN, HTTP_X_HUB_CSRF="1")

    assert response.status_code == 200


@pytest.mark.tc("TC-FR-15-17")
def test_utf8_origin_header_is_not_a_wildcard(client: Client, agent: SeededUser) -> None:
    client.cookies[COOKIE] = session_token(agent)

    response = post(client, REFRESH_URL, HTTP_ORIGIN=ORIGIN + "/", HTTP_X_HUB_CSRF="1")

    assert response.status_code == 403
