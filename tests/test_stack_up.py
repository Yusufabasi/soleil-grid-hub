"""The local compose stack answering through the balancer (slice 00).

These tests need the stack that ``make up`` starts, so they are marked ``integration`` and
run only by ``make verify`` (Task 00.12 extends this file with the request-id, traceparent,
stats and log-hygiene checks).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

BALANCER = "http://localhost:8080"


def get(path: str) -> tuple[int, dict[str, str], bytes]:
    """GET one path through the balancer; returns status, headers and raw body."""
    request = urllib.request.Request(BALANCER + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:  # a 503 is an answer, not a transport failure
        return error.code, dict(error.headers), error.read()


@pytest.mark.integration
@pytest.mark.tc("TC-NFR-14-1")
def test_health_through_the_balancer_for_the_api_and_web_pools():
    status, _, body = get("/api/v1/health/live")
    assert status == 200, body
    assert json.loads(body)["data"]["status"] == "ok"

    status, _, body = get("/api/v1/health/ready")
    assert status == 200, body
    document = json.loads(body)["data"]
    assert document["status"] in {"ok", "degraded"}
    assert "postgres" in {check["name"] for check in document["checks"]}

    status, _, body = get("/")
    assert status == 200, body
    assert b"hub-web stub" in body
