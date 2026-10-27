"""Slice 04 end to end: a top-up collected by the provider double (Task 04.11).

Every other payments test replaces the provider with an in-process fake, so none of them can prove
the three things this module pins:

* **the adapter speaks the provider's dialect.** `payments.infrastructure.provider_http` posts to
  the double's `POST /providers/{provider}/collections` and pulls
  `GET /providers/{provider}/collections/{provider_reference}`; the schemas under
  `tools/provider_stub/schemas/` are that dialect's contract of record. A fake provider can never
  fail this, which is exactly why an end-to-end run exists.
* **the signature survives the real route.** The double signs the exact bytes it sends, HAProxy
  routes them to `be_webhooks`, and five sync gunicorn workers verify them under the `hub_ingest`
  role. Nothing is patched.
* **a burst is deduplicated across workers.** The fifty-way case repeats the concurrency suite's
  first test through the balancer instead of in threads that share one process.

It needs the stack `make up` starts (``make up`` builds the provider double and seeds nothing), so
it is marked ``e2e`` and run explicitly by ``make verify SLICE=04``. Each test provisions its own
customer and meter over the staff API, and the run recreates the provider double under a seed of
its own first: the seeded registry allows ten top-ups per meter per day and five per payer per
hour, and the double's seeded event ids would make a second run a duplicate of the first — an
end-to-end suite that can only run once a day is a suite nobody runs.

The provider double is addressed through its control API, which is token-guarded; the hub reaches
the provider API through the compose network and never through this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
BALANCER = "http://localhost:8080"
STUB = "http://localhost:9100"
COMPOSE = ("docker", "compose", "--env-file", ".env", "-f", "infra/docker/compose.yaml")
PROVIDER = "mpesa_cd"

# The provider's own reference, derived from the caller's exactly as the double documents it
# (`state.provider_reference_for`). The test derives it rather than reading it back so a control
# call can complete the collection without a second endpoint; `_collection` below proves the
# derivation against the double's own answer before anything relies on it.
REFERENCE_PREFIX = "MP"

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


# --- transport -------------------------------------------------------------------------------


def request(
    method: str,
    url: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    *,
    timeout: float = 30.0,
) -> tuple[int, dict[str, str], bytes]:
    """One HTTP call; an error status is an answer, not a transport failure."""
    if body is None:
        data = None
    elif isinstance(body, bytes):
        data = body
    else:
        data = json.dumps(body).encode("utf-8")
    merged = {"Accept": "application/json"}
    if data is not None:
        merged["Content-Type"] = "application/json"
    merged.update(headers or {})
    prepared = urllib.request.Request(url, data=data, method=method, headers=merged)  # noqa: S310
    try:
        with urllib.request.urlopen(prepared, timeout=timeout) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def api(
    method: str,
    path: str,
    body: Any = None,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], Any]:
    """One call through the balancer; the body is parsed when it is JSON."""
    merged = dict(headers or {})
    if token is not None:
        merged["Authorization"] = f"Bearer {token}"
    status, received, raw = request(method, BALANCER + path, body, merged)
    try:
        document = json.loads(raw or b"{}")
    except json.JSONDecodeError:  # pragma: no cover - the hub always answers problem+json
        document = {"raw": raw.decode("utf-8", "replace")}
    return status, received, document


def stub(method: str, path: str, body: Any = None, *, control: bool = True) -> tuple[int, Any]:
    """One call to the double: the control API is token-guarded, the provider API is not."""
    headers = {"Authorization": f"Bearer {control_token()}"} if control else {}
    status, _, raw = request(method, STUB + path, body, headers)
    try:
        return status, json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", "replace")


def compose(*arguments: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [*COMPOSE, *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


# --- environment -----------------------------------------------------------------------------


def env_file() -> dict[str, str]:
    """The stack's variables, read the way `make` reads them (no shell interpolation)."""
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip()
    return values


def control_token() -> str:
    return env_file()["STUB_CONTROL_TOKEN"]


def webhook_secret() -> str:
    """The secret the double signs with for `mpesa_cd`: the hub verifies against the same value."""
    return env_file()["STUB_SECRET_MPESA_CD"]


# --- tokens ----------------------------------------------------------------------------------

