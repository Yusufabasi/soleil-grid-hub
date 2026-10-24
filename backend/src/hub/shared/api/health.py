"""Liveness and readiness (docs/09 1.4 and 4.8).

Both are plain Django views, not DRF: health must answer even when a serializer, a
permission class or a parser is broken (docs/03 7.7 item 9). The body is the OpenAPI
``Health`` schema inside the usual ``{"data": ...}`` envelope, and every check name comes
from that schema's enum.

Semantics, and why they are these and not the opposite:

* liveness touches nothing. A dependency being down must not restart a healthy process.
* readiness fails (503) only for a *critical* check: the database, because the process can
  no longer do its job. Redis is not critical: docs/09 8.2 says an outage degrades the
  cache and the idempotency fast path without making the service unready, so it reports
  ``degraded`` and the instance stays in the pool.
* a draining instance is 503 before any check runs: the balancer must remove it, and a
  probe that first waits on the database delays exactly the deploy it protects.

The database check opens its own short-lived psycopg connection instead of using Django's
request connection, so a probe never inherits a poisoned connection, and it is bounded to
two seconds (docs/09 1.4: two failed checks remove a server).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import psycopg
import redis
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.urls import path

from hub.bootstrap import get_container
from hub.shared.api.problem import MethodNotAllowed, problem_response, to_problem
from hub.shared.context import request_id_var
from hub.shared.errors import ExternalUnavailable

__all__ = [
    "CheckResult",
    "check_postgres",
    "check_redis",
    "health_urlpatterns",
    "liveness",
    "readiness",
    "readiness_checks",
    "register_readiness_check",
    "unregister_readiness_check",
]

Status = Literal["ok", "degraded", "down"]

# docs/09 1.4: how long the balancer waits before retrying an instance that left the pool.
RETRY_AFTER_SECONDS = 5
# Bounded checks: a probe must answer inside the balancer's timeout, never queue behind it.
POSTGRES_CONNECT_TIMEOUT_SECONDS = 2
POSTGRES_STATEMENT_TIMEOUT_MS = 2000
REDIS_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one dependency probe, as ``Health.checks[]`` expects it."""

    status: Status
    latency_ms: int
    detail: str | None = None


CheckFn = Callable[[], CheckResult]


@dataclass(frozen=True)
class _RegisteredCheck:
    name: str
    fn: CheckFn
    critical: bool


_CHECKS: dict[str, _RegisteredCheck] = {}


def register_readiness_check(name: str, fn: CheckFn, *, critical: bool) -> None:
    """Add a probe to the registry (later slices add storage, queue and outbox lag)."""
    _CHECKS[name] = _RegisteredCheck(name=name, fn=fn, critical=critical)


def unregister_readiness_check(name: str) -> None:
    """Remove a probe; tests and later slices that replace an adapter use this."""
    _CHECKS.pop(name, None)


def readiness_checks() -> tuple[_RegisteredCheck, ...]:
    """The registered probes, in declaration order."""
    return tuple(_CHECKS.values())


def _timed(probe: Callable[[], str | None]) -> CheckResult:
    """Run a probe, measure it, and map any raised exception to ``down``.

    The detail never carries a DSN, a password or a driver message: readiness is served
    without authentication, so it names *which* dependency failed and nothing more.
    """
    started = time.perf_counter()
    try:
        detail = probe()
    except Exception:  # noqa: BLE001 - any failure of a probe is a failed probe
        return CheckResult(
            status="down",
            latency_ms=int((time.perf_counter() - started) * 1000),
            detail="unreachable",
        )
    return CheckResult(
        status="ok",
        latency_ms=int((time.perf_counter() - started) * 1000),
        detail=detail,
    )


