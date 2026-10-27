"""`ConfirmPayment`: one delivery, recorded once, credited once (Task 04.5, FR-03, NFR-05).

These tests go through the real route and a real PostgreSQL, because the properties they pin
are properties of the *database*: the unique index decides who won, the ledger function refuses
a credit without stored provider evidence, and the top-up guard refuses a status that skips a
step. A fake would let all three pass while the deployed system did none of them.

The one thing that is not real is the provider: `FakeProviderGateway` signs with the same
algorithm the HTTP adapter uses, so a delivery here is verified exactly as a production one is.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.db import connection
from django.test import Client

from hub.payments import wiring as payments_wiring
from hub.payments.domain.errors import CreditPathUnavailable
from support.payments import NOW, World
from support.webhooks import (
    PROVIDER,
    deliver,
    envelope,
    event_rows,
    make_top_up,
    outbox_rows,
    redis_hint,
    seed_top_up,
    top_up_row,
    transactions_for,
    wallet_balance,
    webhook_pool,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def _webhook_pool() -> Any:
    """Serve the route from the webhooks URLConf, as the deployed pool does."""
    with webhook_pool():
        yield  # --- the credit -----------------------------------------------------------------------------


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
    top_up_id = seed_top_up(world, status="initiated")

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

    def post_suspense(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - not called
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