# A token is minted by the running `hub-api` process rather than by this one. That is the only way
# to use the stack's own signing key without copying a key (or the derivation seed) into a test,
# and it is why a customer token here authenticates exactly like one the OTP flow issued.
MINT_SCRIPT = """\
import json
import sys
import uuid
from datetime import UTC, datetime

import django

django.setup()

from django.db import connection

from hub.identity import wiring
from hub.identity.domain.principal import MFA_ACR, Principal


def staff_token(email):
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM hub.users WHERE email = %s AND status = 'active'", [email])
        row = cursor.fetchone()
    if row is None:
        raise SystemExit("no active staff account with that address")
    now = datetime.now(UTC)
    session = uuid.uuid4()
    principal = wiring.users().build_principal(
        row[0], session_id=session, auth_time=now, acr=MFA_ACR
    )
    return wiring.tokens().issue(
        principal, session_id=session, auth_time=now, acr=MFA_ACR, amr=("pwd", "totp")
    )


def customer_token(customer_id):
    now = datetime.now(UTC)
    session = uuid.uuid4()
    principal = Principal(
        subject_id=customer_id,
        kind="customer",
        customer_id=customer_id,
        session_id=session,
        auth_time=now,
        acr="pwd",
        locale="fr",
    )
    return wiring.tokens().issue(
        principal, session_id=session, auth_time=now, acr="pwd", amr=("otp",)
    )


kind, value = sys.argv[1], sys.argv[2]
if kind == "staff":
    token = staff_token(value)
else:
    token = customer_token(uuid.UUID(value))
print(json.dumps({"token": token}))
"""


def mint(kind: str, value: str) -> str:
    """Mint one access token inside the running api container."""
    result = compose("exec", "-T", "hub-api", "python", "-c", MINT_SCRIPT, kind, value)
    if result.returncode != 0:
        pytest.fail(f"could not mint a token in hub-api:\n{result.stderr}")
    lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        pytest.fail(f"hub-api minted no token:\n{result.stdout}\n{result.stderr}")
    token = json.loads(lines[-1])["token"]
    return str(token)


# --- the stack -------------------------------------------------------------------------------


def wait_for_balancer(timeout: float = 60.0) -> None:
    """The balancer answers 503 for a few seconds after `make up --wait` returns (``rise 3``)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _, _ = request("GET", f"{BALANCER}/api/v1/health/live", timeout=5)
        except (OSError, TimeoutError):
            status = 0
        if status == 200:
            return
        time.sleep(0.5)
    raise AssertionError("the balancer never answered 200: is the stack up (make up)?")


def wait_for_stub(timeout: float = 60.0) -> None:
    """The double answers `/health`; a container that was just recreated needs a moment.

    A socket the container closed underneath us (*ConnectionResetError*, an ``OSError`` like
    ``URLError``) is the same answer as a refused one: not yet.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _, _ = request("GET", f"{STUB}/health", timeout=5)
        except (OSError, TimeoutError):
            status = 0
        if status == 200:
            return
        time.sleep(0.5)
    raise AssertionError("the provider double never answered /health: is the stack up (make up)?")


def reseed_double() -> None:
    """Recreate the double under a seed of this run's own, so its ids are this run's ids.

    An `event_id` is drawn from `random.Random(STUB_SEED)` (`Deliverer.new_event_id`, slice T1
    step T1.3), so a second run of the same flow sends the same ids — and the hub answers the
    second one `duplicate`, which is the correct answer to a repeated event id and leaves every
    top-up pending. A suite that can only run once is a suite nobody runs, so the double is
    recreated with a fresh seed before the first case: same contract, a different sequence of
    ids. Nothing else about the stack changes.
    """
    environment = dict(os.environ, STUB_SEED=str(uuid.uuid4().int % (2**31)))
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [*COMPOSE, "--profile", "stubs", "up", "-d", "--no-deps", "provider-stub"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=environment,
    )
    if result.returncode != 0:
        pytest.fail(f"could not reseed the provider double:\n{result.stderr}")
    wait_for_stub()


@pytest.fixture(scope="session", autouse=True)
def stack() -> None:
    """Refuse to pretend: without the stack there is nothing end to end about this module."""
    wait_for_balancer()
    reseed_double()
    status, document = stub("GET", "/health")
    assert status == 200, document
    assert PROVIDER in document["providers"], document


@pytest.fixture(scope="session")
def support_token() -> str:
    """`createCustomer` is a support operation, and the seed names the account."""
    return mint("staff", "support@example.test")


