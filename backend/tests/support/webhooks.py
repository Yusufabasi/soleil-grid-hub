"""Helpers for the webhook and top-up suites: deliveries, seeded rows and the assertions' reads.

They live in ``tests/support`` because five integration modules need them, and a relative import
of another test module's helpers would make one test file the API of the others. Everything here
goes through the real stack — the route, the signed bytes, PostgreSQL — because the properties
these tests pin are properties of the database and of the signature, not of a fake.

The one thing that is not real is the provider's network: `payments_wiring.gateway()` returns the
in-memory adapter unless a base URL is configured, and that adapter verifies with the production
algorithm.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from django.db import connection
from django.test import Client, override_settings

from hub.identity import wiring as identity_wiring
from hub.ledger.contracts import AccountRef
from hub.ledger.wiring import service as ledger_service
from hub.payments import wiring as payments_wiring
from hub.payments.infrastructure.fake_provider import DEFAULT_FAKE_SECRET, FakeProviderGateway
from support.payments import NOW, PAYER, World, customer_token

__all__ = [
    "PROVIDER",
    "TIMESTAMP",
    "WEBHOOK",
    "deliver",
    "envelope",
    "event_rows",
    "gateway",
    "make_top_up",
    "outbox_rows",
    "redis_hint",
    "seed_top_up",
    "top_up_row",
    "transactions_for",
    "wallet_balance",
    "webhook_pool",
    "wired_gateway",
]

PROVIDER = "mpesa_cd"
WEBHOOK = f"/api/v1/webhooks/providers/{PROVIDER}"
TIMESTAMP = str(int(NOW.timestamp()))


def webhook_pool() -> Any:
    """The URLConf the deployed webhooks pool serves; the route is mounted only there."""
    return override_settings(ROOT_URLCONF="hub.urls.webhooks")


def gateway() -> FakeProviderGateway:
    """A signer with the configured secret, for building the bytes a provider would send."""
    return FakeProviderGateway(secrets=(DEFAULT_FAKE_SECRET,))


def wired_gateway(provider: str = PROVIDER) -> FakeProviderGateway:
    """The adapter the application is *using* for ``provider``, to script its answers.

    A test that asserts the provider was asked once, or that teaches its status API what it
    says about a charge, has to reach the same instance the use case holds. It is asserted to
    be the in-memory adapter rather than cast: a test that silently scripted an HTTP adapter
    would be a test of nothing.
    """
    adapter = payments_wiring.gateway(provider)
    assert isinstance(adapter, FakeProviderGateway), type(adapter)
    return adapter


def envelope(
    event_id: str,
    *,
    top_up_id: uuid.UUID | None = None,
    event_type: str = "payment.confirmed",
    amount_minor: int = 500,
    currency: str = "USD",
    payer: str | None = PAYER,
    provider_reference: str | None = None,
    failure_code: str | None = None,
) -> dict[str, Any]:
    """One ``WebhookEnvelope``; the reference defaults to the event id, so each is its own charge."""
    data: dict[str, Any] = {
        "provider_reference": provider_reference or f"MP-{event_id}",
        "external_reference": str(top_up_id or uuid.uuid4()),
        "amount": {"amount_minor": amount_minor, "currency": currency},
    }
    if payer is not None:
        data["payer_msisdn"] = payer
    if failure_code is not None:
        data["failure_code"] = failure_code
    return {
        "event_id": event_id,
        "type": event_type,
        "occurred_at": "2026-10-27T08:59:00Z",
        "data": data,
    }


def deliver(
    client: Client,
    document: dict[str, Any],
    *,
    secret: str | None = None,
    timestamp: str = TIMESTAMP,
) -> Any:
    """Sign ``document`` and POST it to the webhook route."""
    headers, body = gateway().signed(document, timestamp=timestamp, secret=secret)
    return client.post(WEBHOOK, data=body, content_type="application/json", headers=headers)


def make_top_up(client: Client, world: World, *, amount_minor: int = 500) -> uuid.UUID:
    """Initiate through the API, so the row is `pending` the way a real delivery finds it."""
    with override_settings(ROOT_URLCONF="hub.urls.api"):
        created = client.post(
            "/api/v1/top-ups",
            {
                "meter_id": str(world.meter_id),
                "amount": {"amount_minor": amount_minor, "currency": "USD"},
                "provider": PROVIDER,
                "payer_msisdn": PAYER,
            },
            content_type="application/json",
            headers={
                "Authorization": f"Bearer {customer_token(world.customer_id)}",
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
    assert created.status_code == 202, created.json()
    return uuid.UUID(created.json()["data"]["id"])


def seed_top_up(
    world: World,
    *,
    status: str = "initiated",
    amount_minor: int = 500,
    payer: str = PAYER,
    expires_at: Any = None,
    initiated_at: Any = None,
    updated_at: Any = None,
    top_up_id: uuid.UUID | None = None,
    provider: str = PROVIDER,
) -> uuid.UUID:
    """A top-up in the status a test needs, plus the wallet a posting would name.

    The wallet is created here because initiation creates it in the transaction that inserts the
    row: a hand-seeded top-up without one is a row no environment has, and the credit would fail
    on a missing account rather than on the property under test.
    """
    chosen = top_up_id or uuid.uuid4()
    tariff = 25_000  # `support.payments.TARIFF_MINOR_PER_KWH`
    with identity_wiring.unit_of_work(None) as uow:
        ledger_service().ensure_wallet(world.customer_id, "USD", uow=uow)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, provider_ref,"
            " payer_msisdn, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh,"
            " energy_wh, status, expires_at, initiated_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 'USD', %s, %s, %s, %s, %s, %s, %s)",
            [
                chosen,
                world.customer_id,
                world.meter_id,
                provider,
                str(chosen),
                payer,
                amount_minor,
                world.tariff_id,
                tariff,
                amount_minor * 1000 // tariff,
                status,
                expires_at or (NOW + timedelta(minutes=30)),
                initiated_at or NOW,
                updated_at or initiated_at or NOW,
            ],
        )
    return chosen


def top_up_row(top_up_id: uuid.UUID) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, late, provider_txn_id, payer_matched, provider_event_id,"
            " ledger_transaction_id, reversal_transaction_id, confirmed_at, status_pull_at"
            " FROM hub.top_ups WHERE id = %s",
            [top_up_id],
        )
        row = cursor.fetchone()
    assert row is not None
    return {
        "status": row[0],
        "late": row[1],
        "provider_txn_id": row[2],
        "payer_matched": row[3],
        "provider_event_id": row[4],
        "ledger_transaction_id": row[5],
        "reversal_transaction_id": row[6],
        "confirmed_at": row[7],
        "status_pull_at": row[8],
    }


def event_rows() -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, event_id, provider_txn_id, event_type, status, last_error"
            " FROM hub.provider_events ORDER BY received_at"
        )
        return [
            {
                "id": row[0],
                "event_id": row[1],
                "provider_txn_id": row[2],
                "event_type": row[3],
                "status": row[4],
                "last_error": row[5],
            }
            for row in cursor.fetchall()
        ]


def outbox_rows(event_type: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT event_type, aggregate_id, payload FROM hub.outbox"
    params: list[Any] = []
    if event_type is not None:
        query += " WHERE event_type = %s"
        params.append(event_type)
    with connection.cursor() as cursor:
        cursor.execute(query + " ORDER BY created_at", params)
        rows = cursor.fetchall()
    # Django's psycopg backend loads jsonb as text, so the payload is decoded here.
    return [
        {
            "event_type": row[0],
            "aggregate_id": row[1],
            "payload": json.loads(row[2]) if isinstance(row[2], str) else row[2],
        }
        for row in rows
    ]


def wallet_balance(customer_id: uuid.UUID) -> int:
    reference = AccountRef(kind="customer_wallet", currency="USD", customer_id=customer_id)
    return ledger_service().balance(reference).amount_minor


def transactions_for(top_up_id: uuid.UUID) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT posting_key FROM hub.ledger_transactions WHERE source_top_up_id = %s"
            " ORDER BY created_at, posting_key",
            [top_up_id],
        )
        return [row[0] for row in cursor.fetchall()]


def redis_hint(event_id: str) -> str | None:
    """The hint of a delivery, read from the same Redis the application uses."""
    return identity_wiring.store().get(f"provider_event:{PROVIDER}:{event_id}")
