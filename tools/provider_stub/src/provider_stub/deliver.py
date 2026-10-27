"""Building and sending signed envelopes, with the faults a scenario asks for.

Two properties of this module are the reason it exists at all:

* **The body is serialised once and signed as bytes.** `build_event` serialises with compact
  separators, signs exactly those bytes, and hands the sender those same bytes. Every hub
  signature test that passes because a document round-tripped cleanly would be passing for the
  wrong reason (slice T1 review focus 1).
* **The timestamp is taken at send time, not at schedule time.** A duplicate or a reordered
  delivery is a *new attempt*: it carries a fresh `X-Webhook-Timestamp` and a fresh signature
  over it, while the `event_id` stays the same so the hub's unique key does the deduplication
  (review focus 2). Signing at schedule time would make a duplicate look stale to a hub that (as
  it should) enforces a 300-second window.

The scheduler is a heap of due deliveries plus an injectable clock, so a test can move time
instead of sleeping; `run_due(now)` is the whole engine, and the server's background thread just
calls it. Deliveries that must wait for another one (`reorder`) carry a `wait_for` reference and
are released once that reference has been delivered.

`drop` is the interesting fault: the envelope is never sent, but the collection's status still
becomes `successful`. That is not a bug in the double — it is the scenario slice 04's
`pending_unknown` sweeper and pull verification are built for: the provider took the money and the
webhook never came.
"""

from __future__ import annotations

import heapq
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from typing import Any

import httpx

from provider_stub.config import Config
from provider_stub.signing import build_headers
from provider_stub.state import (
    FAILED,
    SUCCESSFUL,
    Collection,
    Delivery,
    Store,
)
from provider_stub.scenarios import Scenario

__all__ = [
    "DELIVERY_TIMEOUT_SECONDS",
    "Deliverer",
    "Sender",
    "build_event",
    "envelope_document",
    "http_sender",
]

# One POST, one timeout. A provider that hangs must not hold the scheduler thread: the hub's own
# retry policy is what handles a delivery that did not arrive.
DELIVERY_TIMEOUT_SECONDS = 5.0

# How long a duplicate's spread may last before the double refuses: the hub's tolerance is
# 300 s, so a burst beyond that would be testing the window rather than the deduplication.
MAX_SPREAD_MS = 250_000

Sender = Callable[[str, bytes, dict[str, str]], int | None]

# The outcome a collection's completion produces, to the envelope type of the contract.
OUTCOME_TYPES = {
    SUCCESSFUL: "payment.confirmed",
    FAILED: "payment.failed",
    "reversed": "payment.reversed",
}


def http_sender(url: str, body: bytes, headers: dict[str, str]) -> int | None:
    """POST the exact bytes: `content=`, never `json=`, which would re-serialise them."""
    try:
        with httpx.Client(timeout=DELIVERY_TIMEOUT_SECONDS) as client:
            return client.post(url, content=body, headers=headers).status_code
    except httpx.HTTPError:
        # The hub is unreachable. That is a fact worth recording and not a reason to stop: the
        # provider would retry, and the double's control API is how a test asks for one.
        return None


def envelope_document(
    collection: Collection,
    outcome: str,
    *,
    event_id: str,
    occurred_at: str,
    mode: str | None = None,
    params: Mapping[str, Any] | None = None,
    failure_code: str | None = None,
) -> dict[str, Any]:
    """The `WebhookEnvelope` for one outcome, with the mode's body mutations applied.

    Mutations happen *before* serialisation, so a `wrong_amount` delivery is genuinely signed:
    the hub has to reject it on its business rules, not on the signature (Task T1.3).
    """
    if outcome not in OUTCOME_TYPES:
        raise ValueError(f"not a collection outcome: {outcome!r}")
    values = dict(params or {})
    data: dict[str, Any] = {
        "provider_reference": collection.provider_reference,
        "external_reference": collection.reference,
        "amount": {
            "amount_minor": collection.amount_minor,
            "currency": collection.currency,
        },
    }
    if collection.payer_msisdn:
        data["payer_msisdn"] = collection.payer_msisdn
    if outcome == FAILED:
        data["failure_code"] = failure_code or values.get("failure_code") or "declined"

    if mode == "wrong_amount":
        data["amount"]["amount_minor"] = int(values["amount_minor"])
    elif mode == "wrong_currency":
        data["amount"]["currency"] = str(values["currency"])
    elif mode == "wrong_payer":
        data["payer_msisdn"] = str(values["payer_msisdn"])

    return {
        "event_id": event_id,
        "type": OUTCOME_TYPES[outcome],
        "occurred_at": occurred_at,
        "data": data,
    }