@pytest.fixture(scope="session")
def operations_token() -> str:
    """`createMeter` and `updateMeter` are operations operations."""
    return mint("staff", "operations@example.test")


@pytest.fixture
def clean_double() -> None:
    """Every test starts from a double that never saw a collection (and holds no scenarios).

    The reset is what makes the delivery assertions exact: without it, a collection left over
    from the previous case could still be completing, and a delivery count would mean nothing.
    """
    status, _ = stub("POST", "/control/reset")
    assert status == 204


@dataclass(frozen=True, slots=True)
class World:
    """The registry rows one test acts on, and the customer's own access token."""

    customer_id: str
    meter_id: str
    phone: str
    token: str


def _seed() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        (ROOT / "db/seed/registry_demo.json").read_text(encoding="utf-8")
    )
    return document


def _synthetic_phone() -> str:
    """A number in the synthetic range slice T3 reserves, unique enough per run."""
    suffix = int(uuid.uuid4().hex[:6], 16) % 1_000_000
    return f"+2438100{suffix:06d}"


@pytest.fixture
def world(clean_double: None, support_token: str, operations_token: str) -> World:
    """A fresh customer, a fresh active meter and that customer's token.

    Provisioning through the API (instead of reusing the seeded customer) is what lets the suite
    run repeatedly: the per-meter and per-payer limits are counted over a day and an hour.
    """
    seed = _seed()
    site_id = next(entry["id"] for entry in seed["sites"] if entry["code"] == "LSHI")
    plan_id = next(entry["id"] for entry in seed["plans"] if entry["code"] == "BASIC")
    phone = _synthetic_phone()

    status, _, body = api(
        "POST",
        "/api/v1/customers",
        {
            "full_name": "E2E Top-up Customer",
            "phone_e164": phone,
            "site_id": site_id,
            "plan_id": plan_id,
            "locale": "fr",
        },
        token=support_token,
    )
    assert status == 201, body
    customer_id = body["data"]["id"]

    status, _, body = api(
        "POST",
        "/api/v1/meters",
        {
            "serial_number": f"E2E-{uuid.uuid4().hex[:10].upper()}",
            "site_id": site_id,
            "customer_id": customer_id,
            "hardware_model": "SG-M1",
            "firmware_version": "1.4.2",
        },
        token=operations_token,
    )
    assert status == 201, body
    meter_id = body["data"]["id"]
    version = int(body["data"]["version"])

    # A registered meter is `provisioned`; only an active one may be collected into. The create
    # route answers no ETag, so the validator is built from the version it does return (the
    # contract's shape, `W/"v<n>"`).
    status, _, body = api(
        "PATCH",
        f"/api/v1/meters/{meter_id}",
        {"status": "active"},
        token=operations_token,
        headers={"If-Match": f'W/"v{version}"'},
    )
    assert status == 200 and body["data"]["status"] == "active", body

    return World(
        customer_id=str(customer_id),
        meter_id=str(meter_id),
        phone=phone,
        token=mint("customer", str(customer_id)),
    )


# --- top-ups ---------------------------------------------------------------------------------


def provider_reference(top_up_id: str) -> str:
    digest = hashlib.sha256(top_up_id.encode("utf-8")).hexdigest()[:10].upper()
    return REFERENCE_PREFIX + digest


def collection(reference: str) -> dict[str, Any]:
    """The double's own view of one collection, and a proof of the reference derivation."""
    status, document = stub("GET", f"/providers/{PROVIDER}/collections/{reference}", control=False)
    assert status == 200, document
    return dict(document)


