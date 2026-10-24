"""Middleware of the kernel (docs/03 section 7.3 and 7.1).

``RequestContextMiddleware`` is first: it accepts or replaces the inbound request id, binds
the request context, records request metrics and clears the context again in ``finally``.
``DomainErrorMiddleware`` is last: it turns a ``DomainError`` raised by a plain view into an
RFC 9457 problem document; DRF views are converted by the exception handler instead.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from hub.bootstrap import get_container
from hub.shared.api.problem import problem_response, retry_after, to_problem
from hub.shared.context import bind_context, request_id_var, reset_context
from hub.shared.errors import DomainError
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
    """Convert a ``DomainError`` raised by a plain view into problem+json (docs/03 7.1).

    DRF views never reach here: their exceptions go through ``exception_handler``, which
    applies the same converter. Non-domain exceptions propagate so Django's ``handler500``
    can answer with the generic internal error.
    """

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if not isinstance(exception, DomainError):
            return None
        status, body = to_problem(
            exception,
            instance=request.path,
            request_id=request_id_var.get() or "",
            accept_language=request.headers.get("Accept-Language"),
        )
        return problem_response(status, body, retry_after_seconds=retry_after(exception))
