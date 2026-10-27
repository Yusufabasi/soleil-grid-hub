"""Task T1.1: the provider API, the journal, and the restart that has to keep them consistent.

The interesting assertions are about *what survives*: a repeated initiation, a conflict on a reused
reference, and — the one that matters most — a status pull after a restart still agreeing with the
webhook the double already sent. That last one is why the journal exists at all: a double that
forgot its collections would answer `not_found` for money the hub has credited.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from provider_stub.config import Config, ConfigError, load
from provider_stub.deliver import Deliverer
from provider_stub.server import Router
from provider_stub.state import Store

# `conftest` is importable as a top-level module: the tests directory has no `__init__.py`, so
# pytest puts it on `sys.path` and every test module reaches the helpers by name.
from conftest import ENVIRON, FakeClock, FakeHub, build_config, start_stub

TOP_UP = "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0003"
PAYER = "+243810000111"


def collection_body(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "reference": TOP_UP,
        "amount": {"amount_minor": 500, "currency": "USD"},
        "payer_msisdn": PAYER,
    }
    document.update(overrides)
    return document


def initiate(router: Router, **overrides: object):  # noqa: ANN201 - a small test helper
    payload = json.dumps(collection_body(**overrides)).encode("utf-8")
    return router.handle("POST", "/providers/mpesa_cd/collections", {}, payload)


def document(response) -> dict:  # noqa: ANN001 - a small test helper
    return json.loads(response.body)


def test_initiate_is_idempotent_on_reference(router: Router) -> None:
    """Two posts naming one reference are one collection, and say the same provider reference."""
    first = initiate(router)
    second = initiate(router)

    assert first.status == 202
    assert second.status == 202
    assert document(first)["provider_reference"] == document(second)["provider_reference"]
    assert document(first)["status"] == "pending"
    assert len(router.store.collections("mpesa_cd")) == 1


def test_initiate_conflicting_amount_is_409(router: Router) -> None:
    """A different amount under one reference is a reused idempotency key, not a retry."""
    initiate(router)

    conflicting = initiate(router, amount={"amount_minor": 900, "currency": "USD"})

    assert conflicting.status == 409
    assert document(conflicting)["error"]["code"] == "reference_conflict"
    assert len(router.store.collections("mpesa_cd")) == 1


def test_status_pending_then_successful(
    router: Router, deliverer: Deliverer, clock: FakeClock, hub: FakeHub
) -> None:
    """With automatic completion off, only the control API settles a collection."""
    created = document(initiate(router))
    reference = created["provider_reference"]

    pending = router.handle("GET", f"/providers/mpesa_cd/collections/{reference}", {}, b"")
    assert pending.status == 200
    assert document(pending)["status"] == "pending"

    completed = router.handle(
        "POST",
        f"/control/collections/{reference}/complete",
        {"Authorization": "Bearer control-test-token"},
        json.dumps({"outcome": "successful"}).encode(),
    )
    assert completed.status == 202

    deliverer.run_due(now=clock.now)
    settled = router.handle("GET", f"/providers/mpesa_cd/collections/{reference}", {}, b"")
    assert document(settled)["status"] == "successful"
    assert len(hub.requests) == 1


def test_status_matches_last_webhook_after_restart(journal: Path, clock: FakeClock, hub: FakeHub) -> None:
    """A restart on the same journal keeps the collection and the status it reported by webhook."""
    config = build_config(journal)
    store = Store(config.journal_path, clock=clock)
    deliverer = Deliverer(config=config, store=store, sender=hub, clock=clock)
    first = start_stub(config, store, deliverer, clock)
    try:
        created = json.loads(
            first.request("POST", "/providers/mpesa_cd/collections", collection_body())[1]
        )
        reference = created["provider_reference"]
        first.request(
            "POST",
            f"/control/collections/{reference}/complete",
            {"outcome": "successful"},
            token="control-test-token",
        )
        deliverer.run_due(now=clock.now)
        sent = first.request("GET", "/control/deliveries", token="control-test-token")
        assert len(json.loads(sent[1])["deliveries"]) == 1
    finally:
        first.stop()

    # Same journal, a brand-new process: the store replays what was written.
    restarted_store = Store(config.journal_path, clock=clock)
    restarted_deliverer = Deliverer(
        config=config, store=restarted_store, sender=FakeHub(), clock=clock
    )
    second = start_stub(config, restarted_store, restarted_deliverer, clock)
    try:
        status = second.request("GET", f"/providers/mpesa_cd/collections/{reference}")
        assert status[0] == 200
        assert json.loads(status[1])["status"] == "successful"
        # And the deliveries the first process recorded are still reported.
        again = second.request("GET", "/control/deliveries", token="control-test-token")
        assert len(json.loads(again[1])["deliveries"]) == 1
    finally:
        second.stop()


def test_the_provider_api_waits_only_when_latency_is_simulated(
    config: Config, store: Store, deliverer: Deliverer, clock: FakeClock
) -> None:
    """The double is instant unless `STUB_SIMULATE_LATENCY` asks for the provider's latency.

    The default matters more than the simulation: a suite whose every provider call waited 400 ms
    would be slow for no evidence. Both sides are asserted here without living through either.
    """
    waited: list[float] = []
    quiet = Router(config=config, store=store, deliverer=deliverer, clock=clock, sleep=waited.append)

    assert initiate(quiet).status == 202
    assert len(quiet.store.collections("mpesa_cd")) == 1
    assert quiet.handle("GET", "/health", {}, b"").status == 200
    assert waited == []

    slow_config = build_config(
        config.journal_path,
        STUB_SIMULATE_LATENCY="1",
        STUB_LATENCY_P50_MS="400",
        STUB_LATENCY_P99_MS="3000",
    )
    slow_store = Store(slow_config.journal_path, clock=clock)
    slow = Router(
        config=slow_config,
        store=slow_store,
        deliverer=Deliverer(config=slow_config, store=slow_store, sender=FakeHub(), clock=clock),
        clock=clock,
        sleep=waited.append,
    )
    # The control API and the health check never wait: an operator must not queue behind a
    # simulation to learn whether the double is alive.
    token = {"Authorization": "Bearer control-test-token"}
    assert slow.handle("GET", "/health", {}, b"").status == 200
    assert slow.handle("GET", "/control/deliveries", token, b"").status == 200
    assert waited == []

    for index in range(20):
        assert initiate(slow, reference=f"{TOP_UP[:-4]}{index:04d}").status == 202

    assert len(waited) == 20
    # Every draw is inside the band the two configured percentiles imply, and they are not all the
    # same number: a constant would not be a distribution. The middle of twenty draws sits near
    # p50 (0.4 s), and the seeded source means the same twenty come back on a rerun.
    assert all(0.0 < seconds <= 30.0 for seconds in waited)
    assert len(set(waited)) > 15
    assert 0.15 < sorted(waited)[9] < 1.2


def test_missing_secret_refuses_to_start() -> None:
    """No secret has a default: an incomplete environment stops the process at start-up."""
    incomplete = {key: value for key, value in ENVIRON.items() if key != "STUB_SECRET_ORANGE_CD"}

    with pytest.raises(ConfigError) as raised:
        load({**incomplete, "STUB_JOURNAL_PATH": "/tmp/does-not-matter.jsonl"})

    assert "STUB_SECRET_ORANGE_CD" in str(raised.value)
    # The message names the variable; it must never name a value.
    assert "secret" in str(raised.value).lower()


def test_missing_control_token_refuses_to_start() -> None:
    incomplete = {key: value for key, value in ENVIRON.items() if key != "STUB_CONTROL_TOKEN"}

    with pytest.raises(ConfigError):
        load({**incomplete, "STUB_JOURNAL_PATH": "/tmp/does-not-matter.jsonl"})


def test_unknown_provider_is_404(router: Router) -> None:
    """A channel the double does not know must not create state under another one's secret."""
    response = router.handle(
        "POST",
        "/providers/not_a_channel/collections",
        {},
        json.dumps(collection_body()).encode(),
    )

    assert response.status == 404
    assert document(response)["error"]["code"] == "unknown_provider"
    assert router.store.collections() == []


