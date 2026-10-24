"""Django middleware of the kernel (docs/03 section 7.3 and 7.1).

``RequestContextMiddleware`` is first: it accepts or replaces the inbound request id, binds
the request context and records request metrics. ``DomainErrorMiddleware`` is last: it turns
a ``DomainError`` into an RFC 9457 problem document. Both arrive with their own tasks
(00.4 and 00.5); until then they pass the request through untouched so that the settings
modules of task 00.2 can be checked and deployed.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

GetResponse = Callable[[HttpRequest], HttpResponse]


class RequestContextMiddleware:
    """Placeholder until task 00.4 binds request_id, correlation_id and metrics."""

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)


class DomainErrorMiddleware:
    """Placeholder until task 00.5 maps DomainError to problem+json."""

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)
