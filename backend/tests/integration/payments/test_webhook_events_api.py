"""The staff webhook log and replay (Task 04.10, FR-03).

Three properties matter here, and each is a way the console could be wrong:

* **Only finance and operations may look.** The log names providers, amounts and customers, so
  every other role is refused by the contract's matrix (``api/permissions.yaml``), not by a check
  written in the view.
* **The log never shows the payload.** ``hub_api`` holds a narrowed column grant and the adapter
  selects only the granted columns, so the stored envelope — with its payer number — cannot leak
  even by accident.
* **Replay is a request.** The route answers 202 and stages `payments.replay_event` in the
  durable queue; the job re-drives the stored event through the same exactly-once path, which
  makes replaying a settled event a no-op rather than a second effect.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client, override_settings

from hub.payments import wiring as payments_wiring
from hub.shared.jobs import run_job
from support.payments import NOW, World, customer_token, staff_token
from support.webhooks import (
    PROVIDER,
    deliver,
    envelope,
    make_top_up,
    seed_top_up,
    top_up_row,
    transactions_for,
    wallet_balance,
    webhook_pool,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

LOG = "/api/v1/webhook-events"


@pytest.fixture(autouse=True)
def _webhook_pool() -> Any:
    with webhook_pool():
        yield


def staff_headers(*roles: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {staff_token(roles=roles)}"}


def job_rows() -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, event_type, payload FROM hub.outbox WHERE event_type = 'job.requested'"
        )
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "event_type": row[1],
            "payload": json.loads(row[2]) if isinstance(row[2], str) else row[2],
        }
        for row in rows
    ]


def event_id_of(event_key: str) -> UUID:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM hub.provider_events WHERE event_id = %s", [event_key])
        found = cursor.fetchone()
    assert found is not None, event_key
    return UUID(str(found[0]))


# --- the log --------------------------------------------------------------------------------


def test_list_requires_finance_or_ops_role(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    deliver(client, envelope("evt_log_roles", top_up_id=top_up_id))

    with override_settings(ROOT_URLCONF="hub.urls.api"):
        for role in ("support_agent", "administrator"):
            refused = client.get(LOG, headers=staff_headers(role))
            assert refused.status_code == 403, (role, refused.json())
        customer = client.get(
            LOG, headers={"Authorization": f"Bearer {customer_token(world.customer_id)}"}
        )
        assert customer.status_code == 403
        for role in ("finance_officer", "operations_engineer"):
            allowed = client.get(LOG, headers=staff_headers(role))
            assert allowed.status_code == 200, (role, allowed.json())


def test_log_lists_the_delivery_with_its_outcome(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    deliver(client, envelope("evt_log", top_up_id=top_up_id))

    with override_settings(ROOT_URLCONF="hub.urls.api"):
        response = client.get(LOG, headers=staff_headers("finance_officer"))

    assert response.status_code == 200, response.json()
    body = response.json()
    assert len(body["data"]) == 1
    entry = body["data"][0]
    assert entry["event_id"] == "evt_log"
    assert entry["provider"] == PROVIDER
    assert entry["event_type"] == "payment.succeeded"
    assert entry["signature_valid"] is True
    assert entry["status"] == "processed"
    assert entry["top_up_id"] == str(top_up_id)
    assert entry["error_code"] is None
    assert entry["processed_at"] == "2026-10-27T09:00:00Z"
    assert body["meta"]["has_next"] is False


def test_log_never_returns_payload(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    # Named without the word under assertion, so the check below is about the document's shape.
    deliver(client, envelope("evt_body", top_up_id=top_up_id))

    with override_settings(ROOT_URLCONF="hub.urls.api"):
        list_response = client.get(LOG, headers=staff_headers("finance_officer"))
        detail = client.get(
            f"{LOG}/{event_id_of('evt_body')}",
            headers=staff_headers("finance_officer"),
        )

    assert detail.status_code == 200, detail.json()
    for response in (list_response, detail):
        text = response.content.decode("utf-8")
        assert "payload" not in text
        # The payer's number is inside the stored envelope; it must not surface here.
        assert "+243810000111" not in text
        assert "payer_msisdn" not in text


def test_an_ignored_delivery_shows_its_reason(client: Client, world: World) -> None:
    deliver(client, envelope("evt_ignored", top_up_id=uuid.uuid4()))

    with override_settings(ROOT_URLCONF="hub.urls.api"):
        response = client.get(LOG, headers=staff_headers("operations_engineer"))

    entry = response.json()["data"][0]
    assert entry["status"] == "ignored"
    assert entry["error_code"] == "unknown_reference"


def test_unknown_event_is_404(client: Client, world: World) -> None:
    with override_settings(ROOT_URLCONF="hub.urls.api"):
        response = client.get(f"{LOG}/{uuid.uuid4()}", headers=staff_headers("finance_officer"))

    assert response.status_code == 404, response.json()


# --- replay ---------------------------------------------------------------------------------


def replay(
    client: Client,
    event_id: UUID,
    *,
    headers: dict[str, str] | None = None,
    key: str | None = None,
) -> Any:
    with override_settings(ROOT_URLCONF="hub.urls.api"):
        return client.post(
            f"{LOG}/{event_id}/replay",
            headers={
                **(headers or staff_headers("finance_officer")),
                "Idempotency-Key": key or str(uuid.uuid4()),
            },
        )


def test_replay_enqueues_job_and_returns_202(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    deliver(client, envelope("evt_replay", top_up_id=top_up_id))
    event_id = event_id_of("evt_replay")

    response = replay(client, event_id)

    assert response.status_code == 202, response.json()
    assert response.json()["data"]["id"] == str(event_id)
    assert response["Location"] == f"/api/v1/webhook-events/{event_id}"
    jobs = job_rows()
    assert len(jobs) == 1
    assert jobs[0]["payload"]["name"] == "payments.replay_event"
    assert jobs[0]["payload"]["payload"] == {"event_id": str(event_id)}


def test_a_retried_replay_with_the_same_key_is_one_job(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    deliver(client, envelope("evt_replay_twice", top_up_id=top_up_id))
    event_id = event_id_of("evt_replay_twice")
    key = str(uuid.uuid4())
    # One token: the key's scope names the caller, so two tokens would be two scopes.
    headers = staff_headers("finance_officer")

    first = replay(client, event_id, headers=headers, key=key)
    second = replay(client, event_id, headers=headers, key=key)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second["Idempotent-Replayed"] == "true"
    assert len(job_rows()) == 1


def test_replay_job_is_noop_when_posting_exists(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    deliver(client, envelope("evt_replay_settled", top_up_id=top_up_id))
    event_id = event_id_of("evt_replay_settled")
    assert wallet_balance(world.customer_id) == 500

    replay(client, event_id)
    run_job(
        "payments.replay_event",
        now=NOW,
        ids=payments_wiring.ids(),
        payload={"event_id": str(event_id)},
    )

    # A settled event is left as it is: no second credit, and no suspense for a settled payment.
    assert wallet_balance(world.customer_id) == 500
    assert transactions_for(top_up_id) == [f"topup:{top_up_id}:confirm"]


def test_replay_credits_ignored_event_after_top_up_appears(client: Client, world: World) -> None:
    """The situation the operation exists for: the delivery arrived before the row did."""
    top_up_id = uuid.uuid4()
    deliver(client, envelope("evt_replay_late_row", top_up_id=top_up_id))
    assert top_up_row_or_none(top_up_id) is None
    event_id = event_id_of("evt_replay_late_row")

    # The row appears (as a repaired initiation would leave it) and the operator replays.
    seed_top_up(world, top_up_id=top_up_id)
    response = replay(client, event_id)
    assert response.status_code == 202, response.json()

    run_job(
        "payments.replay_event",
        now=NOW,
        ids=payments_wiring.ids(),
        payload={"event_id": str(event_id)},
    )

    assert top_up_row(top_up_id)["status"] == "confirmed"
    assert wallet_balance(world.customer_id) == 500


def top_up_row_or_none(top_up_id: UUID) -> Any:
    with connection.cursor() as cursor:
        cursor.execute("SELECT status FROM hub.top_ups WHERE id = %s", [top_up_id])
        row = cursor.fetchone()
    return None if row is None else {"status": row[0]}


def test_replay_requires_a_key(client: Client, world: World) -> None:
    top_up_id = make_top_up(client, world)
    deliver(client, envelope("evt_replay_nokey", top_up_id=top_up_id))

    with override_settings(ROOT_URLCONF="hub.urls.api"):
        response = client.post(
            f"{LOG}/{event_id_of('evt_replay_nokey')}/replay",
            headers=staff_headers("finance_officer"),
        )

    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "idempotency_key_required"
    assert job_rows() == []
