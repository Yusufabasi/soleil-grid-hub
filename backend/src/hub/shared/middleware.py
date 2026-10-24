"""Middleware of the kernel (docs/03 section 7.3 and 7.1).

``RequestContextMiddleware`` is first: it accepts or replaces the inbound request id, binds
the request context, records request metrics and clears the context again in ``finally``.
``DomainErrorMiddleware`` is last: it turns a ``DomainError`` into an RFC 9457 problem
document (task 00.5); until then it passes the response through untouched.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from hub.bootstrap import get_container
from hub.shared.context import bind_context, reset_context
from hub.shared.metrics import HTTP_DURATION, HTTP_REQUESTS

GetResponse = Callable[[HttpRequest], HttpResponse]

# Only the balancer may supply an id, and only a well-formed one (D-13): an inbound value
# is echoed back in a response header, so anything else is replaced by a fresh UUIDv7.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def _route_label(request: HttpRequest) -> str:
    """The URL *template*, never a concrete id, so metrics cardinality stays bounded."""
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", None) if match is not None else None
    return route or "unmatched"


class RequestContextMiddleware:
    """Bind request_id, correlation_id and entrypoint, and record the request metrics."""

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        trusted = bool(settings.HUB_TRUST_LB_REQUEST_ID)
        inbound = request.META.get("HTTP_X_REQUEST_ID", "")
        # An untrusted or malformed id is replaced, never echoed (header injection, D-13).
        request_id = inbound if trusted and _REQUEST_ID_RE.fullmatch(inbound) else None
        if request_id is None:
            request_id = str(get_container().ids.new_id())

        correlation_inbound = request.META.get("HTTP_X_CORRELATION_ID", "")
        if trusted and _REQUEST_ID_RE.fullmatch(correlation_inbound):
            correlation_id = correlation_inbound
        else:
            correlation_id = request_id

        tokens = bind_context(
            request_id=request_id,
            correlation_id=correlation_id,
            entrypoint=str(getattr(settings, "SERVICE_NAME", "hub")),
        )
        started = time.perf_counter()
        try:
            response = self.get_response(request)
        finally:
            reset_context(tokens)

        response["X-Request-Id"] = request_id
        labels = {
            "service": str(getattr(settings, "SERVICE_NAME", "hub")),
            "route": _route_label(request),
            "code": str(response.status_code),
        }
        HTTP_REQUESTS.labels(**labels).inc()
        HTTP_DURATION.labels(**labels).observe(time.perf_counter() - started)
        return response


class DomainErrorMiddleware:
    """Placeholder until task 00.5 maps DomainError to problem+json."""

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)
