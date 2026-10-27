"""Initiation, reads and cancellation over HTTP (Tasks 04.2 and 04.3, FR-02).

The money assertions are about *when* things happen: the row and its event exist before the
provider is asked, the key replays for seven days, and nothing is written for a request the
permission class, the plan limits or the idempotency window refuse.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from psycopg.types.json import Jsonb

from hub.identity.contracts import Principal
from hub.ledger import wiring as ledger_wiring
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.payments import wiring as payments_wiring
from hub.payments.application.top_ups import TopUpQuery
from hub.payments.domain.errors import InsufficientScope
from hub.shared.money import Money
from support.payments import NOW, PAYER, World, customer_token, staff_token, top_up_count

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TOP_UPS = "/api/v1/top-ups"


def body(world: World, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "meter_id": str(world.meter_id),
        "amount": {"amount_minor": 50_000, "currency": "USD"},
        "provider": "mpesa_cd",
        "payer_msisdn": PAYER,
    }
    document.update(overrides)
    return document


def post(client: Client, token: str, document: dict[str, Any], key: str | None) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(TOP_UPS, document, content_type="application/json", headers=headers)


def get(client: Client, token: str, path: str) -> Any:
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.tc("TC-FR-02-3")
def test_same_key_same_body_replays_and_provider_called_once(client: Client, world: World) -> None:
    token = customer_token(world.customer_id)
    gateway = payments_wiring.gateway("synthetic")

    first = post(client, token, body(world), "key-1")
    second = post(client, token, body(world), "key-1")

    assert first.status_code == 202, first.json()
    assert second.status_code == 202, second.json()
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert first.headers["Idempotent-Replayed"] == "false"
    assert second.headers["Idempotent-Replayed"] == "true"
    assert len(gateway.calls) == 1
    assert top_up_count(customer_id=world.customer_id) == 1


def test_same_key_different_body_is_422_idempotency_key_reuse(client: Client, world: World) -> None:
    token = customer_token(world.customer_id)
    post(client, token, body(world), "key-2")

    changed = post(
        client, token, body(world, amount={"amount_minor": 60_000, "currency": "USD"}), "key-2"
    )

    assert changed.status_code == 422, changed.json()
    assert changed.json()["code"] == "idempotency_key_reuse"
    assert top_up_count(customer_id=world.customer_id) == 1


def test_missing_key_is_400_idempotency_key_required(client: Client, world: World) -> None:
    response = post(client, customer_token(world.customer_id), body(world), None)

    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "idempotency_key_required"
    assert top_up_count() == 0


def test_key_expires_after_7_days(client: Client, world: World) -> None:
    """The window is the database's: the same key is a new request one second past it."""
    token = customer_token(world.customer_id)
    first = post(client, token, body(world), "key-3")
    assert first.status_code == 202

    world.clock.advance(timedelta(days=7, seconds=1))
    # The access token from the first call expired long ago (it lives ten minutes), so the
    # second attempt signs in again. Only the *idempotency key* is the subject of this test.
    second = post(client, customer_token(world.customer_id), body(world), "key-3")

    assert second.status_code == 202, second.json()
    assert second.json()["data"]["id"] != first.json()["data"]["id"]
    assert second.headers["Idempotent-Replayed"] == "false"
    assert top_up_count(customer_id=world.customer_id) == 2


@pytest.mark.parametrize(
    "role", ["support_agent", "finance_officer", "operations_engineer", "administrator"]
)
def test_staff_token_is_403(client: Client, world: World, role: str) -> None:
    """No staff role may initiate a top-up (D-14), whatever its other powers."""
    response = post(client, staff_token((role,)), body(world), f"staff-{role}")

    assert response.status_code == 403, response.json()
    assert top_up_count() == 0


