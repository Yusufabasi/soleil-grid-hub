"""Provider reversals: give the money back, and book what the wallet no longer holds (D-06).

A reversal is the one event that has to look at the *ledger*, not at the top-up: the customer
may already have spent part of the credit on energy, and the hub cannot un-burn it. The SQL
function (`hub.reverse_ledger_transaction`) computes the split, writes the reversing entries and
stages `ledger.chargeback_opened` in the same transaction; these tests assert both halves —
what the wallet returns and what becomes a receivable.

The reversal envelope names the *same* charge as the confirmation (`provider_reference`), and the
stored event is keyed `<reference>-R`: `(provider, provider_txn_id)` therefore holds two rows for
one charge — the payment and its reversal — without the second being swallowed as a duplicate.
The suffix is the store's, not the envelope's: a provider that resent the confirmation under the
same reference must still be a duplicate, and only a reversal may share that reference.

Named `test_provider_reversal.py`, not the slice's `test_reversal.py`: the ledger suite already
holds a `test_reversal.py` (reversing one transaction), and this tree's test directories carry no
`__init__.py`, so two modules with one basename cannot both be collected.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.test import Client

from hub.identity import wiring as identity_wiring
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.ledger.wiring import service as ledger_service
from hub.shared.money import Money
from support.payments import NOW, World
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
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def _webhook_pool() -> Any:
    with webhook_pool():
        yield


def confirmed(client: Client, world: World, *, amount_minor: int = 500) -> tuple[uuid.UUID, str]:
    """A confirmed top-up and the provider reference its confirmation carried."""
    top_up_id = make_top_up(client, world, amount_minor=amount_minor)
    confirmation = envelope("evt_charge", top_up_id=top_up_id, amount_minor=amount_minor)
    response = deliver(client, confirmation)
    assert response.status_code == 200, response.json()
    assert top_up_row(top_up_id)["status"] == "confirmed"
    return top_up_id, confirmation["data"]["provider_reference"]


def spend(client: Client, world: World, top_up_id: uuid.UUID, *, amount_minor: int) -> None:
    """Post an energy purchase from the wallet, as slice 06 will when a token is issued."""
    del client
    with identity_wiring.unit_of_work(None) as uow:
        ledger_service().post(
            PostingRequest(
                posting_key=f"energy:{top_up_id}:1",
                kind="energy_purchase",
                lines=[
                    PostingLine(
                        account=AccountRef(
                            kind="customer_wallet", currency="USD", customer_id=world.customer_id
                        ),
                        direction="debit",
                        amount=Money(amount_minor, "USD"),
                    ),
                    PostingLine(
                        account=AccountRef(kind="revenue_energy", currency="USD"),
                        direction="credit",
                        amount=Money(amount_minor, "USD"),
                    ),
                ],
                effective_at=NOW,
                source_top_up_id=top_up_id,
            ),
            uow=uow,
        )


def reversal(
    confirmation_reference: str, top_up_id: uuid.UUID, *, event_id: str = "evt_rev"
) -> dict[str, Any]:
    document: dict[str, Any] = envelope(
        event_id,
        top_up_id=top_up_id,
        event_type="payment.reversed",
        provider_reference=confirmation_reference,
    )
    return document


def chargeback_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = outbox_rows("ledger.chargeback_opened")
    return rows


def test_reversal_before_energy_issued_returns_full_amount(client: Client, world: World) -> None:
    top_up_id, reference = confirmed(client, world)
    assert wallet_balance(world.customer_id) == 500

    response = deliver(client, reversal(reference, top_up_id))

    assert response.status_code == 200, response.json()
    row = top_up_row(top_up_id)
    assert row["status"] == "reversed"
    assert row["reversal_transaction_id"] is not None
    assert wallet_balance(world.customer_id) == 0
    # Nothing was spent, so nothing becomes a receivable.
    assert chargeback_rows() == []
    reversed_events = outbox_rows("payment.reversed")
    assert len(reversed_events) == 1
    payload = reversed_events[0]["payload"]
    assert payload["reversed_minor"] == 500
    assert payload["shortfall_minor"] == 0
    assert payload["chargeback_transaction_id"] is None
    assert payload["ledger_transaction_id"] == str(row["reversal_transaction_id"])


def test_reversal_after_energy_issued_posts_shortfall(client: Client, world: World) -> None:
    top_up_id, reference = confirmed(client, world)
    spend(client, world, top_up_id, amount_minor=400)  # 80 percent burnt into energy
    assert wallet_balance(world.customer_id) == 100

    response = deliver(client, reversal(reference, top_up_id))

    assert response.status_code == 200, response.json()
    assert top_up_row(top_up_id)["status"] == "reversed"
    # The wallet gives back what it still holds; the burnt part becomes a receivable.
    assert wallet_balance(world.customer_id) == 0
    payload = outbox_rows("payment.reversed")[0]["payload"]
    assert payload["reversed_minor"] == 100
    assert payload["shortfall_minor"] == 400
    assert payload["chargeback_transaction_id"] == payload["ledger_transaction_id"]

    chargeback = chargeback_rows()
    assert len(chargeback) == 1
    assert chargeback[0]["payload"]["shortfall_minor"] == 400
    assert chargeback[0]["payload"]["currency"] == "USD"
    assert chargeback[0]["aggregate_id"] == uuid.UUID(str(payload["ledger_transaction_id"]))


def test_reversal_event_has_own_txn_id(client: Client, world: World) -> None:
    top_up_id, reference = confirmed(client, world)

    deliver(client, reversal(reference, top_up_id))

    events = event_rows()
    assert [event["event_type"] for event in events] == ["payment.succeeded", "payment.reversed"]
    assert events[0]["provider_txn_id"] == reference
    # One charge, two stored keys: the reversal is not the payment, and not a second payment.
    assert events[1]["provider_txn_id"] == f"{reference}-R"
    assert events[1]["status"] == "processed"


def test_duplicate_reversal_is_noop(client: Client, world: World) -> None:
    top_up_id, reference = confirmed(client, world)
    document = reversal(reference, top_up_id)

    first = deliver(client, document)
    balance_after_first = wallet_balance(world.customer_id)
    second = deliver(client, document)

    assert first.json()["data"]["status"] == "accepted"
    assert second.json()["data"]["status"] == "duplicate"
    assert wallet_balance(world.customer_id) == balance_after_first == 0
    assert len([key for key in transactions_for(top_up_id) if ":reverse" in key]) == 1


def test_reversal_of_unconfirmed_top_up_is_exception_not_posting(
    client: Client, world: World
) -> None:
    """Money "returned" for a top-up this hub never credited: a fact, not a posting."""
    top_up_id = seed_top_up(world, status="pending")

    response = deliver(client, reversal("MP-never-charged", top_up_id))

    assert response.status_code == 200, response.json()
    assert top_up_row(top_up_id)["status"] == "pending"
    assert transactions_for(top_up_id) == []
    assert event_rows()[0]["last_error"] == "nothing_to_reverse"
    raised = outbox_rows("reconciliation.exception")
    assert len(raised) == 1
    assert raised[0]["payload"]["reason_code"] == "nothing_to_reverse"
    assert raised[0]["payload"]["kind"] == "statement_only"
