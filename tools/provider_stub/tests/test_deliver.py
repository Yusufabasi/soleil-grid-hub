"""Task T1.3: the scenario engine — every fault a hub test needs, driven by moving a clock.

These tests never sleep. The scheduler's clock is a fixture, so "late" is `clock.advance(600)` and
"three intervals" is three `run_due` calls; a fault that needed real time would be a fault that
cannot be tested in a loop.

What each mode has to get right is stated once, in `Deliverer.plan_outcome`: a `duplicate` shares
one `event_id` across attempts and signs each one fresh (so the hub's unique key decides, not its
signature), a `drop` sends nothing while leaving the status truthful, and a `reverse_after` leaves
a *new* event id and its own provider transaction behind.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from provider_stub.config import Config
from provider_stub.deliver import Deliverer
from provider_stub.scenarios import ScenarioError, validate_mode
from provider_stub.server import Router
from provider_stub.state import Store

from conftest import PROVIDER_SECRETS, FakeClock, FakeHub

TOP_UP = "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0003"
OTHER_TOP_UP = "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0004"
PAYER = "+243810000111"
OTHER_PAYER = "+243810000222"


def seed(store: Store, reference: str = TOP_UP, payer: str = PAYER, amount_minor: int = 500):  # noqa: ANN201
    return store.add_collection(
        provider="mpesa_cd",
        reference=reference,
        amount_minor=amount_minor,
        currency="USD",
        payer_msisdn=payer,
    )


def scenario(router: Router, mode: str, **params: Any):  # noqa: ANN201
    """Add a scenario that matches everything, with ``params`` as its parameters."""
    return router.add_scenario(mode=mode, params=dict(params), match={})


def test_duplicates_reuse_event_id_and_resign_timestamp(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """Three duplicates are four deliveries: one event id, fresh timestamps, every signature valid."""
    scenario(router, "duplicate", count=3, spread_ms=1100)
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")

    # Each is due at its own moment, so the clock is moved to the next scheduled instant rather
    # than by adding an interval up: accumulated float additions land below a due time and the
    # delivery then waits for a sweep that never comes.
    for _ in range(4):
        due = deliverer.next_due
        assert due is not None
        clock.advance(due - clock.now)
        deliverer.run_due(now=clock.now)

    assert len(hub.requests) == 4
    assert set(hub.event_ids()) == {hub.event_ids()[0]}
    assert len(set(hub.timestamps())) >= 2
    assert hub.all_signatures_valid(PROVIDER_SECRETS["mpesa_cd"])
    assert [recorded.headers["X-Webhook-Id"] for recorded in hub.requests] == [
        hub.event_ids()[0]
    ] * 4
    assert [attempt.attempt for attempt in store.deliveries()] == [1, 2, 3, 4]


def test_duplicate_count_above_twenty_is_refused(router: Router) -> None:
    """The burst is capped: an uncapped count would turn an exactly-once test into a load test."""
    with pytest.raises(ScenarioError):
        router.add_scenario(mode="duplicate", params={"count": 21}, match={})


def test_unknown_mode_or_parameter_is_refused() -> None:
    with pytest.raises(ScenarioError):
        validate_mode("teleport", {})
    with pytest.raises(ScenarioError):
        validate_mode("duplicate", {"counts": 3})


def test_reorder_swaps_two_events(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """The failure event waits for the paired confirmation to be delivered, then goes first."""
    second = seed(store, reference=OTHER_TOP_UP, payer=OTHER_PAYER)
    first = seed(store, reference=TOP_UP, payer=PAYER)
    # The failure of `second` is told to wait for `first`, so it can only be delivered after it.
    router.add_scenario(
        mode="reorder",
        params={"with": first.provider_reference},
        match={"reference": OTHER_TOP_UP},
    )
    deliverer.complete(second.provider, second.provider_reference, outcome="failed")

    # Nothing is deliverable yet: the only pending delivery is waiting.
    deliverer.run_due(now=clock.now)
    assert hub.requests == []

    deliverer.complete(first.provider, first.provider_reference, outcome="successful")
    deliverer.run_due(now=clock.now)

    assert len(hub.requests) == 2
    order = [document["data"]["external_reference"] for document in hub.documents()]
    assert order[0] == TOP_UP
    assert hub.documents()[1]["type"] == "payment.failed"


def test_late_delivers_after_delay(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """`late` is a delivery that arrives after the hub's window — here, after 600 s of clock."""
    scenario(router, "late", seconds=600)
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")

    deliverer.run_due(now=clock.now)
    assert hub.requests == []

    clock.advance(600)
    deliverer.run_due(now=clock.now)

    assert len(hub.requests) == 1
    assert store.deliveries()[0].mode == "late"