def check_postgres() -> CheckResult:
    """``SELECT 1`` as this deployable's own role, bounded to two seconds."""

    def probe() -> str | None:
        database = settings.DATABASES["default"]
        options = database.get("OPTIONS") or {}
        with (
            psycopg.connect(
                dbname=str(database.get("NAME") or ""),
                user=str(database.get("USER") or ""),
                password=str(database.get("PASSWORD") or ""),
                host=str(database.get("HOST") or ""),
                port=str(database.get("PORT") or ""),
                connect_timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
                # The alias's application_name, so a probe is still attributable in pg_stat.
                application_name=str(options.get("application_name") or "hub-health"),
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(f"SET LOCAL statement_timeout = {POSTGRES_STATEMENT_TIMEOUT_MS}")
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return None

    return _timed(probe)


def check_redis() -> CheckResult:
    """``PING`` with a one-second socket timeout; not critical (docs/09 8.2)."""

    def probe() -> str | None:
        client = redis.Redis.from_url(
            settings.HUB_REDIS_URL,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
        )
        try:
            client.ping()
        finally:
            client.close()
        return None

    return _timed(probe)


register_readiness_check("postgres", check_postgres, critical=True)
register_readiness_check("redis", check_redis, critical=False)


def _document(*, status: Status, checks: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": status,
        "version": settings.HUB_VERSION,
        "time": get_container().clock.now().isoformat(),
    }
    if checks is not None:
        data["checks"] = list(checks)
    return {"data": data}


def _json_response(document: dict[str, Any]) -> HttpResponse:
    return HttpResponse(
        json.dumps(document, separators=(",", ":")).encode("utf-8"),
        content_type="application/json",
    )


def _problem(
    request: HttpRequest, exc: Exception, *, retry_after_seconds: int | None = None
) -> HttpResponse:
    status, body = to_problem(
        exc,
        instance=request.path,
        request_id=request_id_var.get() or "",
        accept_language=request.headers.get("Accept-Language"),
    )
    return problem_response(status, body, retry_after_seconds=retry_after_seconds)


def _unavailable(request: HttpRequest) -> HttpResponse:
    """503 with ``Retry-After``: the pool must retry elsewhere (docs/09 1.4)."""
    return _problem(
        request,
        ExternalUnavailable(code="service_unavailable", message_key="service_unavailable"),
        retry_after_seconds=RETRY_AFTER_SECONDS,
    )


def _get_only(view: Callable[[HttpRequest], HttpResponse]) -> Callable[[HttpRequest], HttpResponse]:
    """GET and HEAD (Django handles HEAD); any other method is a problem+json 405."""

    def wrapper(request: HttpRequest) -> HttpResponse:
        if request.method not in ("GET", "HEAD"):
            response = _problem(request, MethodNotAllowed())
            response["Allow"] = "GET, HEAD"
            return response
        return view(request)

    return wrapper


@_get_only
def liveness(request: HttpRequest) -> HttpResponse:
    """Process is up: no dependency is touched, so nothing can make this slow or lie."""
    return _json_response(_document(status="ok"))


@_get_only
def readiness(request: HttpRequest) -> HttpResponse:
    """Dependency and drain status; 503 removes this instance from the balancer."""
    if Path(settings.HUB_DRAIN_FILE).exists():
        return _unavailable(request)

    results: list[dict[str, Any]] = []
    critical_down = False
    degraded = False
    for check in readiness_checks():
        result = check.fn()
        entry: dict[str, Any] = {
            "name": check.name,
            "status": result.status,
            "latency_ms": result.latency_ms,
        }
        if result.detail:
            entry["detail"] = result.detail
        results.append(entry)
        if result.status != "ok":
            if check.critical:
                critical_down = True
            else:
                degraded = True

    if critical_down:
        return _unavailable(request)

    return _json_response(_document(status="degraded" if degraded else "ok", checks=results))


# Mounted under `api/v1/` by hub.shared.api.urls: the paths are exactly the contract's.
health_urlpatterns = [
    path("health/live", liveness, name="health-live"),
    path("health/ready", readiness, name="health-ready"),
]