def test_control_api_requires_the_token(router: Router) -> None:
    """Every control call is token-guarded; the provider API (over the network) is not."""
    unauthorised = router.handle(
        "POST", "/control/scenarios", {}, json.dumps({"mode": "success"}).encode()
    )
    wrong = router.handle(
        "POST",
        "/control/scenarios",
        {"Authorization": "Bearer not-the-token"},
        json.dumps({"mode": "success"}).encode(),
    )

    assert unauthorised.status == 401
    assert wrong.status == 401
    assert router.scenarios == []


def test_health_reports_the_registered_providers(router: Router) -> None:
    response = router.handle("GET", "/health", {}, b"")

    assert response.status == 200
    assert set(document(response)["providers"]) == {
        "mpesa_cd",
        "airtel_cd",
        "orange_cd",
        "synthetic",
    }


def test_invalid_bodies_are_refused(router: Router) -> None:
    """Validation happens before any state exists, so a bad request leaves nothing behind."""
    assert router.handle("POST", "/providers/mpesa_cd/collections", {}, b"{not json").status == 400
    assert initiate(router, reference="").status == 422
    assert initiate(router, amount={"amount_minor": 0, "currency": "USD"}).status == 422
    assert initiate(router, amount={"amount_minor": 500, "currency": "EUR"}).status == 422
    assert initiate(router, payer_msisdn="0810000111").status == 422
    assert router.store.collections() == []