def test_customer_id_in_body_is_ignored_for_customers(client: Client, world: World) -> None:
    """A portal request cannot choose whose wallet is credited (D-14)."""
    token = customer_token(world.customer_id)

    response = post(client, token, body(world, customer_id=str(uuid.uuid4())), "key-4")

    assert response.status_code == 202, response.json()
    assert response.json()["data"]["customer_id"] == str(world.customer_id)


def test_get_top_up_customer_sees_only_own(
    client: Client, world: World, make_world_factory: Any
) -> None:
    created = post(client, customer_token(world.customer_id), body(world), "key-5")
    top_up_id = created.json()["data"]["id"]

    mine = get(client, customer_token(world.customer_id), f"{TOP_UPS}/{top_up_id}")
    assert mine.status_code == 200, mine.json()
    assert mine["Cache-Control"] == "no-store"

    # A second, real customer: 404 rather than 403, so the id's existence does not leak.
    other_world = make_world_factory()
    other = get(client, customer_token(other_world.customer_id), f"{TOP_UPS}/{top_up_id}")
    assert other.status_code == 404, other.json()


def test_list_top_ups_cursor_pagination(client: Client, world: World) -> None:
    token = customer_token(world.customer_id)
    ids = []
    for index in range(3):
        created = post(client, token, body(world, payer_msisdn=f"{PAYER}{index}"), f"key-6-{index}")
        ids.append(created.json()["data"]["id"])

    first = get(client, token, f"{TOP_UPS}?limit=2")
    assert first.status_code == 200, first.json()
    assert first.json()["meta"]["has_next"] is True
    cursor = first.json()["meta"]["next_cursor"]
    assert len(first.json()["data"]) == 2

    second = get(client, token, f"{TOP_UPS}?limit=2&cursor={cursor}")
    assert second.status_code == 200, second.json()
    assert second.json()["meta"]["has_next"] is False
    assert len(second.json()["data"]) == 1

    seen = [item["id"] for item in first.json()["data"] + second.json()["data"]]
    assert sorted(seen) == sorted(ids)


def test_integrator_scope_topups_read_required(world: World) -> None:
    """An API key without ``topups:read`` may not list; the check is in the use case."""
    integrator = Principal(subject_id=uuid.uuid4(), kind="integrator", scopes=frozenset())

    with pytest.raises(InsufficientScope) as raised:
        payments_wiring.top_ups().list(integrator, TopUpQuery())

    assert raised.value.code == "insufficient_scope"

    reader = Principal(
        subject_id=uuid.uuid4(), kind="integrator", scopes=frozenset({"topups:read"})
    )
    rows, _ = payments_wiring.top_ups().list(reader, TopUpQuery())
    assert rows == []
    del world


def test_masked_payer_number_in_response(client: Client, world: World) -> None:
    """The response never carries the payer's full number (FR-02)."""
    response = post(client, customer_token(world.customer_id), body(world), "key-7")

    assert response.json()["data"]["payer_msisdn_masked"] == "+243*******11"
    assert PAYER not in response.content.decode()


def test_cancel_pending_sets_cancelled(client: Client, world: World) -> None:
    created = post(client, customer_token(world.customer_id), body(world), "key-8")
    top_up_id = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "pending"

    cancelled = client.post(
        f"{TOP_UPS}/{top_up_id}/cancel",
        headers={"Authorization": f"Bearer {customer_token(world.customer_id)}"},
    )

    assert cancelled.status_code == 200, cancelled.json()
    assert cancelled.json()["data"]["status"] == "cancelled"


