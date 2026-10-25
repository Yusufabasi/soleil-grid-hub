"""OIDC sign-in against a loopback provider: PKCE, state binding, claims, provisioning.

Every test drives the two views directly with ``RequestFactory``; the routes are behind
``HUB_OIDC_ROUTES_ENABLED`` and are not mounted here. The provider is the real HTTP one in
``support.idp``, so the discovery document, the JWKS signature check and the PKCE exchange are
all the production code paths.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from hub.identity import wiring
from hub.identity.api.oidc_views import oidc_callback_view, oidc_start_view
from hub.identity.infrastructure.models import User
from hub.shared.clock import FrozenClock
from support.idp import FakeIdp, IdpIdentity
from support.login import seed_local_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

COOKIE = "sg_oidc_state"
REDIRECT_URI = "http://localhost:8080/api/v1/auth/oidc/callback"
START = "/api/v1/auth/oidc/start"
CALLBACK = "/api/v1/auth/oidc/callback"
NOW = datetime(2026, 10, 24, 9, 0, 15, tzinfo=UTC)


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    provider = FakeIdp(
        client_id="hub-api-test",
        client_secret="idp-client-secret-never-in-a-redirect",
        identity=IdpIdentity(sub="idp-subject-0001"),
    ).start()
    try:
        yield provider
    finally:
        provider.stop()


@pytest.fixture
def clock() -> FrozenClock:
    frozen = FrozenClock(NOW)
    wiring.use_clock(frozen)
    return frozen


@pytest.fixture
def configured(idp: FakeIdp, clock: FrozenClock, settings: Any, redis_store: Any) -> FakeIdp:
    """Point the wiring at the loopback provider, with routes still unmounted."""
    settings.HUB_OIDC_ISSUER = idp.issuer
    settings.HUB_OIDC_CLIENT_ID = idp.client_id
    settings.HUB_OIDC_CLIENT_SECRET = idp.client_secret
    settings.HUB_OIDC_REDIRECT_URI = REDIRECT_URI
    wiring.reset()
    wiring.use_clock(clock)
    return idp


def start(factory: RequestFactory, **query: str) -> Any:
    request: HttpRequest = factory.get(START, query)
    return oidc_start_view(request)


def binding_of(response: Any) -> str:
    cookie = response.cookies[COOKIE]
    assert cookie["path"] == "/api/v1/auth/oidc"
    return str(cookie.value)


def callback(factory: RequestFactory, *, code: str, state: str, binding: str | None) -> Any:
    query = urlencode({"code": code, "state": state})
    headers = {"HTTP_COOKIE": f"{COOKIE}={binding}"} if binding else {}
    return oidc_callback_view(factory.get(f"/api/v1/auth/oidc/callback?{query}", **headers))


def problem(response: Any) -> dict[str, Any]:
    """The problem document of a refusal: these views answer with problem+json, not DRF."""
    assert response["Content-Type"].startswith("application/problem+json")
    document: dict[str, Any] = json.loads(response.content)
    return document


def params_of(response: Any) -> dict[str, str]:
    location = response["Location"]
    return {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}


def sign_in(
    factory: RequestFactory,
    idp: FakeIdp,
    *,
    started: Any | None = None,
    sub: str | None = None,
    return_to: str | None = None,
) -> Any:
    """Play the browser through both views: start, provider, callback."""
    if sub is not None:
        idp.identity.sub = sub
    started = started or start(factory, **({"return_to": return_to} if return_to else {}))
    binding = binding_of(started)
    code = idp.authorize(str(started["Location"]))
    return callback(factory, code=code, state=params_of(started)["state"], binding=binding)


@pytest.mark.tc("TC-FR-15-1")
def test_start_redirect_has_pkce_and_state_cookie(configured: FakeIdp) -> None:
    response = start(RequestFactory(), return_to="/dashboard")

    assert response.status_code == 302
    params = params_of(response)
    assert params["response_type"] == "code"
    assert params["client_id"] == configured.client_id
    assert params["code_challenge_method"] == "S256"
    assert len(params["code_challenge"]) == 43  # base64url of a SHA-256 digest, unpadded
    assert params["state"] and params["nonce"]
    assert params["state"] != params["nonce"]

    cookie = response.cookies[COOKIE]
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"] == "Lax"
    assert cookie["max-age"] == 600
    assert len(str(cookie.value)) >= 32


@pytest.mark.tc("TC-FR-15-1")
def test_callback_with_wrong_state_refused(configured: FakeIdp) -> None:
    factory = RequestFactory()
    started = start(factory)
    binding = binding_of(started)
    code = configured.authorize(str(started["Location"]))
    state = params_of(started)["state"]

    refused = callback(factory, code=code, state="not-the-state", binding=binding)

    assert refused.status_code == 400
    assert problem(refused)["code"] == "invalid_request"
    # The pending flow was spent by the refusal, so the same code cannot be retried.
    retried = callback(factory, code=code, state=state, binding=binding)
    assert retried.status_code == 400


@pytest.mark.tc("TC-FR-15-1")
def test_callback_without_mfa_amr_refused(configured: FakeIdp) -> None:
    configured.identity.amr = ("pwd",)
    user = seed_local_user("finance_officer", idp_subject=configured.identity.sub)

    response = sign_in(RequestFactory(), configured)

    assert response.status_code == 401
    assert problem(response)["code"] == "mfa_required"
    assert configured.token_requests == 1
    assert User.objects.get(id=user.user_id).last_login_at is None


@pytest.mark.tc("TC-FR-15-1")
def test_unknown_subject_refused_not_provisioned(configured: FakeIdp) -> None:
    before = User.objects.count()

    response = sign_in(RequestFactory(), configured, sub="idp-subject-unknown")

    assert response.status_code == 403
    assert problem(response)["code"] == "account_not_provisioned"
    assert User.objects.count() == before


@pytest.mark.tc("TC-FR-15-1")
def test_known_subject_gets_session_and_cookie(configured: FakeIdp) -> None:
    user = seed_local_user("finance_officer", idp_subject=configured.identity.sub)

    response = sign_in(RequestFactory(), configured, return_to="/dashboard")

    assert response.status_code == 302
    assert response["Location"] == "/dashboard"
    cookie = response.cookies["sg_refresh"]
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"] == "Strict"
    assert cookie["path"] == "/api/v1/auth"
    assert "access_token" not in response.content.decode()

    # The refresh token is a real session: it rotates into a new pair and names the user.
    rotation = wiring.sessions().rotate(str(cookie.value))
    assert rotation.user_id == user.user_id
    assert rotation.acr == "mfa"
    assert rotation.auth_time == NOW


@pytest.mark.tc("TC-FR-15-1")
def test_client_secret_never_in_redirect_or_response(configured: FakeIdp) -> None:
    seed_local_user("finance_officer", idp_subject=configured.identity.sub)
    factory = RequestFactory()
    started = start(factory)
    completed = sign_in(factory, configured, started=started)

    secret = configured.client_secret
    assert secret not in str(started["Location"])
    assert secret not in completed.content.decode()
    assert secret not in str(completed.cookies.get("sg_refresh") or "")
    # The token was still exchanged, so "absent" is not the sound of a failed request.
    assert configured.token_requests == 1
