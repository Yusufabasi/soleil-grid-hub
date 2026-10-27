"""Late credit, mismatch routing and the pull (Task 04.8, D-05, D-09).

Every test here is about money that arrived and could not be credited the ordinary way. Two
answers are possible and both are asserted through PostgreSQL, never through the use case's
return value:

* **a late confirmation credits once**, with `late` on the row and in the event — the money is
  the customer's whatever the clock says;
* **anything that disagrees goes to `suspense`**: the provider's amount is posted against the
  suspense account, the wallet is untouched, and a `reconciliation.exception` is staged so a
  person resolves it.

The pull tests use the in-memory provider's status map, which is the same adapter the route
uses: a pull decision that the wiring does not make cannot be faked here.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.db import connection
from django.test import Client, override_settings

from hub.payments import wiring as payments_wiring
from hub.payments.application.ports import ProviderTxnStatus
from hub.shared.money import Money
from support.payments import NOW, World, customer_token
from support.webhooks import (
    deliver,
    envelope,
    event_rows,
    make_top_up,
    outbox_rows,
    seed_top_up,
    top_up_row,
    transactions_for,
    wallet_balance,
    webhook_pool,
    wired_gateway,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def _webhook_pool() -> Any:
    with webhook_pool():
        yield


def cancel(client: Client, world: World, top_up_id: uuid.UUID) -> None:
    """Cancel through the API, so the row is cancelled by the same path a customer uses."""
    with override_settings(ROOT_URLCONF="hub.urls.api"):
        response = client.post(
            f"/api/v1/top-ups/{top_up_id}/cancel",
            headers={"Authorization": f"Bearer {customer_token(world.customer_id)}"},
        )
    assert response.status_code == 200, response.json()
    assert response.json()["data"]["status"] == "cancelled"


def provider_status(
    reference: str, *, status: str = "successful", amount_minor: int = 500, currency: str = "USD"
) -> None:
    """Teach the provider's status API what it says about one charge."""
    wired_gateway().statuses[reference] = ProviderTxnStatus(
        status=status,  # type: ignore[arg-type]
        amount=Money(amount_minor, currency),  # type: ignore[arg-type]
    )


def pull_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every status pull the use case makes, leaving the adapter's answer intact."""
    adapter = wired_gateway()
    calls: list[str] = []
    real = adapter.fetch_status

    def recording(provider_txn_id: str) -> ProviderTxnStatus:
        calls.append(provider_txn_id)
        answered: ProviderTxnStatus = real(provider_txn_id)
        return answered

    monkeypatch.setattr(adapter, "fetch_status", recording)
    return calls


def suspense_keys(top_up_id: uuid.UUID) -> list[str]:
    return [key for key in transactions_for(top_up_id) if ":suspense:" in key]


def exceptions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = outbox_rows("reconciliation.exception")
    return rows


# --- late credits ---------------------------------------------------------------------------


