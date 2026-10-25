"""`GET /audit-log`: the administrator's read, and the fact that reading is itself audited.

`hub.audit_page` is the only read path the schema allows, and it checks `audit.read` in the
database; the permission class refuses every other role before the query runs. A successful
read appends its own `audit.read` row to the `admin` stream, in the same transaction, so an
auditor can tell who looked and found nothing.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import jsonschema
import pytest
import yaml
from django.db import connection
from django.test import Client

from hub.admin.api.views import AuditLogView
from hub.identity.contracts import Principal
from hub.identity.infrastructure.audit import HubAuditLog
from support.principals import SeededPrincipalAuthentication, SeededUser, seed_staff_user

REPO_ROOT = Path(__file__).resolve().parents[4]
OPENAPI = REPO_ROOT / "api" / "openapi.yaml"
URL = "/api/v1/audit-log"

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def api() -> Iterator[Client]:
    """A client whose caller comes from the test header (Task 01.3 supplies the real one).

    The authenticator is patched onto the view, not set through ``REST_FRAMEWORK``: DRF reads
    ``DEFAULT_AUTHENTICATION_CLASSES`` once, when ``APIView`` is imported, so a settings
    override would only take effect while the view module happened to be unimported — an
    order-dependent test that passes alone and fails in the suite.
    """
    with mock.patch.object(AuditLogView, "authentication_classes", [SeededPrincipalAuthentication]):
        yield Client()


@pytest.fixture
def administrator() -> SeededUser:
    return seed_staff_user("administrator")


def _record(actor: Principal, action: str, *, stream: str = "admin") -> uuid.UUID:
    return HubAuditLog().record(
        stream=stream,
        actor=actor,
        action=action,
        entity_type="approval",
        entity_id=uuid.uuid4(),
        site_id=None,
        details={"kind": "user_creation"},
    )


def _audit_entries(stream: str) -> list[tuple[str, uuid.UUID | None, dict[str, Any]]]:
    """``(action, actor_id, details)`` of one stream as stored, oldest first.

    ``details`` arrives as JSON text: Django's psycopg backend registers a text loader for
    jsonb so that its own JSONField can apply a custom decoder.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT action, actor_id, details FROM hub.audit_log WHERE stream = %s ORDER BY seq",
            [stream],
        )
        return [
            (action, actor_id, json.loads(details))
            for action, actor_id, details in cursor.fetchall()
        ]


def _audit_entry_schema() -> dict[str, Any]:
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schema: dict[str, Any] = document["components"]["schemas"]["AuditEntry"]
    return schema


@pytest.mark.tc("TC-FR-15-14")
def test_administrator_reads_page_and_read_is_audited(
    api: Client, administrator: SeededUser
) -> None:
    _record(administrator.principal, "approval.requested")
    _record(administrator.principal, "approval.granted")

    response = api.get(URL, {"stream": "admin"}, headers=administrator.headers())

    assert response.status_code == 200, response.content
    body = response.json()
    entries = body["data"]
    assert [entry["seq"] for entry in entries] == [1, 2]
    assert entries[0]["prev_hash"] == ""
    assert entries[1]["prev_hash"] == entries[0]["hash"]
    assert entries[0]["action"] == "approval.requested"
    assert body["meta"] == {"has_next": False, "next_cursor": None, "limit": 25}

    schema = _audit_entry_schema()
    for entry in entries:
        jsonschema.validate(instance=entry, schema=schema)

    # The read is a fact of its own, written after the page so it is not in it.
    stored = _audit_entries("admin")
    assert [action for action, _, _ in stored] == [
        "approval.requested",
        "approval.granted",
        "audit.read",
    ]
    _, actor_id, details = stored[-1]
    assert actor_id == administrator.user_id
    assert details["stream"] == "admin"
    assert details["returned"] == 2


@pytest.mark.tc("TC-FR-15-14")
def test_support_agent_gets_403(api: Client) -> None:
    agent = seed_staff_user("support_agent")

    response = api.get(URL, {"stream": "admin"}, headers=agent.headers())

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert _audit_entries("admin") == [], "a refused read writes nothing"


def test_cursor_walks_the_stream_without_gaps(api: Client, administrator: SeededUser) -> None:
    for action in ("approval.requested", "approval.granted", "user.create"):
        _record(administrator.principal, action)

    first = api.get(URL, {"stream": "admin", "limit": "2"}, headers=administrator.headers()).json()
    assert [entry["seq"] for entry in first["data"]] == [1, 2]
    assert first["meta"]["has_next"] is True

    second = api.get(
        URL,
        {"stream": "admin", "limit": "2", "cursor": first["meta"]["next_cursor"]},
        headers=administrator.headers(),
    ).json()
    # Reading `admin` appends `audit.read` to `admin`, so the stream grows as it is paged:
    # the third seeded event and the first request's own read row are this page.
    assert [entry["seq"] for entry in second["data"]] == [3, 4]
    assert [entry["action"] for entry in second["data"]] == ["user.create", "audit.read"]
    assert second["meta"]["has_next"] is False


def test_tampered_cursor_is_400(api: Client, administrator: SeededUser) -> None:
    cursor = base64.urlsafe_b64encode(json.dumps({"s": "not-a-number"}).encode()).decode()

    response = api.get(URL, {"stream": "admin", "cursor": cursor}, headers=administrator.headers())

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_cursor"


def test_missing_stream_is_400(api: Client, administrator: SeededUser) -> None:
    # `hub.audit_page` is per stream and the application role cannot list streams: an
    # unfiltered read has no read path, so it is refused instead of silently answering one
    # stream (docs/plan/slice-01, Plan notes).
    response = api.get(URL, headers=administrator.headers())

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"


def test_action_prefix_filter(api: Client, administrator: SeededUser) -> None:
    _record(administrator.principal, "approval.requested")
    _record(administrator.principal, "user.create")
    _record(administrator.principal, "approval.granted")

    response = api.get(
        URL, {"stream": "admin", "action": "approval.*"}, headers=administrator.headers()
    )

    assert [entry["action"] for entry in response.json()["data"]] == [
        "approval.requested",
        "approval.granted",
    ]
