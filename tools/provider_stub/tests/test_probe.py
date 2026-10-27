"""Task T1.5: the synthetic probe — one real signed delivery per interval, failures included.

The probe is what turns "the webhook route is healthy" into something a monitor can see, so its own
tests are about the two ways it could lie: sending a document the hub cannot verify, or stopping the
moment the hub is unhealthy. The clock is injected, so three intervals cost no time, and the fake
hub is the witness of the signatures.
"""

from __future__ import annotations

import json

from provider_stub.probe import PROBE_PROVIDER, probe_once, run

from conftest import PROVIDER_SECRETS, FakeClock, FakeHub

TOP_UP = "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0003"
HUB_URL = "http://hub.test:8080"
SYNTHETIC_SECRET = PROVIDER_SECRETS["synthetic"]


def test_probe_sends_signed_event_every_interval(hub: FakeHub, clock: FakeClock) -> None:
    """Three iterations, three deliveries: distinct event ids, valid signatures, one route."""
    ticks = [clock.now]

    def sleep(seconds: float) -> None:
        clock.advance(seconds)
        ticks.append(clock.now)

    results = run(
        hub_url=HUB_URL,
        region="local",
        secret=SYNTHETIC_SECRET.encode("utf-8"),
        top_up_id=TOP_UP,
        interval=30.0,
        clock=clock,
        sender=hub,
        iterations=3,
        sleep=sleep,
    )

    assert len(results) == 3
    assert len(hub.requests) == 3
    assert len(set(hub.event_ids())) == 3
    assert all(request.signature_is_valid(SYNTHETIC_SECRET) for request in hub.requests)
    assert all(
        request.url == f"{HUB_URL}/api/v1/webhooks/providers/{PROBE_PROVIDER}"
        for request in hub.requests
    )
    for document in hub.documents():
        assert document["type"] == "payment.confirmed"
        assert document["data"]["amount"] == {"amount_minor": 1, "currency": "USD"}
        assert document["data"]["external_reference"] == TOP_UP
    # Two sleeps for three deliveries: the loop does not wait out an interval after the last
    # send, which is what keeps `--iterations 3` a demo rather than a thirty-second pause.
    assert ticks == [ticks[0] + 30 * index for index in range(3)]


def test_probe_records_failures_without_stopping(hub: FakeHub, clock: FakeClock) -> None:
    """A hub answering 503 is data the alert reads; the loop keeps probing."""
    hub.status = 503

    results = run(
        hub_url=HUB_URL,
        region="local",
        secret=SYNTHETIC_SECRET.encode("utf-8"),
        top_up_id=TOP_UP,
        interval=5.0,
        clock=clock,
        sender=hub,
        iterations=2,
        sleep=clock.advance,
    )

    assert [item["status"] for item in results] == [503, 503]
    assert len(hub.requests) == 2


def test_probe_result_is_logged_with_status_and_latency(hub: FakeHub, clock: FakeClock, caplog) -> None:
    """The line the alert parses carries the status and the latency, and no secret."""
    with caplog.at_level("INFO", logger="provider_stub.probe"):
        probe_once(
            hub_url=HUB_URL,
            region="local",
            secret=SYNTHETIC_SECRET.encode("utf-8"),
            top_up_id=TOP_UP,
            clock=clock,
            sender=hub,
        )

    record = next(item for item in caplog.records if item.getMessage() == "probe_result")
    payload = record.__dict__["probe_result"]
    assert payload["status"] == 200
    assert payload["region"] == "local"
    assert isinstance(payload["latency_ms"], float)
    assert SYNTHETIC_SECRET not in json.dumps(payload)


def test_secrets_are_per_provider(hub: FakeHub, clock: FakeClock) -> None:
    """An event for `synthetic` signed with another channel's secret does not verify."""
    probe_once(
        hub_url=HUB_URL,
        region="local",
        secret=SYNTHETIC_SECRET.encode("utf-8"),
        top_up_id=TOP_UP,
        clock=clock,
        sender=hub,
    )
    probe_once(
        hub_url=HUB_URL,
        region="local",
        secret=PROVIDER_SECRETS["mpesa_cd"].encode("utf-8"),
        top_up_id=TOP_UP,
        clock=clock,
        sender=hub,
    )
    synthetic, wrong = hub.requests[0], hub.requests[1]

    assert synthetic.signature_is_valid(SYNTHETIC_SECRET)
    assert not synthetic.signature_is_valid(PROVIDER_SECRETS["mpesa_cd"])
    assert wrong.signature_is_valid(PROVIDER_SECRETS["mpesa_cd"])
    assert not wrong.signature_is_valid(SYNTHETIC_SECRET)
