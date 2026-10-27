"""The provider webhook's refusals (Task 04.4, FR-03, TC-FR-03-5).

What matters here is that a rejected delivery leaves *no* event behind and a log row that holds
a hash rather than a body, and that the three refusals are distinguishable: a bad signature
(401), a stale timestamp (400), a signed body that is not an envelope (422), and a header that
names another event than the body (400).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import pytest
from django.db import connection
from django.test import Client, override_settings
from prometheus_client import REGISTRY

from hub.payments.infrastructure.fake_provider import (
    DEFAULT_FAKE_SECRET,
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    FakeProviderGateway,
)
from support.payments import NOW, PAYER, World, customer_token

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

WEBHOOK = "/api/v1/webhooks/providers/synthetic"
TIMESTAMP = str(int(NOW.timestamp()))


@pytest.fixture(autouse=True)
def _webhook_pool() -> Any:
    """Serve the route from the webhooks URLConf, as the deployed pool does."""
    with override_settings(ROOT_URLCONF="hub.urls.webhooks"):
        yield


def gateway() -> FakeProviderGateway:
    return FakeProviderGateway(secrets=(DEFAULT_FAKE_SECRET,))


def envelope(
    event_id: str = "evt_1",
    *,
    event_type: str = "payment.confirmed",
    external_reference: uuid.UUID | None = None,
    amount_minor: int = 500,
    currency: str = "USD",
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "type": event_type,
        "occurred_at": "2026-10-27T08:59:00Z",
        "data": {
            "provider_reference": f"MP-{event_id}",
            "external_reference": str(external_reference or uuid.uuid4()),
            "amount": {"amount_minor": amount_minor, "currency": currency},
            "payer_msisdn": PAYER,
        },
    }


def deliver(
    client: Client,
    document: dict[str, Any],
    *,
    secret: str | None = None,
    timestamp: str = TIMESTAMP,
    provider: str = "synthetic",
) -> Any:
    """Sign ``document`` and post it, exactly as a provider would."""
    headers, body = gateway().signed(document, timestamp=timestamp, secret=secret)
    return client.post(
        f"/api/v1/webhooks/providers/{provider}",
        data=body,
        content_type="application/json",
        headers=headers,
    )


def event_rows() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM hub.provider_events")
        return int(cursor.fetchone()[0])


def quarantine_rows() -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT provider, reason, body_sha256, body_length, signature_hdr"
            " FROM hub.webhook_quarantine ORDER BY received_at"
        )
        return list(cursor.fetchall())


def test_bad_signature_is_401_and_quarantined_not_stored(client: Client, world: World) -> None:
    document = envelope("evt_forged")

    response = deliver(client, document, secret="not-the-secret")

    assert response.status_code == 401, response.json()
    assert response.json()["code"] == "invalid_signature"
    assert event_rows() == 0

    rows = quarantine_rows()
    assert len(rows) == 1
    provider, reason, digest, length, signature = rows[0]
    assert (provider, reason) == ("synthetic", "bad_signature")
    # A hash and a length, never the body: the sender is untrusted, so its payload is not
    # worth storing (and could be somebody's personal data).
    body = json.dumps(document).encode()
    assert digest == hashlib.sha256(body).hexdigest()
    assert length == len(body)
    assert signature is not None and signature.startswith("v1=")
    del world


def test_stale_timestamp_is_400(client: Client, world: World) -> None:
    old = str(int(NOW.timestamp()) - 301)

    response = deliver(client, envelope("evt_stale"), timestamp=old)

    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "stale_timestamp"
    assert event_rows() == 0
    assert [row[1] for row in quarantine_rows()] == ["stale_timestamp"]
    del world


def test_event_id_header_mismatch_is_400(client: Client, world: World) -> None:
    """The signed id and the body's id disagree: nothing about which event arrived is trusted."""
    document = envelope("evt_body")
    body = json.dumps(document).encode()
    headers = gateway().sign(body, timestamp=TIMESTAMP, event_id="evt_header")

    response = client.post(WEBHOOK, data=body, content_type="application/json", headers=headers)

    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "webhook_id_mismatch"
    assert event_rows() == 0
    del world