def initiate(world: World, amount_minor: int) -> tuple[str, dict[str, Any]]:
    """`createTopUp`: 202 with a pending top-up, and the id the provider was told."""
    status, _, body = api(
        "POST",
        "/api/v1/top-ups",
        {
            "meter_id": world.meter_id,
            "amount": {"amount_minor": amount_minor, "currency": "USD"},
            "provider": PROVIDER,
            "payer_msisdn": world.phone,
        },
        token=world.token,
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert status == 202, body
    document = body["data"]
    assert document["status"] == "pending", document
    return str(document["id"]), document


def complete(reference: str, outcome: str = "successful") -> None:
    status, document = stub(
        "POST", f"/control/collections/{reference}/complete", {"outcome": outcome}
    )
    assert status == 202, document


def add_scenario(*, mode: str, params: dict[str, Any], amount_minor: int) -> None:
    """One fault, matched on the amount so it cannot touch another test's collection."""
    status, document = stub(
        "POST",
        "/control/scenarios",
        {
            "mode": mode,
            "params": params,
            "match": {"provider": PROVIDER, "amount_minor": amount_minor},
        },
    )
    assert status == 201, document


def top_up(world: World, top_up_id: str) -> dict[str, Any]:
    status, _, body = api("GET", f"/api/v1/top-ups/{top_up_id}", token=world.token)
    assert status == 200, body
    return dict(body["data"])


def wait_for_top_up(
    world: World,
    top_up_id: str,
    wanted: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Poll the customer's own route until ``wanted`` holds; the last document is in the failure."""
    deadline = time.monotonic() + timeout
    document = top_up(world, top_up_id)
    while time.monotonic() < deadline:
        if wanted(document):
            return document
        time.sleep(0.2)
        document = top_up(world, top_up_id)
    raise AssertionError(f"top-up {top_up_id} never reached the wanted state: {document}")


def wallet_balance(world: World) -> int:
    status, _, body = api("GET", f"/api/v1/customers/{world.customer_id}/wallet", token=world.token)
    assert status == 200, body
    return int(body["data"]["balance"]["amount_minor"])


def deliveries(reference: str) -> list[dict[str, Any]]:
    """Every attempt the double made for one collection, newest last."""
    status, document = stub("GET", "/control/deliveries")
    assert status == 200, document
    return [item for item in document["deliveries"] if item["note"] == reference]


def wait_for_delivery(reference: str, *, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        attempts = deliveries(reference)
        if attempts:
            return attempts[0]
        time.sleep(0.2)
    raise AssertionError(f"the double delivered nothing for {reference}")


# --- signed deliveries the test sends itself ---------------------------------------------------


def signed_delivery(
    *, top_up_id: str, reference: str, amount_minor: int, payer: str, event_id: str
) -> tuple[bytes, dict[str, str]]:
    """A `payment.confirmed` envelope, signed exactly as the double signs one.

    Used for the two cases the double cannot produce on its own: a `drop` collection (the provider
    has the money and sent no webhook) and a burst wider than the control API's twenty copies.
    """
    body = json.dumps(
        {
            "event_id": event_id,
            "type": "payment.confirmed",
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "data": {
                "provider_reference": reference,
                "external_reference": top_up_id,
                "amount": {"amount_minor": amount_minor, "currency": "USD"},
                "payer_msisdn": payer,
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    timestamp = str(int(time.time()))
    signed = f"{timestamp}.{event_id}.".encode() + body
    signature = hmac.new(webhook_secret().encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Webhook-Id": event_id,
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Signature": f"v1={signature}",
    }


def deliver(body: bytes, headers: dict[str, str], *, timeout: float = 30.0) -> tuple[int, Any]:
    """One signed delivery; the answer is the ack *document*, not the envelope around it.

    The route answers ``{"data": {"status": "accepted" | "duplicate", "event_id": ...}}``
    (`WebhookAck` in `api/openapi.yaml`), and every assertion here is about that document.
    """
    status, _, raw = request(
        "POST", f"{BALANCER}/api/v1/webhooks/providers/{PROVIDER}", body, headers, timeout=timeout
    )
    try:
        answer = json.loads(raw or b"{}")
    except json.JSONDecodeError:  # pragma: no cover - an ack is always JSON
        return status, raw
    return status, answer.get("data", answer)


def event_id() -> str:
    return "evt_" + uuid.uuid4().hex[:16]


# --- the cases ---------------------------------------------------------------------------------


@pytest.mark.tc("TC-FR-02-1")
def test_initiate_then_confirmed_via_stub(world: World) -> None:
    """The happy path over the real adapter: 202, a collection at the double, one credit.

    Also pins the initiation half of idempotency at the wire: the same key and body answer the
    same top-up, so the provider is asked once — which the double's single collection shows.
    """
    amount = 5_100
    key = uuid.uuid4().hex
    payload = {
        "meter_id": world.meter_id,
        "amount": {"amount_minor": amount, "currency": "USD"},
        "provider": PROVIDER,
        "payer_msisdn": world.phone,
    }
    status, _, body = api(
        "POST", "/api/v1/top-ups", payload, token=world.token, headers={"Idempotency-Key": key}
    )
    assert status == 202, body
    top_up_id = str(body["data"]["id"])
    assert body["data"]["status"] == "pending", body
    assert body["data"]["energy_wh"] is None, "energy is the credited amount, not the quote"
    assert world.phone not in json.dumps(body), "the payer's number must be masked"

    reference = provider_reference(top_up_id)
    live = collection(reference)
    assert live["reference"] == top_up_id
    assert live["status"] == "pending"
    assert live["amount"] == {"amount_minor": amount, "currency": "USD"}

    status, _, replay = api(
        "POST", "/api/v1/top-ups", payload, token=world.token, headers={"Idempotency-Key": key}
    )
    assert status == 202, replay
    assert replay["data"]["id"] == top_up_id
    assert collection(reference)["provider_reference"] == reference, "a second collection was made"

    complete(reference)
    document = wait_for_top_up(world, top_up_id, lambda item: item["status"] == "confirmed")

    quoted = int(document["quoted_tariff"]["amount_minor"])
    assert quoted > 0
    assert document["quoted_energy_wh"] == amount * 1000 // quoted  # D-08: round down
    assert document["energy_wh"] == document["quoted_energy_wh"]
    assert document["late"] is False
    assert document["confirmed_at"] is not None
    assert document["ledger_transaction_id"] is not None
    assert wallet_balance(world) == amount

    attempts = deliveries(reference)
    assert len(attempts) == 1, attempts
    assert attempts[0]["hub_status"] == 200, attempts


def test_stub_duplicate_mode_credits_once(world: World) -> None:
    """Five deliveries of one event: one credit, and four acks the provider never notices.

    `count` is the number of *repeats* the double adds to the original delivery
    (`Deliverer.plan_outcome`: one event id, attempts 1..count+1), so four repeats are the five
    deliveries this case is about.
    """
    amount = 5_200
    add_scenario(mode="duplicate", params={"count": 4, "spread_ms": 0}, amount_minor=amount)

    top_up_id, _ = initiate(world, amount)
    reference = provider_reference(top_up_id)
    complete(reference)

    document = wait_for_top_up(world, top_up_id, lambda item: item["status"] == "confirmed")
    attempts = deliveries(reference)
    deadline = time.monotonic() + 10
    while len(attempts) < 5 and time.monotonic() < deadline:
        time.sleep(0.2)
        attempts = deliveries(reference)
    assert len(attempts) == 5, attempts
    assert {attempt["event_id"] for attempt in attempts} == {attempts[0]["event_id"]}
    assert {attempt["attempt"] for attempt in attempts} == {1, 2, 3, 4, 5}
    assert all(attempt["hub_status"] == 200 for attempt in attempts), attempts

    assert document["ledger_transaction_id"] is not None
    assert wallet_balance(world) == amount, "a duplicate must not credit twice"


def test_stub_late_mode_after_cancel(world: World) -> None:
    """A cancelled top-up whose money arrived anyway is a late credit, not a refusal (D-05)."""
    amount = 5_300
    add_scenario(mode="late", params={"seconds": 1}, amount_minor=amount)

    top_up_id, _ = initiate(world, amount)
    status, _, body = api("POST", f"/api/v1/top-ups/{top_up_id}/cancel", token=world.token)
    assert status == 200 and body["data"]["status"] == "cancelled", body

    reference = provider_reference(top_up_id)
    complete(reference)
    document = wait_for_top_up(world, top_up_id, lambda item: item["status"] == "confirmed")

    assert document["late"] is True
    assert wallet_balance(world) == amount


def test_stub_wrong_amount_goes_to_suspense(world: World) -> None:
    """A signed delivery for another amount is suspense, never a wallet credit (D-05).

    The observable from the customer's side is the pair of negatives: no confirmation and no
    money. The posting itself lands on the suspense account, which the staff console reads; the
    point of this case is that it does not land on the wallet.
    """
    amount = 5_400
    add_scenario(mode="wrong_amount", params={"amount_minor": amount + 100}, amount_minor=amount)

    top_up_id, _ = initiate(world, amount)
    reference = provider_reference(top_up_id)
    complete(reference)

    attempt = wait_for_delivery(reference)
    assert attempt["hub_status"] == 200, attempt

    # The ack is written after the transaction commits, so a 200 is authoritative: the effect is
    # durable and the customer's route can be read without waiting for anything.
    document = top_up(world, top_up_id)
    assert document["status"] != "confirmed", document
    assert document["confirmed_at"] is None
    assert document["ledger_transaction_id"] is None
    assert wallet_balance(world) == 0


def test_stub_reverse_after_posts_reversal(world: World) -> None:
    """A reversal after the confirmation returns the money and keeps its own transaction id."""
    amount = 5_500
    add_scenario(mode="reverse_after", params={"seconds": 1}, amount_minor=amount)

    top_up_id, _ = initiate(world, amount)
    reference = provider_reference(top_up_id)
    complete(reference)

    confirmed = wait_for_top_up(world, top_up_id, lambda item: item["status"] == "confirmed")
    assert confirmed["ledger_transaction_id"] is not None
    document = wait_for_top_up(world, top_up_id, lambda item: item["status"] == "reversed")

    assert document["reversal_transaction_id"] is not None
    assert document["reversal_transaction_id"] != document["ledger_transaction_id"]
    assert wallet_balance(world) == 0

    attempts = deliveries(reference)
    assert len(attempts) == 2, attempts
    assert len({attempt["event_id"] for attempt in attempts}) == 2, "the reversal needs its own id"
    assert all(attempt["hub_status"] == 200 for attempt in attempts), attempts


@pytest.mark.tc("TC-NFR-02-1")
def test_ack_p95_under_300ms_smoke(world: World) -> None:
    """Two hundred sequential signed deliveries, ninety-five percent under the budget (NFR-02).

    The double takes the money and sends nothing (`drop`), so the first delivery is a real
    confirmation and the rest are new events about the same charge:    every one verifies a signature, attempts the unique insert and finds the effect already there.
    That is the whole synchronous path under load, and it is the part a provider's retry storm
    actually repeats: a new event id each time, the same charge.
    """
    amount = 5_600
    add_scenario(mode="drop", params={}, amount_minor=amount)

    top_up_id, _ = initiate(world, amount)
    reference = provider_reference(top_up_id)
    complete(reference)
    assert collection(reference)["status"] == "successful", "the pull must agree with the money"
    assert deliveries(reference) == [], "drop must send no envelope"

    latencies: list[float] = []
    outcomes: list[str] = []
    for _ in range(200):
        body, headers = signed_delivery(
            top_up_id=top_up_id,
            reference=reference,
            amount_minor=amount,
            payer=world.phone,
            event_id=event_id(),
        )
        started = time.perf_counter()
        status, ack = deliver(body, headers)
        latencies.append(time.perf_counter() - started)
        assert status == 200, ack
        outcomes.append(str(ack.get("status")))

    assert outcomes[0] == "accepted", outcomes[:1]
    assert set(outcomes[1:]) == {"duplicate"}, Counter(outcomes)

    ordered = sorted(latencies)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    assert p95 < 0.300, f"p95 ack was {p95 * 1000:.0f} ms over 200 deliveries"

    document = wait_for_top_up(world, top_up_id, lambda item: item["status"] == "confirmed")
    assert document["late"] is False
    assert wallet_balance(world) == amount, "the retry storm credited more than once"


@pytest.mark.tc("TC-FR-03-1")
def test_four_gunicorn_workers_fifty_parallel(world: World) -> None:
    """Fifty copies of one event through the balancer: one effect, forty-nine duplicates.

    The compose pool runs five sync workers, not four; fifty concurrent copies are spread across
    all of them, so the property under test is the one the plan names — an effect recorded once
    even when the copies are in different processes that share nothing but PostgreSQL.
    """
    amount = 5_700
    add_scenario(mode="drop", params={}, amount_minor=amount)

    top_up_id, _ = initiate(world, amount)
    reference = provider_reference(top_up_id)
    complete(reference)

    body, headers = signed_delivery(
        top_up_id=top_up_id,
        reference=reference,
        amount_minor=amount,
        payer=world.phone,
        event_id=event_id(),
    )
    barrier = Barrier(50)

    def one(_: int) -> tuple[int, Any]:
        barrier.wait(timeout=30)
        return deliver(body, dict(headers), timeout=60)

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(one, range(50)))

    statuses = [status for status, _ in results]
    assert statuses == [200] * 50, Counter(statuses)
    outcomes = Counter(str(ack.get("status")) for _, ack in results)
    assert outcomes == {"accepted": 1, "duplicate": 49}, outcomes

    document = wait_for_top_up(world, top_up_id, lambda item: item["status"] == "confirmed")
    assert document["ledger_transaction_id"] is not None
    assert wallet_balance(world) == amount, "fifty parallel copies credited more than once"
