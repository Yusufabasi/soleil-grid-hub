"""The two cookie endpoints: ``POST /auth/refresh`` and ``POST /auth/logout`` (D-17, D-46).

They are plain Django views rather than DRF ones because DRF is built around a body and these
two read a cookie. Two consequences are deliberate:

* **Django's CSRF middleware is not installed** (D-46, and the settings test pins it). The
  guard here is the one the contract describes: ``Origin`` must equal ``SITE_ORIGIN`` and the
  request must carry ``X-Hub-CSRF: 1``. A browser cannot set that header cross-origin without
  a CORS preflight, and the cookie is ``SameSite=Strict`` on top of that.
* **The refresh token never reaches JSON** unless the client asked for body transport with
  ``X-Token-Transport: body``. A browser gets it in ``Set-Cookie`` only, with ``HttpOnly``,
  ``Secure``, ``SameSite=Strict`` and ``Path=/api/v1/auth`` — narrow enough that no other
  route ever sees it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from hub.identity import wiring
from hub.identity.api.serializers import token_pair_document
from hub.identity.contracts import Principal
from hub.shared.errors import InvalidRequest, PermissionDenied, Unauthenticated

__all__ = ["cookie_csrf_guard", "logout_view", "refresh_view"]

CSRF_HEADER = "X-Hub-CSRF"
TRANSPORT_HEADER = "X-Token-Transport"
BODY_TRANSPORT = "body"

Handler = Callable[..., HttpResponse]


def _json_body(request: HttpRequest) -> dict[str, Any]:
    """Parse the optional JSON body; an unparsable one is 400, not a crash."""
    raw = request.body or b""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidRequest(
            details={
                "errors": [{"field": "/", "code": "invalid_json", "message": "Body is not JSON."}]
            }
        ) from exc
    if not isinstance(parsed, dict):
        raise InvalidRequest()
    return parsed


def cookie_csrf_guard(view: Handler) -> Handler:
    """Refuse a cookie request that is not same-origin and explicitly marked as ours."""

    @wraps(view)
    def guarded(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        same_origin = request.headers.get("Origin") == str(settings.SITE_ORIGIN)
        marked = request.headers.get(CSRF_HEADER) == "1"
        if not (same_origin and marked):
            raise PermissionDenied(code="csrf_check_failed", message_key="csrf_check_failed")
        return view(request, *args, **kwargs)

    return guarded


def set_refresh_cookie(response: HttpResponse, token: str, *, max_age: int) -> None:
    """The one place the cookie's attributes are written (D-17)."""
    response.set_cookie(
        str(settings.HUB_REFRESH_COOKIE_NAME),
        token,
        max_age=max_age,
        path=str(settings.HUB_REFRESH_COOKIE_PATH),
        secure=True,
        httponly=True,
        samesite="Strict",
    )


def clear_refresh_cookie(response: HttpResponse) -> None:
    """Clear it with the same attributes, so a narrow Path is still matched."""
    response.set_cookie(
        str(settings.HUB_REFRESH_COOKIE_NAME),
        "",
        max_age=0,
        path=str(settings.HUB_REFRESH_COOKIE_PATH),
        secure=True,
        httponly=True,
        samesite="Strict",
    )


def _wants_body_transport(request: HttpRequest) -> bool:
    return request.headers.get(TRANSPORT_HEADER, "").lower() == BODY_TRANSPORT


def _body_token(body: dict[str, Any]) -> str:
    """The body's refresh token, for non-browser clients only (contract: opaque string)."""
    value = body.get("refresh_token")
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 256:
        raise InvalidRequest()
    return value


@csrf_exempt
@require_POST
@cookie_csrf_guard
def refresh_view(request: HttpRequest) -> HttpResponse:
    """Spend the current refresh token and answer with the next pair."""
    body_transport = _wants_body_transport(request)
    presented = ""
    if body_transport:
        presented = _body_token(_json_body(request))
    if not presented:
        presented = str(request.COOKIES.get(str(settings.HUB_REFRESH_COOKIE_NAME)) or "")
    if not presented:
        raise Unauthenticated()

    rotation = wiring.sessions().rotate(
        presented, ip=_client_ip(request), user_agent=_user_agent(request)
    )
    principal = _principal_for(rotation)
    access = wiring.tokens().issue(
        principal,
        session_id=rotation.session_id,
        auth_time=rotation.auth_time,
        acr=rotation.acr,
        # A customer signed in with the code alone; a staff session had a password first.
        amr=("otp",) if rotation.acr == "otp" else ("pwd", "otp"),
    )
    document = token_pair_document(
        access_token=access,
        expires_in=int(settings.HUB_ACCESS_TTL_SECONDS),
        principal=principal,
        refresh_expires_in=rotation.expires_in,
        refresh_token=rotation.token if body_transport else None,
    )
    response = JsonResponse({"data": document})
    if not body_transport:
        set_refresh_cookie(response, rotation.token, max_age=rotation.expires_in)
    return response


@csrf_exempt
@require_POST
@cookie_csrf_guard
def logout_view(request: HttpRequest) -> HttpResponse:
    """Revoke the session family and clear the cookie (204, always, once authorised)."""
    presented = str(request.COOKIES.get(str(settings.HUB_REFRESH_COOKIE_NAME)) or "")
    if not presented:
        presented = str(_json_body(request).get("refresh_token") or "")
    wiring.sessions().logout(presented, ip=_client_ip(request))
    response = HttpResponse(status=204)
    clear_refresh_cookie(response)
    return response


def _principal_for(rotation: Any) -> Principal:
    """The caller a rotated session belongs to: a staff account, or a customer."""
    if rotation.user_id is None:
        if rotation.customer_id is None:
            raise Unauthenticated()
        return wiring.customer_otp().principal_for(
            rotation.customer_id,
            session_id=rotation.session_id,
            auth_time=rotation.auth_time,
            acr=rotation.acr,
        )
    return wiring.users().build_principal(
        rotation.user_id,
        session_id=rotation.session_id,
        auth_time=rotation.auth_time,
        acr=rotation.acr,
    )


def _client_ip(request: HttpRequest) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _user_agent(request: HttpRequest) -> str | None:
    value = request.headers.get("User-Agent")
    return value[:512] if value else None