def test_late_confirmation_after_cancel_credits_once(client: Client, world: World) -> None:
    """D-05: a cancelled top-up that is paid anyway is credited, and the row says `late`."""
    top_up_id = make_top_up(client, world)
    cancel(client, world, top_up_id)

    response = deliver(client, envelope("evt_late_cancel", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    assert response.json()["data"]["status"] == "accepted"
    row = top_up_row(top_up_id)
    assert (row["status"], row["late"]) == ("confirmed", True)
    assert wallet_balance(world.customer_id) == 500
    assert transactions_for(top_up_id) == [f"topup:{top_up_id}:confirm"]
    assert outbox_rows("payment.confirmed")[0]["payload"]["late"] is True
    assert exceptions() == []


def test_a_late_confirmation_is_not_suspense_even_when_the_provider_agrees(
    client: Client, world: World
) -> None:
    top_up_id = make_top_up(client, world)
    with connection.cursor() as cursor:
        cursor.execute("UPDATE hub.top_ups SET status = 'expired' WHERE id = %s", [top_up_id])

    response = deliver(client, envelope("evt_late_pull", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    assert top_up_row(top_up_id)["status"] == "confirmed"
    assert suspense_keys(top_up_id) == []


# --- mismatches -----------------------------------------------------------------------------


def assert_parked(top_up_id: uuid.UUID, customer_id: uuid.UUID, reason: str, kind: str) -> None:
    """The shared half of every mismatch: no credit, one suspense posting, one exception."""
    row = top_up_row(top_up_id)
    assert wallet_balance(customer_id) == 0
    assert f"topup:{top_up_id}:confirm" not in transactions_for(top_up_id)
    assert len(suspense_keys(top_up_id)) == 1
    events = event_rows()
    assert [event["status"] for event in events] == ["processed"]
    assert events[0]["last_error"] == reason
    raised = exceptions()
    assert len(raised) == 1
    assert raised[0]["payload"]["reason_code"] == reason
    assert raised[0]["payload"]["kind"] == kind
    assert row["ledger_transaction_id"] is None


def test_amount_mismatch_costs_the_customer_nothing(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)

    response = deliver(client, envelope("evt_amount", top_up_id=top_up_id, amount_minor=600))

    assert response.status_code == 200, response.json()
    assert_parked(top_up_id, world.customer_id, "amount_mismatch", "amount_mismatch")


def test_currency_mismatch_costs_the_customer_nothing(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)

    response = deliver(client, envelope("evt_currency", top_up_id=top_up_id, currency="CDF"))

    assert response.status_code == 200, response.json()
    assert_parked(top_up_id, world.customer_id, "currency_mismatch", "currency_mismatch")


def test_payer_mismatch_goes_to_suspense(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)

    response = deliver(
        client,
        envelope("evt_payer", top_up_id=top_up_id, payer="+243810000222"),
    )

    assert response.status_code == 200, response.json()
    assert_parked(top_up_id, world.customer_id, "payer_mismatch", "statement_only")


def test_an_erased_customer_cannot_be_credited(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    with connection.cursor() as cursor:
        # Erasure scrubs the contact details and records when (`ck_customers_erased`); the row
        # itself stays so its history keeps pointing at a real account.
        cursor.execute(
            "UPDATE hub.customers SET status = 'erased', phone_e164 = NULL, erased_at = %s"
            " WHERE id = %s",
            [NOW, world.customer_id],
        )

    response = deliver(client, envelope("evt_erased", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    assert_parked(top_up_id, world.customer_id, "erased_customer", "statement_only")


def test_a_decommissioned_meter_cannot_be_credited(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE hub.meters SET status = 'decommissioned' WHERE id = %s", [world.meter_id]
        )

    response = deliver(client, envelope("evt_decommissioned", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    assert_parked(top_up_id, world.customer_id, "meter_decommissioned", "statement_only")


def test_a_second_charge_against_settled_money_is_suspense(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    deliver(client, envelope("evt_first", top_up_id=top_up_id))
    assert wallet_balance(world.customer_id) == 500

    response = deliver(
        client,
        envelope("evt_second", top_up_id=top_up_id, provider_reference="MP-second-charge"),
    )

    assert response.status_code == 200, response.json()
    row = top_up_row(top_up_id)
    assert row["status"] == "confirmed"
    # The first credit stands, the second payment is parked, and the wallet did not grow.
    assert wallet_balance(world.customer_id) == 500
    assert len(suspense_keys(top_up_id)) == 1
    events = event_rows()
    assert events[1]["last_error"] == "already_confirmed"
    assert exceptions()[0]["payload"]["reason_code"] == "already_confirmed"


def test_a_confirmation_for_a_failed_top_up_is_parked_not_dropped(
    client: Client, world: World
) -> None:
    """docs/03 wants a credit; `top_ups_guard` refuses `failed -> confirmed`. D-05 wins."""
    top_up_id = make_top_up(client, world)
    deliver(
        client,
        envelope(
            "evt_failed_first",
            top_up_id=top_up_id,
            event_type="payment.failed",
            failure_code="declined",
        ),
    )
    assert top_up_row(top_up_id)["status"] == "failed"

    response = deliver(client, envelope("evt_failed_then_paid", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    assert top_up_row(top_up_id)["status"] == "failed"
    assert wallet_balance(world.customer_id) == 0
    assert len(suspense_keys(top_up_id)) == 1
    assert event_rows()[-1]["last_error"] == "top_up_failed_state"
    assert exceptions()[0]["payload"]["reason_code"] == "top_up_failed_state"


# --- the pull -------------------------------------------------------------------------------


def test_pull_verification_at_threshold(
    client: Client, world: World, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reserved for the provider's own answer: it costs a round trip, so only at the threshold."""
    settings.HUB_PULL_VERIFY_THRESHOLD_MINOR = {"USD": 500}
    payments_wiring.reset()
    top_up_id = make_top_up(client, world, amount_minor=500)
    provider_status("MP-evt_pull_at", amount_minor=500)
    calls = pull_calls(monkeypatch)

    response = deliver(client, envelope("evt_pull_at", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    assert calls == ["MP-evt_pull_at"]
    row = top_up_row(top_up_id)
    assert row["status"] == "confirmed"
    assert row["status_pull_at"] == NOW
    assert wallet_balance(world.customer_id) == 500


def test_no_pull_below_the_threshold(
    client: Client, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    top_up_id = make_top_up(client, world, amount_minor=500)
    calls = pull_calls(monkeypatch)

    response = deliver(client, envelope("evt_no_pull", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    assert calls == []
    assert top_up_row(top_up_id)["status_pull_at"] is None


def test_pull_disagreement_sets_pending_unknown_and_suspense(
    client: Client, world: World, settings: Any
) -> None:
    settings.HUB_PULL_VERIFY_THRESHOLD_MINOR = {"USD": 500}
    payments_wiring.reset()
    top_up_id = make_top_up(client, world, amount_minor=500)
    provider_status("MP-evt_pull_liar", amount_minor=9_999)

    response = deliver(client, envelope("evt_pull_liar", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    row = top_up_row(top_up_id)
    assert row["status"] == "pending_unknown"
    assert row["status_pull_at"] == NOW
    assert wallet_balance(world.customer_id) == 0
    assert len(suspense_keys(top_up_id)) == 1
    assert event_rows()[-1]["last_error"] == "pull_disagrees"
    assert exceptions()[0]["payload"]["reason_code"] == "pull_disagrees"


def test_provider_unreachable_on_pull_is_503_and_nothing_stored(
    client: Client, world: World, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/03 6.1: no reliable answer means nothing is stored and the provider retries."""
    settings.HUB_PULL_VERIFY_THRESHOLD_MINOR = {"USD": 500}
    payments_wiring.reset()
    top_up_id = make_top_up(client, world, amount_minor=500)

    def unreachable(provider_txn_id: str) -> ProviderTxnStatus:
        del provider_txn_id
        raise TimeoutError("the provider did not answer")

    monkeypatch.setattr(wired_gateway(), "fetch_status", unreachable)

    response = deliver(client, envelope("evt_pull_dead", top_up_id=top_up_id))

    assert response.status_code == 503, response.json()
    assert response["Retry-After"] == "30"
    assert event_rows() == []
    assert top_up_row(top_up_id)["status"] == "pending"
    assert transactions_for(top_up_id) == []


def test_payer_mismatch_triggers_pull_and_stores_status_pull_at(
    client: Client, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A payer that disagrees is the second reason to ask: the money may be somebody else's."""
    top_up_id = make_top_up(client, world)
    provider_status("MP-evt_payer_pull", amount_minor=500)
    calls = pull_calls(monkeypatch)

    response = deliver(
        client, envelope("evt_payer_pull", top_up_id=top_up_id, payer="+243810000333")
    )

    assert response.status_code == 200, response.json()
    assert calls == ["MP-evt_payer_pull"]
    row = top_up_row(top_up_id)
    assert row["status_pull_at"] == NOW
    assert row["payer_matched"] is False
    assert row["status"] == "pending"
    assert len(suspense_keys(top_up_id)) == 1


def test_unknown_reference_is_parked_and_not_dropped(client: Client) -> None:
    """A delivery for a top-up this hub does not have is recorded and handed to a person."""
    response = deliver(client, envelope("evt_orphan", top_up_id=uuid.uuid4()))

    assert response.status_code == 200, response.json()
    assert event_rows()[0]["status"] == "ignored"
    assert outbox_rows("reconciliation.exception")[0]["payload"]["reason_code"] == (
        "unknown_reference"
    )


def test_suspense_receipt_is_zero_sum(client: Client, world: World) -> None:
    """The parked money is a balanced pair: the ledger still proves every transaction sums to 0."""
    top_up_id = make_top_up(client, world)
    deliver(client, envelope("evt_zero_sum", top_up_id=top_up_id, amount_minor=600))

    key = suspense_keys(top_up_id)[0]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT sum(e.amount_minor), count(*) FROM hub.ledger_entries e"
            "  JOIN hub.ledger_transactions t ON t.id = e.transaction_id"
            " WHERE t.posting_key = %s",
            [key],
        )
        total, lines = cursor.fetchone()
    assert total == 0
    assert lines == 2


def test_a_seeded_pending_top_up_credits_once(client: Client, world: World) -> None:
    """A row seeded straight into `pending` credits, which is the ordinary provider path."""
    top_up_id = seed_top_up(world, status="pending")

    deliver(client, envelope("evt_seeded", top_up_id=top_up_id))

    assert top_up_row(top_up_id)["status"] == "confirmed"
    assert wallet_balance(world.customer_id) == 500
