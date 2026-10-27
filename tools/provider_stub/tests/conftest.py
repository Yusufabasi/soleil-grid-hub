"""Fixtures for the double's own tests: a clock that moves, a hub that remembers, a server.

Two of these are the reason the suite can be about behaviour rather than about sleeping:

* :class:`FakeClock` — every delay in the double is expressed in clock readings, so `late` and
  `reverse_after` are tested by moving time, not by waiting for it.
* :class:`FakeHub` — the sender is injectable all the way to the delivery scheduler, so the tests
  assert on the *bytes and headers* that were about to go out. That is what makes
  `test_signature_covers_exact_sent_bytes` possible: the fake hub is the only witness of the exact
  bytes, and the test re-computes the HMAC over them.

The environment for `config.load()` is spelled out here with obvious values: no secret in the
double has a default, so a test that forgot one would fail to start rather than sign with the wrong
key.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from provider_stub.config import Config, load
from provider_stub.deliver import Deliverer, Sender
from provider_stub.server import Router, StubServer, make_server
from provider_stub.signing import verify
from provider_stub.state import Store

# The secrets the tests use. Not credentials: they exist only inside a test process.
ENVIRON = {
    "STUB_SECRET_MPESA_CD": "mpesa-test-secret",
    "STUB_SECRET_AIRTEL_CD": "airtel-test-secret",
    "STUB_SECRET_ORANGE_CD": "orange-test-secret",
    "STUB_SECRET_SYNTHETIC": "synthetic-test-secret",
    "STUB_CONTROL_TOKEN": "control-test-token",
}

PROVIDER_SECRETS = {
    "mpesa_cd": ENVIRON["STUB_SECRET_MPESA_CD"],
    "airtel_cd": ENVIRON["STUB_SECRET_AIRTEL_CD"],
    "orange_cd": ENVIRON["STUB_SECRET_ORANGE_CD"],
    "synthetic": ENVIRON["STUB_SECRET_SYNTHETIC"],
}


class FakeClock:
    """A clock a test moves by hand."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


@dataclass
class Recorded:
    """One delivery as the fake hub saw it: the exact bytes, and the headers that covered them."""

    url: str
    body: bytes
    headers: dict[str, str]

    def document(self) -> dict[str, Any]:
        return json.loads(self.body)

    def signature_is_valid(self, secret: str) -> bool:
        """Whether the signature covers *these* bytes — the property review focus 1 is about."""
        return verify(
            secret.encode("utf-8"),
            self.headers["X-Webhook-Signature"],
            self.headers["X-Webhook-Timestamp"],
            self.headers["X-Webhook-Id"],
            self.body,
        )


@dataclass
class FakeHub:
    """A sender that answers a status and keeps every request it was handed."""

    status: int | None = 200
    requests: list[Recorded] = field(default_factory=list)

    def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> int | None:
        self.requests.append(Recorded(url=url, body=body, headers=dict(headers)))
        return self.status

    @property
    def bodies(self) -> list[bytes]:
        return [request.body for request in self.requests]

    def documents(self) -> list[dict[str, Any]]:
        return [request.document() for request in self.requests]

    def event_ids(self) -> list[str]:
        return [document["event_id"] for document in self.documents()]

    def timestamps(self) -> list[int]:
        return [int(request.headers["X-Webhook-Timestamp"]) for request in self.requests]

    def all_signatures_valid(self, secret: str = PROVIDER_SECRETS["mpesa_cd"]) -> bool:
        return all(request.signature_is_valid(secret) for request in self.requests)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()


@pytest.fixture
def journal(tmp_path: Path) -> Path:
    return tmp_path / "journal.jsonl"


def build_config(journal: Path, **overrides: Any) -> Config:
    """A config for a test: no latency simulation, and no automatic completion."""
    environ = {
        **ENVIRON,
        "STUB_JOURNAL_PATH": str(journal),
        "STUB_HUB_BASE_URL": "http://hub.test:8080",
        "STUB_AUTO_CONFIRM_SECONDS": "0",
        "STUB_SIMULATE_LATENCY": "0",
    }
    environ.update({key: str(value) for key, value in overrides.items()})
    return load(environ)


@pytest.fixture
def config(journal: Path) -> Config:
    return build_config(journal)


@pytest.fixture
def store(config: Config, clock: FakeClock) -> Store:
    return Store(config.journal_path, clock=clock)


@pytest.fixture
def deliverer(config: Config, store: Store, hub: FakeHub, clock: FakeClock) -> Deliverer:
    return Deliverer(config=config, store=store, sender=hub, clock=clock)


@pytest.fixture
def router(config: Config, store: Store, deliverer: Deliverer, clock: FakeClock) -> Router:
    return Router(config=config, store=store, deliverer=deliverer, clock=clock)


@dataclass
class LiveStub:
    """A server on a real socket, for the tests that must go through HTTP (and a restart)."""

    server: StubServer
    router: Router
    port: int
    _thread: threading.Thread

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        token: str | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, bytes]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.url(path), data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:  # a 4xx/5xx is an answer, not a test error
            return error.code, error.read()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)


def start_stub(config: Config, store: Store, deliverer: Deliverer, clock: FakeClock) -> LiveStub:
    """Bring one double up on an ephemeral port, serving in a background thread."""
    server = make_server(
        config=config, store=store, deliverer=deliverer, clock=clock, host="127.0.0.1", port=0
    )
    thread = threading.Thread(target=server.serve_forever, name="provider-stub-test", daemon=True)
    thread.start()
    return LiveStub(server=server, router=server.router, port=server.server_address[1], _thread=thread)


@pytest.fixture
def live_stub(
    config: Config, store: Store, deliverer: Deliverer, clock: FakeClock
) -> Any:
    stub = start_stub(config, store, deliverer, clock)
    try:
        yield stub
    finally:
        stub.stop()


def free_port() -> int:  # pragma: no cover - diagnostics helper
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


__all__ = [
    "ENVIRON",
    "PROVIDER_SECRETS",
    "FakeClock",
    "FakeHub",
    "LiveStub",
    "Recorded",
    "Sender",
    "build_config",
    "free_port",
    "start_stub",
]
