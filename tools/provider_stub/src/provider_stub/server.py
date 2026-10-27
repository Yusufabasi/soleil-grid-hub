"""The double's HTTP surface: the provider API, the control API and `/health`.

The router is a plain function of the request — method, path, query, headers, raw body — so the
tests can drive every endpoint without a socket, and the server is a thin
`http.server.ThreadingHTTPServer` around it. No web framework: the double has fewer than a dozen
routes, and a dependency-free double is one that never drifts from the contract it is doubling.

Two boundaries are deliberate:

* **Unknown providers are 404, before anything else.** A request for a channel the double does not
  know must not create state, and it must not be answered with another channel's secret.
* **The control API is token-guarded and the provider API is not.** The provider API is what the
  hub's adapter calls, over the compose network — a real provider would authenticate there and
  docs/10 section 6.4 says the double does not. The control API tells the double to misbehave, so
  it demands `Authorization: Bearer <STUB_CONTROL_TOKEN>` and answers 401 without it.

Every state change goes through :class:`~provider_stub.state.Store`, which journals it before the
answer is sent.
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import threading
from collections.abc import Callable
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from provider_stub.config import PROVIDERS, Config
from provider_stub.deliver import Deliverer
from provider_stub.scenarios import Scenario, ScenarioError, select, validate_mode
from provider_stub.state import Collection, Store
from provider_stub.statements import (
    KINDS,
    Row,
    render,
    rows_for_collections,
    rows_for_day,
    rows_for_month,
)

__all__ = ["MAX_BODY_BYTES", "Router", "Response", "StubServer", "handler_for", "make_server"]

logger = logging.getLogger("provider_stub.server")

# A control body is a handful of fields; anything larger is a mistake or an attack.
MAX_BODY_BYTES = 1_048_576

# A simulated call never waits longer than this, however the percentiles are configured: a
# double that reads as a hung provider would make a timeout test about the double.
MAX_LATENCY_SECONDS = 30.0

# The z of the 99th percentile of a standard normal. It is the whole fit: a log-normal has median
# `exp(mu)` and p99 `exp(mu + z*sigma)`, so two configured numbers give `(mu, sigma)`.
Z_P99 = 2.3263478740408408

# `YYYY-MM-DD` and `YYYY-MM`. The regexes are here rather than in a date parser so a bad query
# string is a 400 with a message instead of a 500 from a traceback.
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
CURRENCIES = ("USD", "CDF")


def _sleep(seconds: float) -> None:
    """Wait for real. A caller that must not wait injects its own sleeper (see `Router`)."""
    if seconds > 0:
        import time  # noqa: PLC0415 - importing at the top would make `time` patchable by accident

        time.sleep(min(seconds, MAX_LATENCY_SECONDS))


class Response:
    """A status, a body and a content type; the only thing the router returns."""

    __slots__ = ("body", "content_type", "status")

    def __init__(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.status = status
        self.body = body
        self.content_type = content_type

    @classmethod
    def json(cls, status: int, document: Any) -> Response:
        encoded = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return cls(status, encoded, "application/json")

    @classmethod
    def error(cls, status: int, code: str, detail: str = "") -> Response:
        return cls.json(status, {"error": {"code": code, "detail": detail}})

    @classmethod
    def empty(cls, status: int) -> Response:
        return cls(status, b"", "text/plain")


class Router:
    """Every route of the double, as a function of the request."""

    def __init__(
        self,
        *,
        config: Config,
        store: Store,
        deliverer: Deliverer,
        clock: Callable[[], float],
        sleep: Callable[[float], None] = _sleep,
    ) -> None:
        self.config = config
        self.store = store
        self.deliverer = deliverer
        self._clock = clock
        # Injected like the clock, for the same reason: the latency the double simulates is the
        # hub's problem, and a test that proved it by waiting 400 ms would be a slow test.
        self._sleep = sleep
        self._random = random.Random(config.seed)
        self._lock = threading.RLock()
        self._scenarios: list[Scenario] = []
        self._scenario_sequence = 0
        # Injected statement mismatches, keyed by (provider, date): the next export of that day
        # carries them, which is how slice 14's tests get a statement that proves something.
        self._injections: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.deliverer.set_scenarios(self._scenario_for)

    # --- scenarios -------------------------------------------------------------------------
    def _scenario_for(self, collection: Collection) -> Scenario | None:
        return select(
            list(self._scenarios),
            {
                "provider": collection.provider,
                "payer_msisdn": collection.payer_msisdn or None,
                "reference": collection.reference,
                "amount_minor": collection.amount_minor,
            },
        )

    @property
    def scenarios(self) -> list[Scenario]:
        with self._lock:
            return list(self._scenarios)

    def add_scenario(self, *, mode: str, params: dict[str, Any], match: dict[str, Any]) -> Scenario:
        validate_mode(mode, params)
        with self._lock:
            self._scenario_sequence += 1
            scenario = Scenario(
                scenario_id=f"scn_{self._scenario_sequence:04d}",
                mode=mode,
                params=params,
                match=match,
            )
            self._scenarios.append(scenario)
            return scenario

    def delete_scenario(self, scenario_id: str) -> bool:
        with self._lock:
            remaining = [item for item in self._scenarios if item.scenario_id != scenario_id]
            found = len(remaining) != len(self._scenarios)
            self._scenarios = remaining
            return found

    def reset(self) -> None:
        """Forget scenarios, collections, deliveries and journals — a fresh double."""
        with self._lock:
            self._scenarios.clear()
            self._scenario_sequence = 0
            self._injections.clear()
        self.store.reset()
        self.deliverer.clear()

    # --- routing ---------------------------------------------------------------------------
    def handle(
        self,
        method: str,
        target: str,
        headers: dict[str, str],
        body: bytes,
    ) -> Response:
        """Answer one request. ``target`` is the path plus query string, as received."""
        parts = urlsplit(target)
        path = parts.path.rstrip("/") or "/"
        query = parse_qs(parts.query)

        if path == "/health":
            return Response.json(200, {"status": "ok", "providers": list(PROVIDERS)})

        if path.startswith("/control/"):
            if not self._authorised(headers):
                return Response.error(401, "unauthorized", "a control call needs the bearer token")
            return self._control(method, path, body)

        match = re.fullmatch(r"/providers/([a-z_]+)/(.*)", path)
        if match is None:
            return Response.error(404, "not_found", path)
        provider, rest = match.groups()
        if provider not in PROVIDERS:
            return Response.error(404, "unknown_provider", provider)
        # The provider API answers slowly when the caller asks for it; `/health` and the control
        # API never do, so an operator is not waiting on simulated latency to see the truth.
        self.pause()

        # A route that exists under another method is 405, not 404: the hub's adapter logs the
        # code it was told, and "that path is not here" would send it looking for a
        # configuration mistake instead of a wrong verb.
        if rest == "collections":
            if method != "POST":
                return Response.error(405, "method_not_allowed", method)
            return self._initiate(provider, body)
        collections_match = re.fullmatch(r"collections/([A-Za-z0-9._-]+)", rest)
        if collections_match is not None:
            if method != "GET":
                return Response.error(405, "method_not_allowed", method)
            return self._status(provider, collections_match.group(1))
        if rest == "statements":
            if method != "GET":
                return Response.error(405, "method_not_allowed", method)
            return self._statement(provider, query)
        return Response.error(404, "not_found", path)

    def pause(self) -> None:
        """Wait the provider's own latency, when the configuration asks for it (Task T1.1).

        Off by default, and off in every test: a double whose calls take 400 ms slows a suite
        without proving anything. It is on in the compose service (`STUB_SIMULATE_LATENCY=1`),
        where what is being rehearsed is the *hub's* timeout and latency budget against a provider
        that is not instant.

        The delay is drawn from a log-normal fitted to `STUB_LATENCY_P50_MS` and
        `STUB_LATENCY_P99_MS` — two numbers an operator can reason about, rather than a spread
        nobody can predict — from the seeded source, so a slow call is reproducible.
        """
        if not self.config.simulate_latency:
            return
        p50 = max(1, self.config.latency_p50_ms)
        p99 = max(p50, self.config.latency_p99_ms)
        mu = math.log(p50 / 1000.0)
        sigma = math.log(p99 / p50) / Z_P99
        with self._lock:
            seconds = math.exp(self._random.normalvariate(mu, sigma))
        self._sleep(seconds)

    # --- provider API ----------------------------------------------------------------------
    def _initiate(self, provider: str, body: bytes) -> Response:
        document = _json(body)
        if document is None:
            return Response.error(400, "invalid_json")
        reference = document.get("reference")
        amount = document.get("amount") or {}
        payer = document.get("payer_msisdn")
        if not isinstance(reference, str) or not reference:
            return Response.error(422, "reference_required")
        amount_minor = amount.get("amount_minor")
        currency = amount.get("currency")
        if not isinstance(amount_minor, int) or amount_minor < 1:
            return Response.error(422, "invalid_amount")
        if currency not in CURRENCIES:
            return Response.error(422, "invalid_currency")
        if not isinstance(payer, str) or not PHONE_RE.fullmatch(payer):
            return Response.error(422, "invalid_payer")

        # `error_initiate` and `slow_initiate` are about *this* call: the hub's initiation retry
        # and its timeout path need a provider that fails or hangs before answering.
        chosen = select(
            list(self._scenarios),
            {
                "provider": provider,
                "payer_msisdn": payer,
                "reference": reference,
                "amount_minor": amount_minor,
            },
        )
        if chosen is not None and chosen.mode == "error_initiate":
            return Response.error(int(chosen.params.get("status", 500)), "provider_error")
        if chosen is not None and chosen.mode == "slow_initiate":
            # The injected sleeper, so a test that asks for a slow initiate does not live it.
            self._sleep(float(chosen.params.get("seconds", 1)))

        existing = self.store.by_reference(provider, reference)
        if existing is not None:
            if existing.amount_minor != amount_minor or existing.currency != currency:
                # A second, different collection under one reference is not a retry: the hub must
                # learn that its idempotency key was reused, not silently have it satisfied.
                return Response.error(409, "reference_conflict", str(existing.provider_reference))
            return Response.json(202, _ack(existing))

        collection = self.store.add_collection(
            provider=provider,
            reference=reference,
            amount_minor=amount_minor,
            currency=str(currency),
            payer_msisdn=payer,
        )
        return Response.json(202, _ack(collection))

    def _status(self, provider: str, provider_reference: str) -> Response:
        collection = self.store.get(provider, provider_reference)
        if collection is None:
            return Response.error(404, "unknown_collection", provider_reference)
        return Response.json(200, collection.document())

    def _statement(self, provider: str, query: dict[str, list[str]]) -> Response:
        rows = self._rows(provider)
        if "month" in query:
            month = query["month"][0]
            if not MONTH_RE.fullmatch(month):
                return Response.error(400, "invalid_month", month)
            chosen = rows_for_month(rows, month)
            injections = self._injections.get((provider, month), [])
        else:
            day = query.get("date", [date.today().isoformat()])[0]
            if not DATE_RE.fullmatch(day):
                return Response.error(400, "invalid_date", day)
            chosen = rows_for_day(rows, date.fromisoformat(day))
            injections = self._injections.get((provider, day), [])
        return Response(200, render(chosen, injections), "text/csv")

    def _rows(self, provider: str) -> list[Row]:
        """The provider's own view: one row per collection it has settled successfully."""
        return rows_for_collections(self.store.collections(provider))

    # --- control API -----------------------------------------------------------------------
    def _control(self, method: str, path: str, body: bytes) -> Response:
        if path == "/control/deliveries" and method == "GET":
            return Response.json(
                200,
                {"deliveries": [_delivery_document(item) for item in self.store.deliveries()]},
            )
        if path == "/control/scenarios" and method == "POST":
            document = _json(body)
            if document is None:
                return Response.error(400, "invalid_json")
            try:
                scenario = self.add_scenario(
                    mode=str(document.get("mode", "")),
                    params=dict(document.get("params") or {}),
                    match=dict(document.get("match") or {}),
                )
            except ScenarioError as exc:
                return Response.error(422, "invalid_scenario", str(exc))
            return Response.json(201, {"scenario_id": scenario.scenario_id})
        scenario_match = re.fullmatch(r"/control/scenarios/([A-Za-z0-9_]+)", path)
        if scenario_match is not None and method == "DELETE":
            if self.delete_scenario(scenario_match.group(1)):
                return Response.empty(204)
            return Response.error(404, "unknown_scenario", scenario_match.group(1))
        if path == "/control/reset" and method == "POST":
            self.reset()
            return Response.empty(204)
        complete_match = re.fullmatch(r"/control/collections/([A-Za-z0-9._-]+)/complete", path)
        if complete_match is not None and method == "POST":
            return self._complete(complete_match.group(1), body)
        if path == "/control/statements/inject" and method == "POST":
            return self._inject(body)
        return Response.error(404, "not_found", path)

    def _complete(self, provider_reference: str, body: bytes) -> Response:
        document = _json(body) or {}
        outcome = str(document.get("outcome", "successful"))
        found = [
            (collection.provider, collection)
            for collection in self.store.collections()
            if collection.provider_reference == provider_reference
        ]
        if not found:
            return Response.error(404, "unknown_collection", provider_reference)
        provider, _ = found[0]
        collection = self.deliverer.complete(provider, provider_reference, outcome=outcome)
        return Response.json(202, collection.document())

    def _inject(self, body: bytes) -> Response:
        document = _json(body)
        if document is None:
            return Response.error(400, "invalid_json")
        provider = str(document.get("provider", ""))
        if provider not in PROVIDERS:
            return Response.error(404, "unknown_provider", provider)
        when = str(document.get("date", "") or document.get("month", ""))
        if not (DATE_RE.fullmatch(when) or MONTH_RE.fullmatch(when)):
            return Response.error(400, "invalid_date", when)
        injections = [dict(item) for item in document.get("injections") or []]
        try:
            for item in injections:
                validate_injection(item)
        except ValueError as exc:
            return Response.error(422, "invalid_injection", str(exc))
        with self._lock:
            self._injections.setdefault((provider, when), []).extend(injections)
        return Response.json(201, {"injections": len(injections)})

    def _authorised(self, headers: dict[str, str]) -> bool:
        supplied = headers.get("Authorization", "")
        expected = f"Bearer {self.config.control_token}"
        return supplied == expected


