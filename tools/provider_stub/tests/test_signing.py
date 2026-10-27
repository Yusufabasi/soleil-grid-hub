"""Task T1.2 and review focus 1: the signature covers the bytes that were actually sent.

The contract vector pins the *shape* (`timestamp.event_id.body`, `v1=<hex>`). The other test is the
one that catches the failure this review exists for: a double that serialises, signs, and then lets
a client re-serialise the body would produce a signature that verifies against a document nobody
sent. So the assertion is made over the bytes a recording hub received, not over a document built
again in the test.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path

import jsonschema
import pytest

from provider_stub.config import Config
from provider_stub.deliver import Deliverer, build_event
from provider_stub.server import Router
from provider_stub.signing import build_headers, sign, signed_string, verify
from provider_stub.state import Collection

from conftest import PROVIDER_SECRETS, FakeClock, FakeHub

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"


def collection() -> Collection:
    return Collection(
        provider="mpesa_cd",
        reference="0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0003",
        provider_reference="MP0123456789",
        amount_minor=500,
        currency="USD",
        payer_msisdn="+243810000111",
        status="successful",
        updated_at="2026-10-27T09:00:00Z",
    )


def test_sign_matches_contract_vector() -> None:
    """The vector from the slice document: secret `k`, `1700000000.evt_1.{"a":1}`."""
    body = b'{"a":1}'
    expected = hmac.new(b"k", b'1700000000.evt_1.{"a":1}', hashlib.sha256).hexdigest()

    assert signed_string(1700000000, "evt_1", body) == b'1700000000.evt_1.{"a":1}'
    assert sign(b"k", 1700000000, "evt_1", body) == f"v1={expected}"
    # And the double's own verifier agrees, which is what a hub test relies on.
    assert verify(b"k", f"v1={expected}", 1700000000, "evt_1", body)
    assert not verify(b"k", f"v1={expected}", 1700000000, "evt_1", body + b" ")


def test_signature_covers_exact_sent_bytes(
    config: Config, store, deliverer: Deliverer, clock: FakeClock, hub: FakeHub
) -> None:
    """The recorded bytes verify against the recorded signature: nothing was re-serialised."""
    created = store.add_collection(
        provider="mpesa_cd",
        reference="0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0003",
        amount_minor=500,
        currency="USD",
        payer_msisdn="+243810000111",
    )
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")
    deliverer.run_due(now=clock.now)

    assert len(hub.requests) == 1
    sent = hub.requests[0]
    assert sent.signature_is_valid(PROVIDER_SECRETS["mpesa_cd"])
    # A body the test builds itself must NOT verify: the signature is over the sent bytes, and
    # a compact re-serialisation that happened to match would hide exactly this bug.
    rebuilt = json.dumps(sent.document(), separators=(",", ":"), sort_keys=True).encode()
    assert rebuilt == sent.body
    reordered = json.dumps(sent.document(), indent=2).encode()
    assert not verify(
        PROVIDER_SECRETS["mpesa_cd"].encode(),
        sent.headers["X-Webhook-Signature"],
        sent.headers["X-Webhook-Timestamp"],
        sent.headers["X-Webhook-Id"],
        reordered,
    )


def test_headers_complete(config: Config) -> None:
    """The three headers the hub reads, with the id equal to the body's event id."""
    body, headers = build_event(
        collection(),
        "successful",
        secret=b"secret",
        event_id="evt_0123456789abcdef",
        timestamp=1_700_000_000,
    )
    document = json.loads(body)

    assert set(headers) == {"X-Webhook-Id", "X-Webhook-Timestamp", "X-Webhook-Signature"}
    assert headers["X-Webhook-Id"] == document["event_id"] == "evt_0123456789abcdef"
    assert int(headers["X-Webhook-Timestamp"]) == 1_700_000_000
    assert re.fullmatch(r"v1=[0-9a-f]{64}", headers["X-Webhook-Signature"])
    assert document["occurred_at"] == "2023-11-14T22:13:20Z"


def test_timestamp_is_the_send_time(config: Config, clock: FakeClock, hub: FakeHub, store, deliverer: Deliverer) -> None:
    """A delivery signed at send time: within two seconds of the clock, never of the schedule."""
    created = store.add_collection(
        provider="mpesa_cd",
        reference="0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0003",
        amount_minor=500,
        currency="USD",
        payer_msisdn="+243810000111",
    )
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")
    clock.advance(120)
    deliverer.run_due(now=clock.now)

    sent_at = int(hub.requests[0].headers["X-Webhook-Timestamp"])
    assert abs(sent_at - int(clock.now)) <= 2


def test_envelope_validates_against_schema(config: Config) -> None:
    """The body the double builds is the document the API contract describes."""
    schema = json.loads((SCHEMAS / "webhook_envelope.schema.json").read_text())
    body, _ = build_event(
        collection(),
        "successful",
        secret=b"secret",
        event_id="evt_0123456789abcdef",
        timestamp=1_700_000_000,
    )

    jsonschema.validate(json.loads(body), schema)


def test_a_failed_envelope_validates_and_carries_its_code(config: Config) -> None:
    """`payment.failed` is the same document with a failure code, not a different one."""
    schema = json.loads((SCHEMAS / "webhook_envelope.schema.json").read_text())
    body, _ = build_event(
        collection(),
        "failed",
        secret=b"secret",
        event_id="evt_0123456789abcdef",
        timestamp=1_700_000_000,
    )
    document = json.loads(body)

    jsonschema.validate(document, schema)
    assert document["type"] == "payment.failed"
    assert document["data"]["failure_code"] == "declined"


def test_build_headers_signs_the_bytes_it_is_given() -> None:
    """`build_headers` is the low-level seam: it must not reformat what it signs."""
    body = b'{"z":1,"a":2}'

    headers = build_headers(secret=b"s", timestamp=1, event_id="e", body=body)

    assert verify(b"s", headers["X-Webhook-Signature"], 1, "e", body)


def test_router_is_not_needed_for_signing() -> None:
    """`signing` imports nothing from the HTTP layer: it is the part a hub test doubles."""
    import provider_stub.signing as signing  # noqa: PLC0415 - the import is the assertion

    assert not hasattr(signing, "Router")
    with pytest.raises(ImportError):
        exec("from provider_stub.signing import Router", {})  # noqa: S102 - a deliberate probe
