"""Health endpoints: the readiness contract the balancer relies on (docs/09 1.4 and 4.8).

Readiness must never lie in either direction: a database the pooler cannot reach removes the
instance from service, while a Redis outage is reported as ``degraded`` and keeps serving
(docs/09 8.2). A draining instance answers 503 before any check runs, so a rolling deploy
takes the replica out of the balancer instead of failing customer requests.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import mock

import jsonschema
import pytest
import yaml
from django.conf import settings
from django.test import Client, override_settings
from django.urls import URLPattern, URLResolver, get_resolver

REPO_ROOT = Path(__file__).resolve().parents[4]
OPENAPI = REPO_ROOT / "api" / "openapi.yaml"

URLCONFS = ["hub.urls.api", "hub.urls.webhooks", "hub.urls.telemetry", "hub.urls.stream"]

HEALTH_PATHS = {"/api/v1/health/live", "/api/v1/health/ready"}

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture(params=URLCONFS)
def urlconf(request: pytest.FixtureRequest) -> Iterator[str]:
    """Every Django deployable serves the same two health paths (D-04)."""
    value: str = request.param
    with override_settings(ROOT_URLCONF=value):
        yield value


@pytest.fixture
def dependencies(redis_url: str) -> Iterator[None]:
    """A reachable Postgres (the session container) and a reachable Redis."""
    with override_settings(HUB_REDIS_URL=redis_url):
        yield


def _closed_port() -> int:
    """Bind and immediately release a local port: connecting to it is refused."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _health_schema() -> dict[str, Any]:
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schema: dict[str, Any] = document["components"]["schemas"]["Health"]
    return schema


def _flatten(patterns: list[URLPattern | URLResolver], prefix: str = "") -> set[str]:
    """Every leaf route of a URLConf, as an absolute path without regex noise."""
    found: set[str] = set()
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            found |= _flatten(pattern.url_patterns, route)
        else:
            found.add("/" + route.lstrip("^").rstrip("$"))
    return found


def test_live_returns_ok_without_dependencies(urlconf: str, django_assert_num_queries: Any) -> None:
    with django_assert_num_queries(0):
        response = Client().get("/api/v1/health/live")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["version"] == settings.HUB_VERSION
    datetime.fromisoformat(body["data"]["time"])
    assert response["X-Request-Id"]


def test_ready_200_all_ok(dependencies: None) -> None:
    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "ok"
    checks = {check["name"]: check for check in body["checks"]}
    assert checks["postgres"]["status"] == "ok"
    assert checks["redis"]["status"] == "ok"
    assert all(isinstance(check["latency_ms"], int) for check in body["checks"])


def test_ready_503_when_postgres_down() -> None:
    # Only the alias's HOST/PORT change: the check reads the alias at call time, and
    # `override_settings(DATABASES=...)` would close the test's own connection.
    with mock.patch.dict(
        settings.DATABASES["default"],
        {"HOST": "127.0.0.1", "PORT": str(_closed_port())},
    ):
        response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response["Content-Type"] == "application/problem+json"
    assert response["Retry-After"] == "5"
    body = response.json()
    assert body["code"] == "service_unavailable"
    assert body["status"] == 503
    assert "password" not in json.dumps(body).lower()


def test_ready_200_degraded_when_redis_down() -> None:
    with override_settings(HUB_REDIS_URL=f"redis://127.0.0.1:{_closed_port()}/0"):
        response = Client().get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "degraded"
    checks = {check["name"]: check for check in body["checks"]}
    assert checks["postgres"]["status"] == "ok"
    assert checks["redis"]["status"] == "down"


def test_ready_503_when_draining_file_exists(tmp_path: Path, dependencies: None) -> None:
    drain_file = tmp_path / "draining"
    drain_file.write_text("", encoding="utf-8")

    with override_settings(HUB_DRAIN_FILE=str(drain_file)):
        response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert response.json()["code"] == "service_unavailable"


def test_response_validates_against_openapi_health_schema(dependencies: None) -> None:
    schema = _health_schema()

    live = Client().get("/api/v1/health/live").json()["data"]
    ready = Client().get("/api/v1/health/ready").json()["data"]

    jsonschema.validate(instance=live, schema=schema)
    jsonschema.validate(instance=ready, schema=schema)


def test_other_methods_are_refused_as_problem(urlconf: str) -> None:
    response = Client().post("/api/v1/health/live")

    assert response.status_code == 405
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["code"] == "method_not_allowed"


@pytest.mark.tc("TC-NFR-06-7")
def test_only_health_routes_exist(urlconf: str) -> None:
    routes = _flatten(get_resolver().url_patterns)

    assert routes == HEALTH_PATHS
