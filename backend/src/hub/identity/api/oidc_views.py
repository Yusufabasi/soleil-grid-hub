"""The two OIDC endpoints: ``GET /auth/oidc/start`` and ``GET /auth/oidc/callback`` (D-47).

Both are plain Django views because both answer with a 302 and a cookie, and because the
provider drives the second one with a query string rather than a body. Failures are problem
documents, never redirects: a callback that could not be completed must not look like a
successful sign-in to the web application.

The cookie is the browser binding: ``HttpOnly``, ``Secure``, ``SameSite=Lax`` (Lax is required —
the callback arrives as a top-level navigation from the provider, where ``Strict`` would send no
cookie at all) and scoped to this path, so no other route ever sees it.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET

from hub.identity import wiring
from hub.identity.api.cookie_views import _client_ip, _user_agent, set_refresh_cookie
from hub.shared.api.problem import problem_response, retry_after, to_problem
from hub.shared.context import request_id_var
from hub.shared.errors import DomainError, InvalidRequest

__all__ = ["oidc_callback_view", "oidc_start_view"]

OIDC_COOKIE_PATH = "/api/v1/auth/oidc"
MAX_RETURN_TO = 512
Handler = Callable[..., HttpResponse]


def _cookie_name() -> str:
    return str(settings.HUB_OIDC_STATE_COOKIE_NAME)


def _return_to(request: HttpRequest) -> str:
    """Only a same-site absolute path is honoured; anything else becomes ``/``.

    This is the one place a redirect target comes from the request, so it is also the one place
    an open redirect could start: ``//evil.example`` and ``https://evil.example`` both look like
    paths to a naive check.
    """
    candidate = str(request.GET.get("return_to") or "")
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or len(candidate) > MAX_RETURN_TO
    ):
        return "/"
    if urlsplit(candidate).scheme or urlsplit(candidate).netloc:
        return "/"
    return candidate


def _problem(request: HttpRequest, exc: DomainError) -> HttpResponse:
    status, body = to_problem(
        exc,
        instance=request.path,
        request_id=request_id_var.get() or "",
        accept_language=request.headers.get("Accept-Language"),
    )
    return problem_response(status, body, retry_after_seconds=retry_after(exc))


def _as_problem(view: Handler) -> Handler:
    """Convert a domain error into problem+json: these views have no DRF handler behind them."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        try:
            return view(request, *args, **kwargs)
        except DomainError as exc:
            return _problem(request, exc)

    return wrapped


def _set_state_cookie(response: HttpResponse, value: str) -> None:
    response.set_cookie(
        _cookie_name(),
        value,
        max_age=600,
        path=OIDC_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="Lax",
    )


def _clear_state_cookie(response: HttpResponse) -> None:
    response.set_cookie(
        _cookie_name(),
        "",
        max_age=0,
        path=OIDC_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="Lax",
    )


@require_GET
@_as_problem
def oidc_start_view(request: HttpRequest) -> HttpResponse:
    """``oidcStart``: 302 to the provider with PKCE, state and a binding cookie."""
    binding = str(request.COOKIES.get(_cookie_name()) or "") or None
    url, cookie_value = wiring.oidc().authorization_url(binding, return_to=_return_to(request))
    response = HttpResponse(status=302)
    response["Location"] = url
    _set_state_cookie(response, cookie_value)
    return response


@require_GET
def oidc_callback_view(request: HttpRequest) -> HttpResponse:
    """``oidcCallback``: exchange the code, set the refresh cookie, send the browser on."""
    try:
        provider_error = str(request.GET.get("error") or "")
        if provider_error:
            # The provider refused (consent withdrawn, account unknown there): nothing to
            # exchange, and the message is the provider's own code, not a token.
            raise InvalidRequest(code="invalid_request")
        code = str(request.GET.get("code") or "")
        state = str(request.GET.get("state") or "")
        if not code or not state:
            raise InvalidRequest(code="invalid_request")
        completion = wiring.oidc().complete(
            code,
            state,
            str(request.COOKIES.get(_cookie_name()) or "") or None,
            ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except DomainError as exc:
        # The pending flow is spent either way, so the cookie must not survive the failure.
        response = _problem(request, exc)
        _clear_state_cookie(response)
        return response

    response = HttpResponse(status=302)
    response["Location"] = completion.return_to
    set_refresh_cookie(response, completion.refresh_token, max_age=completion.expires_in)
    _clear_state_cookie(response)
    return response
