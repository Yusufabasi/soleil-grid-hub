"""The sweepers: a waiting top-up must not wait forever (Task 04.10).

Each sweeper is invoked the way the deployment invokes it — by name, through the kernel's job
registry — so these tests also pin the *registration*: a job nobody can ask for is a job that
never runs, which is exactly the defect slice 03 found in its approval executors.

What the sweepers must never do is decide money. A poll that finds a successful charge does not
credit it (the provider owes the webhook), and `pending_unknown` never auto-expires: the provider
may still be holding the money, and hiding the row would hide the charge.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.test import Client

from hub.payments import wiring as payments_wiring
from hub.payments.application.ports import CollectionAck, ProviderTxnStatus
from hub.shared.jobs import resolve_handler, run_job
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
    webhook_pool,
    wired_gateway,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

LONG_AGO = NOW - timedelta(minutes=5)
BEYOND_GRACE = NOW - timedelta(minutes=31)


def run(name: str) -> Any:
    """Run one registered job the way `run_scheduler --once` does."""
    assert resolve_handler(name) is not None, f"{name} is not registered"
    return run_job(name, now=NOW, ids=payments_wiring.ids())


def provider_status(reference: str, status: str, *, amount_minor: int = 500) -> None:
    wired_gateway().statuses[reference] = ProviderTxnStatus(
        status=status,  # type: ignore[arg-type]
        amount=Money(amount_minor, "USD"),
    )


# --- payments.expire_top_ups ----------------------------------------------------------------


def test_the_three_sweepers_are_registered() -> None:
    for name in (
        "payments.expire_top_ups",
        "payments.retry_initiation",
        "payments.sweep_pending_unknown",
    ):
        assert resolve_handler(name) is not None


def test_expire_top_ups_moves_initiated_and_pending_only(world: World) -> None:
    initiated = seed_top_up(world, status="initiated", expires_at=LONG_AGO)
    pending = seed_top_up(world, status="pending", expires_at=LONG_AGO)
    unknown = seed_top_up(world, status="pending_unknown", expires_at=LONG_AGO)

    outcome = run("payments.expire_top_ups")

    assert outcome.changed == 2
    assert top_up_row(initiated)["status"] == "expired"
    assert top_up_row(pending)["status"] == "expired"
    # The provider may still be holding this money: expiring it would hide the charge.
    assert top_up_row(unknown)["status"] == "pending_unknown"


def test_expire_top_ups_leaves_a_waiting_window_alone(world: World) -> None:
    waiting = seed_top_up(world, status="pending", expires_at=NOW + timedelta(minutes=5))

    outcome = run("payments.expire_top_ups")

    assert outcome.changed == 0
    assert top_up_row(waiting)["status"] == "pending"


# --- payments.retry_initiation ---------------------------------------------------------------


def test_retry_initiation_for_initiated_older_than_60s(world: World) -> None:
    top_up_id = seed_top_up(world, status="initiated", initiated_at=NOW - timedelta(minutes=2))
    gateway = wired_gateway()
    gateway.answer = CollectionAck(accepted=True, provider_txn_id="txn-retried")

    outcome = run("payments.retry_initiation")

    assert outcome.changed == 1
    assert top_up_row(top_up_id)["status"] == "pending"
    # The reference is the top-up's own id, so a second attempt is the same collection.
    assert [call.reference for call in gateway.calls] == [str(top_up_id)]


def test_retry_initiation_skips_a_young_row(world: World) -> None:
    top_up_id = seed_top_up(world, status="initiated", initiated_at=NOW - timedelta(seconds=10))
    gateway = wired_gateway()

    outcome = run("payments.retry_initiation")

    assert outcome.changed == 0
    assert gateway.calls == []
    assert top_up_row(top_up_id)["status"] == "initiated"


def test_retry_initiation_marks_an_unanswered_call_unknown(world: World) -> None:
    top_up_id = seed_top_up(world, status="initiated", initiated_at=NOW - timedelta(minutes=2))
    wired_gateway().failure = TimeoutError("no answer")

    run("payments.retry_initiation")

    # `initiated -> pending_unknown` is not an edge the guard has; the sweeper walks the
    # documented two-step path through `pending`.
    assert top_up_row(top_up_id)["status"] == "pending_unknown"


# --- payments.sweep_pending_unknown ----------------------------------------------------------


def test_pending_unknown_poll_pending_returns_to_pending(world: World) -> None:
    top_up_id = seed_top_up(world, status="pending_unknown", updated_at=BEYOND_GRACE)
    provider_status(str(top_up_id), "pending")

    outcome = run("payments.sweep_pending_unknown")

    assert outcome.changed == 1
    assert top_up_row(top_up_id)["status"] == "pending"


def test_pending_unknown_poll_failed_sets_failed(world: World) -> None:
    top_up_id = seed_top_up(world, status="pending_unknown", updated_at=BEYOND_GRACE)
    provider_status(str(top_up_id), "failed")

    run("payments.sweep_pending_unknown")

    assert top_up_row(top_up_id)["status"] == "failed"


def test_pending_unknown_poll_success_waits_for_webhook_then_raises_exception_after_30_minutes(
    world: World,
) -> None:
    top_up_id = seed_top_up(world, status="pending_unknown", updated_at=BEYOND_GRACE)
    provider_status(str(top_up_id), "successful")

    run("payments.sweep_pending_unknown")

    row = top_up_row(top_up_id)
    # No credit: the provider still owes the webhook, and a credit here would be a second
    # path to the same money.
    assert row["status"] == "pending_unknown"
    assert row["status_pull_at"] == NOW
    raised = outbox_rows("reconciliation.exception")
    assert len(raised) == 1
    assert raised[0]["payload"]["reason_code"] == "pull_confirmed_no_webhook"
    assert raised[0]["payload"]["kind"] == "statement_only"

    # A second run does not raise the same exception twice: the row left the claim window.
    run("payments.sweep_pending_unknown")
    assert len(outbox_rows("reconciliation.exception")) == 1


def test_pending_unknown_poll_leaves_a_row_inside_the_grace_window(world: World) -> None:
    top_up_id = seed_top_up(world, status="pending_unknown", updated_at=NOW - timedelta(minutes=5))
    provider_status(str(top_up_id), "failed")

    outcome = run("payments.sweep_pending_unknown")

    assert outcome.changed == 0
    assert top_up_row(top_up_id)["status"] == "pending_unknown"


def test_pending_unknown_poll_does_nothing_when_the_provider_is_silent(world: World) -> None:
    top_up_id = seed_top_up(world, status="pending_unknown", updated_at=BEYOND_GRACE)

    def silent(provider_txn_id: str) -> ProviderTxnStatus:
        del provider_txn_id
        raise TimeoutError("still no answer")

    wired_gateway().fetch_status = silent

    outcome = run("payments.sweep_pending_unknown")

    assert outcome.changed == 0
    assert top_up_row(top_up_id)["status"] == "pending_unknown"
    assert event_rows() == []


def test_a_swept_top_up_still_credits_when_the_webhook_arrives(
    client: Client, world: World
) -> None:
    """Expiry is not the end of the money: the delivery that follows still credits once."""
    top_up_id = make_top_up(client, world)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE hub.top_ups SET expires_at = %s WHERE id = %s", [LONG_AGO, top_up_id]
        )
    run("payments.expire_top_ups")
    assert top_up_row(top_up_id)["status"] == "expired"

    with webhook_pool():
        response = deliver(client, envelope("evt_after_sweep", top_up_id=top_up_id))

    assert response.status_code == 200, response.json()
    assert top_up_row(top_up_id)["status"] == "confirmed"