def test_cancel_twice_is_idempotent_200(client: Client, world: World) -> None:
    token = customer_token(world.customer_id)
    top_up_id = post(client, token, body(world), "key-9").json()["data"]["id"]
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post(f"{TOP_UPS}/{top_up_id}/cancel", headers=headers)
    second = client.post(f"{TOP_UPS}/{top_up_id}/cancel", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200, second.json()
    assert second.json()["data"]["status"] == "cancelled"


def test_cancel_confirmed_is_409_invalid_state_transition(client: Client, world: World) -> None:
    token = customer_token(world.customer_id)
    created = post(client, token, body(world), "key-10")
    top_up_id = uuid.UUID(created.json()["data"]["id"])
    _confirm(world, top_up_id)

    refused = client.post(
        f"{TOP_UPS}/{top_up_id}/cancel", headers={"Authorization": f"Bearer {token}"}
    )

    assert refused.status_code == 409, refused.json()
    assert refused.json()["code"] == "invalid_state_transition"


def _confirm(world: World, top_up_id: uuid.UUID) -> None:
    """Confirm a top-up the way the delivery path will (Task 04.5), without the webhook yet.

    The ledger function demands a stored success event matching the top-up, and the guard only
    lets the payment path set ``confirmed`` — so the evidence is built first and the row is
    walked through ``pending``, exactly as the trigger requires.
    """
    event_id = uuid.uuid4()
    provider_txn = f"TXN-{event_id.hex[:12]}"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT amount_minor, currency, customer_id, meter_id FROM hub.top_ups WHERE id = %s",
            [top_up_id],
        )
        amount_minor, currency, _customer_id, _meter_id = cursor.fetchone()
        cursor.execute(
            "INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id,"
            " event_type, amount_minor, currency, payload, status)"
            " VALUES (%s, 'mpesa_cd', %s, %s, 'payment.succeeded', %s, %s, %s, 'received')",
            [event_id, f"evt-{event_id.hex[:12]}", provider_txn, amount_minor, currency, Jsonb({})],
        )
        cursor.execute(
            "UPDATE hub.top_ups SET provider_event_id = %s, provider_txn_id = %s WHERE id = %s",
            [event_id, provider_txn, top_up_id],
        )
    with ledger_wiring.unit_of_work() as uow:
        result = ledger_wiring.service().post(
            PostingRequest(
                posting_key=f"topup:{top_up_id}:confirm",
                kind="topup_received",
                lines=[
                    PostingLine(
                        account=AccountRef(
                            kind="provider_clearing", currency="USD", provider="mpesa_cd"
                        ),
                        direction="debit",
                        amount=Money(int(amount_minor), "USD"),
                    ),
                    PostingLine(
                        account=AccountRef(
                            kind="customer_wallet", currency="USD", customer_id=world.customer_id
                        ),
                        direction="credit",
                        amount=Money(int(amount_minor), "USD"),
                    ),
                ],
                effective_at=datetime.now(UTC),
                source_top_up_id=top_up_id,
            ),
            uow=uow,
        )
    with connection.cursor() as cursor:
        cursor.execute("UPDATE hub.top_ups SET status = 'pending' WHERE id = %s", [top_up_id])
        cursor.execute(
            "UPDATE hub.top_ups SET status = 'confirmed', confirmed_at = %s,"
            " ledger_transaction_id = %s WHERE id = %s",
            [NOW, result.transaction_id, top_up_id],
        )


def test_a_finance_officer_reads_the_page_but_not_the_payer_number(
    client: Client, world: World
) -> None:
    """The staff list is site-scoped and masked; finance does not need the payer's number."""
    post(client, customer_token(world.customer_id), body(world), "key-11")
    del world

    token = staff_token(("finance_officer",), all_sites=True)
    page = get(client, token, TOP_UPS)

    assert page.status_code == 200, page.json()
    assert page.json()["data"][0]["payer_msisdn_masked"].endswith("11")


def test_a_top_up_outside_the_staff_site_scope_is_404(client: Client, world: World) -> None:
    top_up_id = post(client, customer_token(world.customer_id), body(world), "key-12").json()[
        "data"
    ]["id"]
    # A finance officer bound to no site at all: the scope is empty, not universal.
    token = staff_token(("finance_officer",), all_sites=False)

    assert get(client, token, f"{TOP_UPS}/{top_up_id}").status_code == 404
    assert get(client, token, TOP_UPS).json()["data"] == []
