"""Task T1.5: the double's documents against its schemas, and its schema against the API's.

Two checks, and they catch different mistakes. The first validates *every* document the double
produces — requests it accepts, responses it answers, envelopes it sends, statement rows it writes —
against `schemas/`, which is the contract of record for everything the frozen OpenAPI does not
describe (docs/10 section 6.4: a test double is only useful if it is checked against a stated
interface). The second runs `tools/scripts/sync_stub_schemas.py --check`, so the one schema that
*is* in the frozen contract cannot drift from it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from provider_stub.config import Config
from provider_stub.deliver import Deliverer
from provider_stub.server import Router
from provider_stub.state import Store
from provider_stub.statements import COLUMNS, synthetic_rows

from conftest import FakeClock, FakeHub

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
TOP_UP = "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0003"
PAYER = "+243810000111"

REQUIRED_SCHEMAS = (
    "collection_request.schema.json",
    "collection_ack.schema.json",
    "collection_status.schema.json",
    "statement_row.schema.json",
    "webhook_envelope.schema.json",
)


def schema_for(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def test_every_schema_is_committed_and_valid() -> None:
    """A missing or malformed schema is a contract nobody is checking."""
    for name in REQUIRED_SCHEMAS:
        document = schema_for(name)
        jsonschema.Draft202012Validator.check_schema(document)
        assert document.get("title")


def test_all_documents_validate_against_schemas(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """Every document the double produces, validated against the schema that describes it."""
    request = {
        "reference": TOP_UP,
        "amount": {"amount_minor": 500, "currency": "USD"},
        "payer_msisdn": PAYER,
    }
    jsonschema.validate(request, schema_for("collection_request.schema.json"))

    response = router.handle(
        "POST", "/providers/mpesa_cd/collections", {}, json.dumps(request).encode()
    )
    ack = json.loads(response.body)
    jsonschema.validate(ack, schema_for("collection_ack.schema.json"))

    status = router.handle(
        "GET", f"/providers/mpesa_cd/collections/{ack['provider_reference']}", {}, b""
    )
    jsonschema.validate(json.loads(status.body), schema_for("collection_status.schema.json"))

    deliverer.complete("mpesa_cd", ack["provider_reference"], outcome="successful")
    deliverer.run_due(now=clock.now)
    envelopes = [json.loads(request.body) for request in hub.requests]
    assert envelopes
    for envelope in envelopes:
        jsonschema.validate(envelope, schema_for("webhook_envelope.schema.json"))

    row = dict(zip(COLUMNS, synthetic_rows(1)[0].cells(), strict=True))
    jsonschema.validate(row, schema_for("statement_row.schema.json"))


def test_every_response_status_the_double_answers_has_a_document(router: Router) -> None:
    """An error is a document too: the hub's adapter logs the code it was told."""
    token = {"Authorization": "Bearer control-test-token"}
    for status, path, method, headers in (
        # An unknown channel, before anything else: no state is created and no other channel's
        # secret is used to answer it.
        (404, "/providers/not_a_channel/collections", "POST", {}),
        # A control route without the token is 401 before the route is even consulted.
        (401, "/control/deliveries", "GET", {}),
        # With the token, an unknown control path is a 404 with a body rather than Django's HTML.
        (404, "/control/nothing-here", "GET", token),
        # A route that exists under another method is 405, not 404.
        (405, "/providers/mpesa_cd/collections", "GET", {}),
        (405, "/providers/mpesa_cd/statements", "POST", {}),
        # And a path under a known channel that is not a route at all.
        (404, "/providers/mpesa_cd/nothing", "GET", {}),
    ):
        response = router.handle(method, path, headers, b"")
        assert response.status == status, (path, method)
        body = json.loads(response.body)
        assert body["error"]["code"], (path, method)


def test_envelope_schema_equals_openapi() -> None:
    """The committed copy of `WebhookEnvelope` is the contract's, byte for byte."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "scripts" / "sync_stub_schemas.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "matches api/openapi.yaml" in result.stdout


def test_the_double_does_not_import_the_hub() -> None:
    """The double is the provider: sharing code with the hub would let it agree about a mistake."""
    package = Path(__file__).resolve().parents[1] / "src" / "provider_stub"
    forbidden = ("hub.", "hub ", "django", "hubdb", "psycopg", "drf_")

    for source in sorted(package.glob("*.py")):
        imports = [
            line.strip()
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from "))
            and not line.startswith("from __future__")
            and not line.startswith("from provider_stub")
        ]
        offending = [
            line for line in imports if any(name in line for name in forbidden)
        ]
        assert offending == [], (source.name, offending)

    # And the runtime dependency list is `httpx` only, as the slice document says.
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text("utf-8")
    assert 'dependencies = ["httpx>=0.28"]' in pyproject