def test_unknown_provider_is_404(client: Client) -> None:
    response = client.post(
        "/api/v1/webhooks/providers/nobody", data=b"{}", content_type="application/json"
    )

    assert response.status_code == 404, response.json()
    assert response.json()["code"] == "not_found"


def test_oversized_body_is_rejected_before_reading(client: Client) -> None:
    oversized = b"x" * 65_537

    response = client.post(
        WEBHOOK,
        data=oversized,
        content_type="application/json",
        headers={
            TIMESTAMP_HEADER: TIMESTAMP,
            EVENT_ID_HEADER: "evt_big",
            SIGNATURE_HEADER: "v1=00",
        },
    )

    assert response.status_code == 413, response.json()
    assert response.json()["code"] == "payload_too_large"
    assert quarantine_rows() == []


def test_malformed_json_with_valid_signature_is_422(client: Client) -> None:
    body = b'{"event_id": "evt_broken", "type": '
    headers = gateway().sign(body, timestamp=TIMESTAMP, event_id="evt_broken")

    response = client.post(WEBHOOK, data=body, content_type="application/json", headers=headers)

    assert response.status_code == 422, response.json()
    assert response.json()["code"] == "malformed_webhook"
    assert event_rows() == 0
    assert [row[1] for row in quarantine_rows()] == ["malformed"]


def test_invalid_signature_does_not_squat_event_id(client: Client, world: World) -> None:
    """A forgery with a real event id must not stop the genuine delivery that follows."""
    document = envelope("evt_9")

    forged = deliver(client, document, secret="wrong-secret")
    assert forged.status_code == 401
    assert event_rows() == 0

    genuine = deliver(client, document)
    assert genuine.status_code == 200, genuine.json()
    assert genuine.json()["data"] == {"status": "accepted", "event_id": "evt_9"}
    assert event_rows() == 1

    # A second genuine delivery is a duplicate, not a second effect.
    again = deliver(client, document)
    assert again.status_code == 200
    assert again.json()["data"]["status"] == "duplicate"
    assert event_rows() == 1
    del world


def test_failure_counter_increments(client: Client) -> None:
    labels = {"provider": "synthetic", "reason": "bad_signature"}
    before = REGISTRY.get_sample_value("webhook_quarantine_total", labels) or 0.0

    deliver(client, envelope("evt_counter"), secret="wrong-secret")

    after = REGISTRY.get_sample_value("webhook_quarantine_total", labels) or 0.0
    assert after == before + 1


def test_accepted_delivery_for_a_known_top_up_is_not_silently_credited(
    client: Client, world: World
) -> None:
    """The credit path is Task 04.5: until it lands, a confirmed payment answers 503 and
    commits nothing, so the provider retries instead of being told the money arrived."""
    with override_settings(ROOT_URLCONF="hub.urls.api"):
        created = client.post(
            "/api/v1/top-ups",
            {
                "meter_id": str(world.meter_id),
                "amount": {"amount_minor": 500, "currency": "USD"},
                "provider": "mpesa_cd",
                "payer_msisdn": PAYER,
            },
            content_type="application/json",
            headers={
                "Authorization": f"Bearer {customer_token(world.customer_id)}",
                "Idempotency-Key": "webhook-known",
            },
        )
    assert created.status_code == 202, created.json()
    top_up_id = created.json()["data"]["id"]

    # The delivery arrives on the route of the provider the top-up was collected on; a
    # webhook on another provider's route can only ever name an unknown reference.
    response = deliver(
        client,
        envelope("evt_known", external_reference=uuid.UUID(top_up_id)),
        provider="mpesa_cd",
    )

    assert response.status_code == 503, response.json()
    assert response["Retry-After"] == "30"
    # Nothing committed: the event row rolled back with the transaction.
    assert event_rows() == 0