def test_delay_postpones_the_delivery(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    scenario(router, "delay", seconds=30)
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")

    deliverer.run_due(now=clock.now)
    assert hub.requests == []
    clock.advance(30)
    deliverer.run_due(now=clock.now)
    assert len(hub.requests) == 1


def test_bad_signature_fails_verification(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """The bytes are delivered with a signature the real secret does not produce."""
    scenario(router, "bad_signature")
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")
    deliverer.run_due(now=clock.now)

    sent = hub.requests[0]
    assert not sent.signature_is_valid(PROVIDER_SECRETS["mpesa_cd"])
    assert json.loads(sent.body)["type"] == "payment.confirmed"


def test_wrong_secret_uses_another_providers_secret(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """A correctly formed signature under the wrong key: the hub must reject it as a forgery."""
    scenario(router, "wrong_secret", secret_provider="orange_cd")
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")
    deliverer.run_due(now=clock.now)

    sent = hub.requests[0]
    assert sent.signature_is_valid(PROVIDER_SECRETS["orange_cd"])
    assert not sent.signature_is_valid(PROVIDER_SECRETS["mpesa_cd"])


def test_stale_signature_timestamp_older_than_300s(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """A four-hour-old timestamp, correctly signed: only the window can reject this one."""
    scenario(router, "stale_signature", age_seconds=14_400)
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")
    deliverer.run_due(now=clock.now)

    sent = hub.requests[0]
    age = int(clock.now) - int(sent.headers["X-Webhook-Timestamp"])
    assert age > 300
    assert sent.signature_is_valid(PROVIDER_SECRETS["mpesa_cd"])


def test_wrong_amount_changes_body_and_resigns(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """A mutated body with a *valid* signature: the hub must reject on its business rules."""
    scenario(router, "wrong_amount", amount_minor=999)
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")
    deliverer.run_due(now=clock.now)

    sent = hub.requests[0]
    assert sent.signature_is_valid(PROVIDER_SECRETS["mpesa_cd"])
    assert sent.document()["data"]["amount"]["amount_minor"] == 999


def test_wrong_currency_and_payer_are_valid_signatures_over_mutated_bodies(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    router.add_scenario(mode="wrong_currency", params={"currency": "CDF"}, match={"reference": TOP_UP})
    router.add_scenario(mode="wrong_payer", params={"payer_msisdn": OTHER_PAYER}, match={})
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")
    deliverer.run_due(now=clock.now)

    # Last matching scenario wins: the payer one applies, and the currency is untouched.
    sent = hub.requests[0]
    assert sent.signature_is_valid(PROVIDER_SECRETS["mpesa_cd"])
    assert sent.document()["data"]["payer_msisdn"] == OTHER_PAYER
    assert sent.document()["data"]["amount"]["currency"] == "USD"


def test_drop_sends_nothing_but_status_pull_reports_successful(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """The money moved and the webhook never came — slice 04's `pending_unknown` case."""
    scenario(router, "drop")
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")
    deliverer.run_due(now=clock.now)

    assert hub.requests == []
    assert store.get(created.provider, created.provider_reference).status == "successful"


def test_scenario_matching_by_msisdn(store: Store, router: Router) -> None:
    """A scenario that names a payer applies to that payer only."""
    router.add_scenario(
        mode="failure", params={"failure_code": "insufficient_funds"}, match={"payer_msisdn": PAYER}
    )
    mine = seed(store, reference=TOP_UP, payer=PAYER)
    theirs = seed(store, reference=OTHER_TOP_UP, payer=OTHER_PAYER)

    # The same chooser the delivery scheduler consults: one answer for both callers.
    assert router._scenario_for(mine) is not None
    assert router._scenario_for(mine).mode == "failure"
    assert router._scenario_for(theirs) is None


def test_a_failure_scenario_delivers_payment_failed(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    scenario(router, "failure", failure_code="insufficient_funds")
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="failed")
    deliverer.run_due(now=clock.now)

    document = hub.documents()[0]
    assert document["type"] == "payment.failed"
    assert document["data"]["failure_code"] == "insufficient_funds"
    assert store.get(created.provider, created.provider_reference).status == "failed"


def test_reverse_after_sends_reversed_event_with_new_event_id_and_new_provider_txn(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    """A reversal is its own event about the same charge, keyed `<reference>-R` by the hub."""
    scenario(router, "reverse_after", seconds=60)
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")

    deliverer.run_due(now=clock.now)
    first = hub.documents()[0]
    assert first["type"] == "payment.confirmed"
    assert store.get(created.provider, created.provider_reference).status == "successful"

    clock.advance(60)
    deliverer.run_due(now=clock.now)

    assert len(hub.requests) == 2
    reversal = hub.documents()[1]
    assert reversal["type"] == "payment.reversed"
    assert reversal["event_id"] != first["event_id"]
    # The same charge: the hub keys the reversal as its own provider transaction with `-R`,
    # which it can only do if the envelope names the confirmation's reference.
    assert reversal["data"]["provider_reference"] == first["data"]["provider_reference"]
    assert (
        store.get(created.provider, created.provider_reference).reversal_reference
        == f"{first['data']['provider_reference']}-R"
    )
    assert store.get(created.provider, created.provider_reference).status == "reversed"


def test_slow_and_error_initiate_are_control_modes(router: Router) -> None:
    """`error_initiate` answers a status instead of creating a collection."""
    router.add_scenario(mode="error_initiate", params={"status": 503}, match={})
    body = json.dumps(
        {
            "reference": TOP_UP,
            "amount": {"amount_minor": 500, "currency": "USD"},
            "payer_msisdn": PAYER,
        }
    ).encode()

    response = router.handle("POST", "/providers/mpesa_cd/collections", {}, body)

    assert response.status == 503
    assert router.store.collections() == []


def test_delivery_control_endpoint_reports_attempts(
    config: Config, store: Store, deliverer: Deliverer, router: Router, clock: FakeClock, hub: FakeHub
) -> None:
    created = seed(store)
    deliverer.complete(created.provider, created.provider_reference, outcome="successful")
    deliverer.run_due(now=clock.now)

    response = router.handle(
        "GET", "/control/deliveries", {"Authorization": "Bearer control-test-token"}, b""
    )
    listed = json.loads(response.body)["deliveries"]

    assert len(listed) == 1
    assert listed[0]["hub_status"] == 200
    assert listed[0]["provider"] == "mpesa_cd"
