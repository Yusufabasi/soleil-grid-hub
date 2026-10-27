"""`ConfirmPayment`: one delivery, recorded once, credited once (Task 04.5, FR-03, NFR-05).

These tests go through the real route and a real PostgreSQL, because the properties they pin
are properties of the *database*: the unique index decides who won, the ledger function refuses
a credit without stored provider evidence, and the top-up guard refuses a status that skips a
step. A fake would let all three pass while the deployed system did none of them.

The one thing that is not real is the provider: `FakeProviderGateway` signs with the same
algorithm the HTTP adapter uses, so a delivery here is verified exactly as a production one is.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.test import Client, override_settings

from hub.identity import wiring as identity_wiring
from hub.ledger.contracts import AccountRef
from hub.ledger.wiring import service as ledger_service
from hub.payments import wiring as payments_wiring
from hub.payments.domain.errors import CreditPathUnavailable
from hub.payments.infrastructure.fake_provider import (
    DEFAULT_FAKE_SECRET,
    FakeProviderGateway,
)
from support.payments import NOW, PAYER, World, customer_token

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

PROVIDER = "mpesa_cd"
WEBHOOK = f"/api/v1/webhooks/providers/{PROVIDER}"
TIMESTAMP = str(int(NOW.timestamp()))


@pytest.fixture(autouse=True)
def _webhook_pool() -> Any:
    """Serve the route from the webhooks URLConf, as the deployed pool does."""
    with override_settings(ROOT_URLCONF="hub.urls.webhooks"):
        yield


# --- helpers --------------------------------------------------------------------------------


def gateway() -> FakeProviderGateway:
    return FakeProviderGateway(secrets=(DEFAULT_FAKE_SECRET,))


def envelope(
    event_id: str,
    *,
    top_up_id: uuid.UUID | None = None,
    event_type: str = "payment.confirmed",
    amount_minor: int = 500,
    payer: str = PAYER,
    failure_code: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "provider_reference": f"MP-{event_id}",
        "external_reference": str(top_up_id or uuid.uuid4()),
        "amount": {"amount_minor": amount_minor, "currency": "USD"},
        "payer_msisdn": payer,
    }
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


def seed_initiated_top_up(world: World, *, amount_minor: int = 500) -> uuid.UUID:
    """A top-up whose initiation has not been answered yet: the row a fast delivery finds."""
    top_up_id = uuid.uuid4()
    tariff = 25_000  # `support.payments.TARIFF_MINOR_PER_KWH`
    # The wallet the posting needs: initiation creates it in the transaction that inserts the
    # row, so a hand-seeded top-up has to create it too or it is a row no environment has.
    with identity_wiring.unit_of_work(None) as uow:
        ledger_service().ensure_wallet(world.customer_id, "USD", uow=uow)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, provider_ref,"
            " payer_msisdn, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh,"
            " energy_wh, status, expires_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 'USD', %s, %s, %s, 'initiated', %s)",
            [
                top_up_id,
                world.customer_id,
                world.meter_id,
                PROVIDER,
                str(top_up_id),
                PAYER,
                amount_minor,
                world.tariff_id,
                tariff,
                amount_minor * 1000 // tariff,
                NOW + timedelta(minutes=30),
            ],
        )
    return top_up_id


def top_up_row(top_up_id: uuid.UUID) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, late, provider_txn_id, payer_matched, provider_event_id,"
            " ledger_transaction_id, confirmed_at FROM hub.top_ups WHERE id = %s",
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
        "confirmed_at": row[6],
    }


def event_rows() -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT event_id, provider_txn_id, event_type, status, last_error"
            " FROM hub.provider_events ORDER BY received_at"
        )
        return [
            {
                "event_id": row[0],
                "provider_txn_id": row[1],
                "event_type": row[2],
                "status": row[3],
                "last_error": row[4],
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
        # Django's psycopg backend loads jsonb as text, so the payload is decoded here.
        return [
            {"event_type": row[0], "aggregate_id": row[1], "payload": json.loads(row[2])}
            if isinstance(row[2], str)
            else {"event_type": row[0], "aggregate_id": row[1], "payload": row[2]}
            for row in cursor.fetchall()
        ]


def wallet_balance(customer_id: uuid.UUID) -> int:
    reference = AccountRef(kind="customer_wallet", currency="USD", customer_id=customer_id)
    return ledger_service().balance(reference).amount_minor


def transactions_for(top_up_id: uuid.UUID) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT posting_key FROM hub.ledger_transactions WHERE source_top_up_id = %s",
            [top_up_id],
        )
        return [row[0] for row in cursor.fetchall()]


def redis_hint(event_id: str) -> str | None:
    """The hint of a delivery, read from the same Redis the application uses."""
    return identity_wiring.store().get(f"provider_event:{PROVIDER}:{event_id}")


# --- the credit -----------------------------------------------------------------------------


def test_confirmed_credits_wallet_once(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    balance_before = wallet_balance(world.customer_id)

    response = deliver(client, envelope("evt_1", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    assert response.json()["data"] == {"status": "accepted", "event_id": "evt_1"}

    row = top_up_row(top_up_id)
    assert row["status"] == "confirmed"
    assert row["late"] is False
    assert row["ledger_transaction_id"] is not None
    assert row["confirmed_at"] == NOW
    assert row["provider_txn_id"] == "MP-evt_1"
    assert row["payer_matched"] is True

    # One transaction, keyed as the contract says, and the wallet moved by exactly the amount.
    keys = transactions_for(top_up_id)
    assert keys == [f"topup:{top_up_id}:confirm"]
    assert wallet_balance(world.customer_id) == balance_before + 500

    events = event_rows()
    assert len(events) == 1
    assert events[0]["event_type"] == "payment.succeeded"
    assert events[0]["status"] == "processed"
    assert row["provider_event_id"] is not None


def test_payment_confirmed_outbox_row_in_same_transaction(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)

    deliver(client, envelope("evt_2", top_up_id=top_up_id))

    confirmed = outbox_rows("payment.confirmed")
    assert len(confirmed) == 1
    payload = confirmed[0]["payload"]
    assert confirmed[0]["aggregate_id"] == top_up_id
    assert payload["top_up_id"] == str(top_up_id)
    assert payload["customer_id"] == str(world.customer_id)
    assert payload["meter_id"] == str(world.meter_id)
    assert payload["amount_minor"] == 500
    assert payload["currency"] == "USD"
    assert payload["provider"] == PROVIDER
    assert payload["provider_txn_id"] == "MP-evt_2"
    assert payload["late"] is False
    assert payload["quoted_energy_wh"] > 0
    assert payload["tariff_version"] == str(world.tariff_id)
    assert payload["ledger_transaction_id"] == str(top_up_row(top_up_id)["ledger_transaction_id"])


def test_duplicate_event_returns_duplicate_with_no_second_effect(
    client: Client, world: World
) -> None:
    top_up_id = make_top_up(client, world)
    document = envelope("evt_3", top_up_id=top_up_id)

    first = deliver(client, document)
    balance_after_first = wallet_balance(world.customer_id)
    second = deliver(client, document)

    assert first.json()["data"]["status"] == "accepted"
    assert second.status_code == 200
    assert second.json()["data"] == {"status": "duplicate", "event_id": "evt_3"}
    assert len(event_rows()) == 1
    assert transactions_for(top_up_id) == [f"topup:{top_up_id}:confirm"]
    assert wallet_balance(world.customer_id) == balance_after_first
    assert len(outbox_rows("payment.confirmed")) == 1


def test_same_txn_different_event_id_is_duplicate(client: Client, world: World) -> None:
    """S2: the provider retried under a new event id for the same charge."""
    top_up_id = make_top_up(client, world)
    first_document = envelope("evt_s2_a", top_up_id=top_up_id)
    second_document = envelope("evt_s2_b", top_up_id=top_up_id)
    # Same charge: only the envelope id differs, so `(provider, provider_txn_id)` collides.
    second_document["data"]["provider_reference"] = first_document["data"]["provider_reference"]

    first = deliver(client, first_document)
    second = deliver(client, second_document)

    assert first.json()["data"]["status"] == "accepted"
    assert second.json()["data"]["status"] == "duplicate"
    assert len(event_rows()) == 1
    assert transactions_for(top_up_id) == [f"topup:{top_up_id}:confirm"]


def test_confirm_from_initiated_passes_through_pending(
    client: Client, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hub.top_ups_guard` has no `initiated -> confirmed` edge, so the credit walks two steps.

    A delivery can beat the provider's own answer to the initiation, which is exactly the row
    this test seeds: inserted as ``initiated`` and never moved to ``pending``.
    """
    top_up_id = seed_initiated_top_up(world)

    written: list[dict[str, Any]] = []
    repository = payments_wiring.top_up_repository()
    real_update = repository.update_fields

    def recording_update(top_up_id: uuid.UUID, **columns: Any) -> None:
        written.append(columns)
        real_update(top_up_id, **columns)

    monkeypatch.setattr(repository, "update_fields", recording_update)

    response = deliver(client, envelope("evt_4", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    statuses = [columns["status"] for columns in written if "status" in columns]
    assert statuses == ["pending", "confirmed"], statuses
    assert top_up_row(top_up_id)["status"] == "confirmed"


def test_response_body_is_webhook_ack(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)

    response = deliver(client, envelope("evt_5", top_up_id=top_up_id))

    assert response["Content-Type"] == "application/json"
    assert response.json() == {"data": {"status": "accepted", "event_id": "evt_5"}}


# --- failures -------------------------------------------------------------------------------


class _BrokenLedger:
    """The ledger, refusing every posting: the credit path must not survive it."""

    def post(self, request: Any, *, uow: Any) -> Any:
        del uow
        raise CreditPathUnavailable(details={"posting_key": request.posting_key})

    def ensure_wallet(self, customer_id: uuid.UUID, currency: str, *, uow: Any) -> uuid.UUID:
        del currency, uow
        return uuid.uuid4()

    def reverse(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - not called
        raise NotImplementedError


def break_the_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose `ConfirmPayment` with a ledger that refuses, exactly as wiring would."""
    monkeypatch.setattr(payments_wiring, "ledger", _BrokenLedger)
    payments_wiring.reset()


def test_ledger_failure_rolls_back_event_row(
    client: Client, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    top_up_id = make_top_up(client, world)
    break_the_ledger(monkeypatch)

    response = deliver(client, envelope("evt_6", top_up_id=top_up_id))

    assert response.status_code == 503, response.json()
    assert response["Retry-After"] == "30"
    assert event_rows() == []
    assert wallet_balance(world.customer_id) == 0
    assert top_up_row(top_up_id)["status"] == "pending"


def test_hint_is_not_written_when_transaction_fails(
    client: Client, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    top_up_id = make_top_up(client, world)
    break_the_ledger(monkeypatch)

    deliver(client, envelope("evt_7", top_up_id=top_up_id))

    assert redis_hint("evt_7") is None


def test_hint_hit_returns_duplicate_only_after_commit(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    document = envelope("evt_8", top_up_id=top_up_id)

    assert redis_hint("evt_8") is None
    deliver(client, document)
    # The hint exists only once the transaction committed.
    assert redis_hint("evt_8") == "1"

    again = deliver(client, document)

    assert again.json()["data"]["status"] == "duplicate"
    assert len(event_rows()) == 1


# --- routing --------------------------------------------------------------------------------


def test_unknown_reference_is_ignored_with_exception_event(client: Client) -> None:
    response = deliver(client, envelope("evt_9", top_up_id=uuid.uuid4()))

    assert response.status_code == 200, response.json()
    assert response.json()["data"]["status"] == "accepted"
    events = event_rows()
    assert len(events) == 1
    assert events[0]["status"] == "ignored"
    assert events[0]["last_error"] == "unknown_reference"

    exceptions = outbox_rows("reconciliation.exception")
    assert len(exceptions) == 1
    payload = exceptions[0]["payload"]
    assert payload["reason_code"] == "unknown_reference"
    assert payload["kind"] == "statement_only"
    assert payload["provider_event_id"] is not None


def test_failed_event_moves_pending_to_failed(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)

    response = deliver(
        client,
        envelope(
            "evt_10",
            top_up_id=top_up_id,
            event_type="payment.failed",
            failure_code="insufficient_funds",
        ),
    )

    assert response.status_code == 200, response.json()
    assert response.json()["data"]["status"] == "accepted"
    assert top_up_row(top_up_id)["status"] == "failed"
    assert transactions_for(top_up_id) == []
    assert wallet_balance(world.customer_id) == 0

    failed = outbox_rows("payment.failed")
    assert len(failed) == 1
    assert failed[0]["payload"]["failure_code"] == "insufficient_funds"


def test_failed_after_confirmed_does_not_downgrade(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    deliver(client, envelope("evt_11", top_up_id=top_up_id))

    response = deliver(
        client,
        envelope("evt_12", top_up_id=top_up_id, event_type="payment.failed", failure_code="late"),
    )

    assert response.status_code == 200, response.json()
    assert top_up_row(top_up_id)["status"] == "confirmed"
    assert wallet_balance(world.customer_id) == 500
    events = event_rows()
    assert [event["status"] for event in events] == ["processed", "processed"]
    assert events[1]["last_error"] == "ignored_terminal"
    assert len(outbox_rows("payment.failed")) == 0


def test_a_confirmation_for_a_failed_top_up_is_not_silently_accepted(
    client: Client, world: World
) -> None:
    """Money for a top-up the hub already gave up on cannot be credited by this build.

    `top_ups_guard` has no `failed -> confirmed` edge, and routing it to `suspense` is Task
    04.8. Answering 503 commits nothing and lets the provider retry, which is the only honest
    answer while the suspense path does not exist.
    """
    top_up_id = make_top_up(client, world)
    deliver(
        client,
        envelope(
            "evt_13", top_up_id=top_up_id, event_type="payment.failed", failure_code="declined"
        ),
    )
    assert top_up_row(top_up_id)["status"] == "failed"

    response = deliver(client, envelope("evt_14", top_up_id=top_up_id))

    assert response.status_code == 503, response.json()
    assert response["Retry-After"] == "30"
    assert top_up_row(top_up_id)["status"] == "failed"
    assert [event["event_id"] for event in event_rows()] == ["evt_13"]


def test_a_late_confirmation_of_an_expired_top_up_is_credited(client: Client, world: World) -> None:
    """D-05: money that arrived is money the customer has, and the row says it was late."""
    top_up_id = make_top_up(client, world)
    # What the expiry sweeper (Task 04.10) would have done before the delivery arrived.
    with connection.cursor() as cursor:
        cursor.execute("UPDATE hub.top_ups SET status = 'expired' WHERE id = %s", [top_up_id])

    response = deliver(client, envelope("evt_15", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    row = top_up_row(top_up_id)
    assert (row["status"], row["late"]) == ("confirmed", True)
    assert wallet_balance(world.customer_id) == 500
    assert outbox_rows("payment.confirmed")[0]["payload"]["late"] is True