def validate_injection(injection: dict[str, Any]) -> None:
    """An injection kind the renderer does not know must be refused, not ignored."""
    kind = str(injection.get("kind", ""))
    if kind not in KINDS:
        raise ValueError(f"not a statement injection kind: {kind!r}")


def _ack(collection: Collection) -> dict[str, Any]:
    return {
        "provider_reference": collection.provider_reference,
        "reference": collection.reference,
        "status": collection.status,
    }


def _delivery_document(delivery: Any) -> dict[str, Any]:
    return {
        "event_id": delivery.event_id,
        "provider": delivery.provider,
        "attempt": delivery.attempt,
        "sent_at": delivery.sent_at,
        "hub_status": delivery.hub_status,
        "mode": delivery.mode,
        "note": delivery.note,
    }


def _json(body: bytes) -> dict[str, Any] | None:
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class StubServer(ThreadingHTTPServer):
    """The threading server, carrying the router its handler answers through."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], router: Router) -> None:
        self.router = router
        super().__init__(address, handler_for(router))

    def stop(self) -> None:  # pragma: no cover - exercised by the CLI's signal handling
        self.shutdown()
        self.server_close()


def handler_for(router: Router) -> type[BaseHTTPRequestHandler]:
    """A request handler bound to one router.

    Built per server rather than looked up globally so two doubles in one test process (two
    journals, two sets of secrets) cannot answer through each other's state.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "provider-stub/0.1"

        def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802 - the stdlib's spelling
            self._respond("POST")

        def do_DELETE(self) -> None:  # noqa: N802 - the stdlib's spelling
            self._respond("DELETE")

        # Declared so a PUT or PATCH is answered by the router like every other method. The
        # stdlib would otherwise answer its own HTML 501, and the double's rule is that every
        # answer is a document the caller can read.
        def do_PUT(self) -> None:  # noqa: N802 - the stdlib's spelling
            self._respond("PUT")

        def do_PATCH(self) -> None:  # noqa: N802 - the stdlib's spelling
            self._respond("PATCH")

        def _respond(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._send(Response.error(413, "body_too_large", str(length)))
                return
            body = self.rfile.read(length) if length else b""
            headers = {name: value for name, value in self.headers.items()}
            response = router.handle(method, self.path, headers, body)
            self._send(response)

        def _send(self, response: Response) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # The access log goes through `logging`, which the compose service configures; the
            # stdlib's default writes straight to stderr and cannot be turned off.
            logger.info("provider_stub.http", extra={"line": format % args})

    return Handler


def make_server(
    *,
    config: Config,
    store: Store,
    deliverer: Deliverer,
    clock: Callable[[], float],
    host: str = "0.0.0.0",
    port: int | None = None,
) -> StubServer:
    """A server bound to ``port`` (0 for a test, the configured port for the service)."""
    router = Router(config=config, store=store, deliverer=deliverer, clock=clock)
    return StubServer((host, config.port if port is None else port), router)