def serialise(document: Mapping[str, Any]) -> bytes:
    """The bytes that are signed and the bytes that are sent — the same object, once."""
    return json.dumps(dict(document), separators=(",", ":"), sort_keys=True).encode("utf-8")


def build_event(
    collection: Collection,
    outcome: str,
    *,
    secret: bytes,
    event_id: str,
    timestamp: int,
    mode: str | None = None,
    params: Mapping[str, Any] | None = None,
    occurred_at: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    """``(body, headers)`` for one delivery, signed over the exact bytes returned."""
    when = occurred_at or datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")
    body = serialise(
        envelope_document(
            collection,
            outcome,
            event_id=event_id,
            occurred_at=when,
            mode=mode,
            params=params,
        )
    )
    return body, build_headers(
        secret=secret, timestamp=timestamp, event_id=event_id, body=body
    )


@dataclass(order=True, slots=True)
class _Planned:
    """One pending delivery. Ordering is by due time, then by insertion sequence."""

    due: float
    sequence: int
    provider: str = field(compare=False)
    provider_reference: str = field(compare=False)
    event_id: str = field(compare=False)
    outcome: str = field(compare=False)
    mode: str | None = field(compare=False, default=None)
    params: dict[str, Any] = field(compare=False, default_factory=dict)
    attempt: int = field(compare=False, default=1)
    wait_for: str | None = field(compare=False, default=None)
    # A reversal delivery flips the collection's status and writes its own provider transaction
    # *when it is sent*, never when it is planned: a status pull between the confirmation and the
    # reversal must still say `successful`.
    is_reversal: bool = field(compare=False, default=False)


class Deliverer:
    """The pending-delivery heap, the sender, and the rule that turns a mode into a number."""

    def __init__(
        self,
        *,
        config: Config,
        store: Store,
        sender: Sender | None = None,
        clock: Callable[[], float] = time.time,
        random_source: Any = None,
        scenarios: Callable[[Collection], Scenario | None] | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._sender = sender or http_sender
        self._clock = clock
        # How the control API's scenarios reach a collection. Injected rather than imported so
        # the scheduler stays a pure function of its inputs, which is what makes it testable
        # without a server.
        self._scenarios = scenarios or (lambda collection: None)
        # Deterministic event ids, so a failure can be reproduced from its log line alone.
        import random  # noqa: PLC0415 - seeded here, never at import

        self._random = random_source or random.Random(config.seed)
        self._lock = threading.RLock()
        self._heap: list[_Planned] = []
        self._sequence = count()
        self._delivered: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    # --- scheduling ------------------------------------------------------------------------
    @property
    def config(self) -> Config:
        return self._config

    def new_event_id(self) -> str:
        """`evt_` plus 16 hex, drawn from the seeded source (the plan's id shape)."""
        return "evt_" + "".join(self._random.choice("0123456789abcdef") for _ in range(16))

    def plan(
        self,
        collection: Collection,
        *,
        outcome: str,
        scenario: Scenario | None = None,
        due: float | None = None,
        event_id: str | None = None,
        attempt: int = 1,
        wait_for: str | None = None,
        is_reversal: bool = False,
    ) -> _Planned:
        """Put one delivery on the heap, applying the scenario's mode."""
        mode = scenario.mode if scenario is not None else "success"
        params = dict(scenario.params) if scenario is not None else {}
        planned = _Planned(
            due=self._clock() if due is None else due,
            sequence=next(self._sequence),
            provider=collection.provider,
            provider_reference=collection.provider_reference,
            event_id=event_id or self.new_event_id(),
            outcome=outcome,
            mode=mode,
            params=params,
            attempt=attempt,
            wait_for=wait_for,
            is_reversal=is_reversal,
        )
        with self._lock:
            heapq.heappush(self._heap, planned)
        return planned

    def plan_outcome(
        self,
        collection: Collection,
        *,
        outcome: str,
        scenario: Scenario | None = None,
    ) -> float | None:
        """Schedule the deliveries one outcome produces; returns when the first one is due.

        The modes are turned into numbers here and nowhere else, so "how does `duplicate` work"
        has exactly one answer.
        """
        now = self._clock()
        mode = scenario.mode if scenario is not None else "success"
        params = dict(scenario.params) if scenario is not None else {}
        event_id = self.new_event_id()

        if mode == "drop":
            # Nothing is sent. The status has already been set by the caller, which is the whole
            # point: the pull path stays truthful while the webhook never arrives.
            return None
        if mode == "delay":
            due = now + float(params.get("seconds", 1))
        elif mode == "late":
            due = now + float(params.get("seconds", 1))
        else:
            due = now

        wait_for: str | None = None
        if mode == "reorder":
            wait_for = str(params["with"])

        self.plan(
            collection,
            outcome=outcome,
            scenario=scenario,
            due=due,
            event_id=event_id,
            wait_for=wait_for,
        )

        if mode == "reverse_after":
            # The reversal is a second envelope about the same charge, later, with its own event
            # id: the hub keys it as its own provider transaction (`<reference>-R`).
            self.plan(
                collection,
                outcome="reversed",
                scenario=None,
                due=now + float(params.get("seconds", 1)),
                event_id=self.new_event_id(),
                is_reversal=True,
            )

        if mode == "duplicate":
            copies = int(params.get("count", 1))
            spread = int(params.get("spread_ms", 0))
            if spread < 0 or spread > MAX_SPREAD_MS:
                raise ValueError(f"duplicate spread_ms must be 0..{MAX_SPREAD_MS}")
            step = spread / 1000 if copies > 1 else 0.0
            for index in range(copies):
                self.plan(
                    collection,
                    outcome=outcome,
                    scenario=scenario,
                    due=due + step * (index + 1),
                    event_id=event_id,  # the same event id: the hub's key decides the loser
                    attempt=index + 2,
                )
        return due

    # --- sending ---------------------------------------------------------------------------
    def run_due(self, *, now: float | None = None) -> list[Delivery]:
        """Send every delivery that is due; returns what was sent, in order."""
        moment = self._clock() if now is None else now
        sent: list[Delivery] = []
        with self._lock:
            due = self._take_due(moment)
        for planned in due:
            delivery = self._send(planned, moment)
            if delivery is not None:
                sent.append(delivery)
        return sent

    def _take_due(self, moment: float) -> list[_Planned]:
        """Pop the deliveries that are due and no longer waiting on another one.

        A `reorder` pair can be released by the very pass that delivers the delivery it waited
        for: the first send records its reference, and the waiter is then due. So the pass is
        repeated until it makes no progress, which delivers the two in the intended order within
        one call — the alternative (one call per dependency level) would make the caller's loop
        the scheduler.
        """
        ready: list[_Planned] = []
        while True:
            ready_now, held = self._pop_due(moment)
            if not ready_now:
                if held:  # pragma: no cover - a waiter whose dependency never comes
                    for planned in held:
                        heapq.heappush(self._heap, planned)
                return ready
            ready.extend(ready_now)
            for planned in held:
                heapq.heappush(self._heap, planned)
            for planned in ready_now:
                self._delivered.add(planned.provider_reference)

    def _pop_due(self, moment: float) -> tuple[list[_Planned], list[_Planned]]:
        """One sweep of the heap: what is due, and what is still waiting on another delivery."""
        ready: list[_Planned] = []
        held: list[_Planned] = []
        while self._heap and self._heap[0].due <= moment:
            planned = heapq.heappop(self._heap)
            if planned.wait_for is not None and planned.wait_for not in self._delivered:
                held.append(planned)
                continue
            ready.append(planned)
        return ready, held

    def _send(self, planned: _Planned, moment: float) -> Delivery | None:
        """One attempt: build the bytes, sign them, post them, record the outcome."""
        if planned.is_reversal:
            # The status moves to `reversed` with the envelope, so the double's status API and the
            # webhook agree at every instant a hub might pull.
            self._store.note_reversal(planned.provider, planned.provider_reference)
        collection = self._store.get(planned.provider, planned.provider_reference)
        if collection is None:  # pragma: no cover - nothing schedules a delivery without a row
            return None
        secret, timestamp = self._credentials(collection, planned, moment)
        body, headers = build_event(
            collection,
            planned.outcome,
            secret=secret,
            event_id=planned.event_id,
            timestamp=int(timestamp),
            mode=planned.mode,
            params=planned.params,
        )
        # A delivery is always *sent*: `bad_signature` and `wrong_secret` produce a wrong
        # signature over delivered bytes, which is the only way to test that the hub rejects on
        # the signature rather than on the body.
        hub_status = self._sender(self._config.webhook_url(collection.provider), body, headers)
        delivery = Delivery(
            event_id=planned.event_id,
            provider=collection.provider,
            attempt=planned.attempt,
            sent_at=datetime.fromtimestamp(moment, UTC).isoformat().replace("+00:00", "Z"),
            hub_status=hub_status,
            # A reversal is recorded under the mode that scheduled it, so `/control/deliveries`
            # says why a second envelope for one charge went out.
            mode="reverse_after" if planned.is_reversal else planned.mode,
            note=planned.provider_reference,
        )
        self._store.record_delivery(delivery)
        with self._lock:
            self._delivered.add(collection.reference)
            self._delivered.add(planned.provider_reference)
        return delivery

    def _credentials(self, collection: Collection, planned: _Planned, moment: float) -> tuple[bytes, float]:
        """The secret and the timestamp this attempt signs with, after the signature faults."""
        secret = self._config.secret_for(collection.provider).encode("utf-8")
        timestamp = moment
        if planned.mode == "stale_signature":
            timestamp = moment - float(planned.params.get("age_seconds", 3600))
        elif planned.mode == "bad_signature":
            secret = b"not-the-providers-secret"
        elif planned.mode == "wrong_secret":
            other = str(
                planned.params.get("secret_provider")
                or next(
                    name
                    for name in self._config.secrets
                    if name != collection.provider
                )
            )
            secret = self._config.secret_for(other).encode("utf-8")
        return secret, timestamp

    # --- the automatic completion loop ------------------------------------------------------
    def auto_confirm_due(self, *, now: float | None = None) -> None:
        """Complete the collections the provider would have settled by now.

        `STUB_AUTO_CONFIRM_SECONDS = 0` disables it, which is what the tests use: a double that
        settles on its own would make "then a control call completed it" untestable.
        """
        if self._config.auto_confirm_seconds <= 0:
            return
        moment = self._clock() if now is None else now
        for collection in self._store.collections():
            if collection.status != "pending":
                continue
            created = _parse_instant(collection.updated_at)
            if created is None or moment - created < self._config.auto_confirm_seconds:
                continue
            scenario = self._scenarios(collection)
            # The outcome is the *scenario's*: a `failure` scenario that settled successfully
            # would make the hub's failed-payment path untestable through the collection API.
            outcome = FAILED if scenario is not None and scenario.mode == "failure" else SUCCESSFUL
            self.complete(collection.provider, collection.provider_reference, outcome=outcome)

    def complete(self, provider: str, provider_reference: str, *, outcome: str) -> Collection:
        """Settle a collection and schedule the envelope its outcome produces."""
        collection = self._store.set_status(provider, provider_reference, outcome)
        self.plan_outcome(collection, outcome=outcome, scenario=self._scenarios(collection))
        return collection

    def set_scenarios(self, chooser: Callable[[Collection], Scenario | None]) -> None:
        """Install the scenario chooser once the control API exists (the router does this)."""
        self._scenarios = chooser

    def clear(self) -> None:
        """Drop every pending delivery and every memory of what was delivered (reset)."""
        with self._lock:
            self._heap.clear()
            self._delivered.clear()

    # --- lifecycle -------------------------------------------------------------------------
    def start(self, *, interval: float = 0.05) -> None:
        """Run the scheduler in the background, as `serve` does."""
        if self._thread is not None:
            return
        self._stopping.clear()

        def loop() -> None:  # pragma: no cover - the loop is exercised through run_due
            while not self._stopping.wait(interval):
                self.auto_confirm_due()
                self.run_due()

        self._thread = threading.Thread(target=loop, name="provider-stub-deliverer", daemon=True)
        self._thread.start()

    def stop(self) -> None:  # pragma: no cover - a daemon thread dies with the process
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._heap)

    @property
    def next_due(self) -> float | None:
        """When the next delivery is due, or ``None``.

        A caller that drives the clock itself (a test, or the demo) moves it to this instant
        rather than adding intervals up: repeated float additions drift below a due time and a
        delivery silently waits for the next sweep.
        """
        with self._lock:
            return self._heap[0].due if self._heap else None


def _parse_instant(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:  # pragma: no cover - every instant here is written by this module
        return None
