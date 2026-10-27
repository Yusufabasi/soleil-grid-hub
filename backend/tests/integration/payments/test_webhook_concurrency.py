"""Fifty identical deliveries, one posting (Task 04.6, invariant 3, NFR-05).

The property under test is the database's, not Python's: the unique indexes on
``(provider, event_id)`` and ``(provider, provider_txn_id)`` decide the winner, and
``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` turns the losers into ``duplicate`` answers
without a second effect. The Redis hint is deleted between some deliveries to prove the same
thing with the shortcut gone.

These tests run with real commits (``transaction=True``) because uncommitted work is invisible to
another connection, which is the whole point of a race. They therefore clean up after themselves:
Django's ``flush`` does not know this schema's unmanaged tables, and a leftover event row would
break the next module's exact counts.

Latency is measured and reported but not asserted here: the 300 ms ack budget is a property of a
warm deployed stack (Task 04.11's smoke test), and asserting it on a loaded CI box would fail for
a reason that has nothing to do with exactly-once.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest
from django.db import connections
from django.test import Client

from hub.customers import wiring as customers_wiring
from hub.identity import wiring as identity_wiring
from hub.payments import wiring as payments_wiring
from hub.payments.application.confirm_payment import ConfirmPayment
from support.cleanup import purge_world
from support.payments import NOW, World
from support.webhooks import (
    PROVIDER,
    deliver,
    envelope,
    gateway,
    make_top_up,
    outbox_rows,
    seed_top_up,
    top_up_row,
    transactions_for,
    wallet_balance,
    webhook_pool,
)

# `concurrency` is its own marker because this module is the one the verification script runs
# five times in a row (nightly CI runs twenty): a race that passes once has proved very little.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.concurrency,
    pytest.mark.django_db(transaction=True),
]

DELIVERIES = 50
RACES = 30


@pytest.fixture(autouse=True)
def _isolation() -> Iterator[None]:
    """The clock and the cached adapters are per test, as in the transactional modules."""
    yield
    payments_wiring.reset()
    customers_wiring.reset()
    identity_wiring.reset()


@pytest.fixture
def committed(world: World) -> Iterator[World]:
    try:
        yield world
    finally:
        purge_world(world)


def run_parallel(work: Any, count: int) -> list[Any]:
    """Run ``work(index)`` on ``count`` threads released together by a barrier."""
    barrier = threading.Barrier(count)
    results: list[Any] = [None] * count
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            results[index] = work(index)
        except BaseException as exc:  # noqa: BLE001 - reported below, never swallowed
            errors.append(exc)
        finally:
            # A thread's connection is not the main thread's; leaving it open would leak a
            # server connection per race and eventually exhaust PostgreSQL's limit.
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    if errors:
        raise AssertionError(f"{len(errors)} thread(s) failed: {errors[:3]}")
    return results


def signed_body(document: dict[str, Any]) -> tuple[dict[str, str], bytes]:
    headers, body = gateway().signed(document, timestamp=str(int(NOW.timestamp())))
    return dict(headers), bytes(body)


def test_fifty_parallel_identical_webhooks_credit_once(committed: World) -> None:
    top_up_id = make_top_up(Client(), committed)
    document = envelope("evt_race", top_up_id=top_up_id)
    headers, body = signed_body(document)

    def send(index: int) -> str:
        del index
        response = Client().post(
            f"/api/v1/webhooks/providers/{PROVIDER}",
            data=body,
            content_type="application/json",
            headers=headers,
        )
        assert response.status_code == 200, response.content
        return str(response.json()["data"]["status"])

    with webhook_pool():
        statuses = run_parallel(send, DELIVERIES)

    assert statuses.count("accepted") == 1
    assert statuses.count("duplicate") == DELIVERIES - 1
    row = top_up_row(top_up_id)
    assert row["status"] == "confirmed"
    assert wallet_balance(committed.customer_id) == 500
    assert transactions_for(top_up_id) == [f"topup:{top_up_id}:confirm"]
    assert len(outbox_rows("payment.confirmed")) == 1


def test_parallel_same_txn_different_event_ids_credit_once(committed: World) -> None:
    """Twenty event ids for one provider charge: the second unique key decides, not the first."""
    top_up_id = make_top_up(Client(), committed)
    reference = "MP-one-charge"
    bodies = [
        signed_body(
            envelope(
                f"evt_same_txn_{index}",
                top_up_id=top_up_id,
                provider_reference=reference,
            )
        )
        for index in range(20)
    ]

    def send(index: int) -> str:
        headers, body = bodies[index]
        response = Client().post(
            f"/api/v1/webhooks/providers/{PROVIDER}",
            data=body,
            content_type="application/json",
            headers=headers,
        )
        assert response.status_code == 200, response.content
        return str(response.json()["data"]["status"])

    with webhook_pool():
        statuses = run_parallel(send, 20)

    assert statuses.count("accepted") == 1
    assert wallet_balance(committed.customer_id) == 500
    assert len(transactions_for(top_up_id)) == 1


def test_parallel_confirm_and_failed_never_downgrade(committed: World) -> None:
    """A confirmation racing its own failure: the money wins, or the row is failed — never both."""
    top_up_id = make_top_up(Client(), committed)
    confirmation = signed_body(envelope("evt_win_confirm", top_up_id=top_up_id))
    failure = signed_body(
        envelope(
            "evt_win_failed",
            top_up_id=top_up_id,
            event_type="payment.failed",
            failure_code="declined",
        )
    )

    def send(index: int) -> str:
        headers, body = confirmation if index % 2 == 0 else failure
        response = Client().post(
            f"/api/v1/webhooks/providers/{PROVIDER}",
            data=body,
            content_type="application/json",
            headers=headers,
        )
        assert response.status_code == 200, response.content
        return str(response.json()["data"]["status"])

    with webhook_pool():
        statuses = run_parallel(send, RACES)

    assert statuses.count("accepted") == 2
    row = top_up_row(top_up_id)
    balance = wallet_balance(committed.customer_id)
    postings = transactions_for(top_up_id)
    if row["status"] == "confirmed":
        assert balance == 500
        assert len(postings) == 1
    else:
        # The failure won the race. The money still arrived, so it is not dropped: it is parked
        # in suspense under the documented `top_up_failed_state` reason, and the wallet is
        # untouched. What must never happen is a wallet credit on a failed row.
        assert row["status"] == "failed"
        assert balance == 0
        assert all(":suspense:" in key for key in postings), postings


def test_redis_flush_between_deliveries_still_one_effect(
    committed: World, redis_store: Any
) -> None:
    """The hint is an optimisation: losing it costs a round trip, never a second posting."""
    top_up_id = make_top_up(Client(), committed)
    document = envelope("evt_flush", top_up_id=top_up_id)

    with webhook_pool():
        first = deliver(Client(), document)
        redis_store.flushdb()
        second = deliver(Client(), document)

    assert first.json()["data"]["status"] == "accepted"
    assert second.json()["data"]["status"] == "duplicate"
    assert wallet_balance(committed.customer_id) == 500
    assert len(transactions_for(top_up_id)) == 1


class _RaiseAfterLedger:
    """A crash inside the transaction, after the ledger write and before the commit."""

    def after_ledger_post(self) -> None:
        raise RuntimeError("simulated crash before commit")

    def after_commit(self) -> None:  # pragma: no cover - not reached in this scenario
        return None


class _RaiseAfterCommit:
    """A crash after the commit: the effect is real, the caller never hears about it."""

    def after_ledger_post(self) -> None:
        return None

    def after_commit(self) -> None:
        raise RuntimeError("simulated crash after commit")


def composed(fault: Any) -> ConfirmPayment:
    """The real `ConfirmPayment`, with a fault hook a test controls."""
    return ConfirmPayment(
        clock=payments_wiring.clock(),
        ids=payments_wiring.ids(),
        provider=payments_wiring.gateway,
        top_ups=payments_wiring.top_up_repository(),
        events=payments_wiring.event_repository(),
        ledger=payments_wiring.ledger(),
        eligibility=payments_wiring.eligibility(),
        cache=payments_wiring.cache(),
        unit_of_work=payments_wiring.unit_of_work,
        pull_thresholds=payments_wiring.pull_thresholds(),
        fault=fault,
    )


def test_crash_before_commit_leaves_no_partial_effect(
    committed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    top_up_id = make_top_up(Client(), committed)
    document = envelope("evt_crash_before", top_up_id=top_up_id)
    headers, body = signed_body(document)

    monkeypatch.setattr(payments_wiring, "confirm_payment", lambda: composed(_RaiseAfterLedger()))
    with webhook_pool():
        crashed = Client(raise_request_exception=False).post(
            f"/api/v1/webhooks/providers/{PROVIDER}",
            data=body,
            content_type="application/json",
            headers=headers,
        )

    assert crashed.status_code == 500
    assert outbox_rows("payment.confirmed") == []
    assert transactions_for(top_up_id) == []
    assert top_up_row(top_up_id)["status"] == "pending"
    assert wallet_balance(committed.customer_id) == 0

    # The provider retries, as it would: the retry is the first *and* only effect.
    monkeypatch.undo()
    payments_wiring.reset()
    with webhook_pool():
        retried = deliver(Client(), document)

    assert retried.status_code == 200, retried.json()
    assert retried.json()["data"]["status"] == "accepted"
    assert wallet_balance(committed.customer_id) == 500
    assert len(transactions_for(top_up_id)) == 1


def test_crash_after_commit_before_response_retry_is_noop(
    committed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    top_up_id = make_top_up(Client(), committed)
    document = envelope("evt_crash_after", top_up_id=top_up_id)
    headers, body = signed_body(document)

    monkeypatch.setattr(payments_wiring, "confirm_payment", lambda: composed(_RaiseAfterCommit()))
    with webhook_pool():
        crashed = Client(raise_request_exception=False).post(
            f"/api/v1/webhooks/providers/{PROVIDER}",
            data=body,
            content_type="application/json",
            headers=headers,
        )

    # The effect committed before the crash; the provider did not hear the answer.
    assert crashed.status_code == 500
    assert wallet_balance(committed.customer_id) == 500

    monkeypatch.undo()
    payments_wiring.reset()
    with webhook_pool():
        retried = deliver(Client(), document)

    assert retried.json()["data"]["status"] == "duplicate"
    assert wallet_balance(committed.customer_id) == 500
    assert len(transactions_for(top_up_id)) == 1


def test_a_second_distinct_charge_is_not_a_duplicate(committed: World) -> None:
    """The unique key is the charge, not the top-up: a real second payment is recorded."""
    top_up_id = make_top_up(Client(), committed)

    with webhook_pool():
        first = deliver(Client(), envelope("evt_two_a", top_up_id=top_up_id))
        second = deliver(Client(), envelope("evt_two_b", top_up_id=top_up_id))

    assert first.json()["data"]["status"] == "accepted"
    assert second.json()["data"]["status"] == "accepted"
    # The second delivery is a mismatch for settled money, so it is parked, not credited.
    assert wallet_balance(committed.customer_id) == 500
    assert len([key for key in transactions_for(top_up_id) if ":suspense:" in key]) == 1


def test_threads_leave_no_open_connection(committed: World) -> None:
    """A suite that leaks a connection per thread fails the next suite, not this one."""
    top_up_id = seed_top_up(committed, status="pending")
    document = envelope("evt_connections", top_up_id=top_up_id)
    headers, body = signed_body(document)

    def send(index: int) -> int:
        del index
        response = Client().post(
            f"/api/v1/webhooks/providers/{PROVIDER}",
            data=body,
            content_type="application/json",
            headers=headers,
        )
        return response.status_code

    with webhook_pool():
        codes = run_parallel(send, 10)

    assert set(codes) == {200}
    assert top_up_row(top_up_id)["status"] == "confirmed"
