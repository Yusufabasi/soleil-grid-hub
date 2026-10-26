"""The meter registry through the real permission class (Task 02.6, TC-FR-01-1 to TC-FR-01-3).

Every call carries a real EdDSA token for a real ``hub.users`` row, so the verdict under test is
the one ``api/permissions.yaml`` gives that role. The two things this file exists for are the
races: two operators assigning one meter at the same instant must leave exactly one assignment
row, and two creates with one serial must leave exactly one meter. Both are exercised with two
threads and two real connections, because a single-threaded test proves nothing about a lock.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client

from hub.customers import wiring as customers_wiring
from hub.identity import wiring as identity_wiring
from hub.meters import wiring as meters_wiring
from hub.meters.infrastructure.repositories import MeterRegistryImpl
from hub.shared.clock import FrozenClock

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

METERS = "/api/v1/meters"
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
SITE_A = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0601")
SITE_B = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0602")


@pytest.fixture(autouse=True)
def frozen() -> Any:
    """A frozen clock and fresh wiring, so every timestamp the use cases write is known."""
    clock = FrozenClock(NOW)
    identity_wiring.use_clock(clock)
    customers_wiring.reset()
    meters_wiring.reset()
    yield clock
    meters_wiring.reset()
    customers_wiring.reset()
    identity_wiring.use_clock(None)
    identity_wiring.reset()


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


class Registry:
    """Two sites, one plan, two customers — one per site — and no meters yet."""

    def __init__(self) -> None:
        self.site_a = SITE_A
        self.site_b = SITE_B
        self.plan = uuid.uuid4()
        self.customer_a = uuid.uuid4()
        self.customer_b = uuid.uuid4()

    def seed(self) -> Registry:
        with connection.cursor() as cursor:
            # Idempotent because the concurrency tests run outside a transaction, and the
            # rows they commit are not rolled back between them.
            for site_id, code in ((self.site_a, "MTR-A"), (self.site_b, "MTR-B")):
                cursor.execute(
                    "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, 'USD') "
                    "ON CONFLICT (id) DO NOTHING",
                    [site_id, code, f"Site {code[-1]}"],
                )
            cursor.execute(
                "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, "
                "max_topup_minor) VALUES (%s, %s, 'Meter plan', 'USD', 100, 100000) "
                "ON CONFLICT (id) DO NOTHING",
                [self.plan, f"MTR-P-{self.plan.hex[:6]}"],
            )
            for customer_id, site_id in (
                (self.customer_a, self.site_a),
                (self.customer_b, self.site_b),
            ):
                phone = f"+2438{str(customer_id.int)[:8]}"
                cursor.execute(
                    "INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    [customer_id, site_id, self.plan, "Meter customer", phone],
                )
        return self


@pytest.fixture
def registry() -> Registry:
    return Registry().seed()


def seed_user(
    *roles: str, all_sites: bool = False, sites: tuple[UUID, ...] = ()
) -> tuple[UUID, str]:
    """An active staff user with a real token; site scope comes from ``user_sites``."""
    from hub.identity.infrastructure.models import User, UserRole  # noqa: PLC0415

    user_id = uuid.uuid4()
    User.objects.create(
        id=user_id,
        email=f"{user_id}@example.test",
        display_name="Test Caller",
        status="active",
        all_sites=all_sites,
        token_version=1,
    )
    for role in roles:
        UserRole.objects.create(user_id=user_id, role_code=role)
    for site_id in sites:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.user_sites (user_id, site_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                [user_id, site_id],
            )
    principal = identity_wiring.users().build_principal(
        user_id, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )
    token = identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )
    return user_id, token


def customer_token(customer_id: UUID) -> str:
    """A portal token: the customer is the subject and the claim names the same row."""
    from hub.identity.contracts import Principal  # noqa: PLC0415

    principal = Principal(subject_id=customer_id, kind="customer", customer_id=customer_id)
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=NOW, acr="otp", amr=("otp",)
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "serial_number": "SGM-0003912",
        "site_id": str(SITE_A),
        "hardware_model": "SGM-1P-60A",
    }
    payload.update(overrides)
    return payload


def create(client: Client, token: str, **overrides: Any) -> Any:
    return client.post(
        METERS, data=create_body(**overrides), content_type="application/json", headers=auth(token)
    )


def patch(
    client: Client, meter_id: str, token: str, body: dict[str, Any], *, etag: str = 'W/"v1"'
) -> Any:
    headers = {**auth(token), "If-Match": etag}
    return client.patch(
        f"{METERS}/{meter_id}", data=body, content_type="application/json", headers=headers
    )


def get(client: Client, path: str, token: str) -> Any:
    return client.get(path, headers=auth(token))


def delete(client: Client, path: str, token: str) -> Any:
    return client.delete(path, headers=auth(token))


def seed_meter(
    *,
    site_id: UUID = SITE_A,
    serial_no: str = "SGM-SEED-1",
    customer_id: UUID | None = None,
    status: str = "provisioned",
) -> UUID:
    meter_id = uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.meters (id, serial_no, site_id, customer_id, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            [meter_id, serial_no, site_id, customer_id, status],
        )
    return meter_id


class _FixedCredit:
    """A credit probe that answers one number, like slice 06's ledger will."""

    def __init__(self, amount: int) -> None:
        self._amount = amount

    def credit_wh(self, meter_id: UUID) -> int:  # noqa: ARG002 - the port's shape
        return self._amount


def outbox_events(event_type: str, meter_id: UUID) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT payload FROM hub.outbox WHERE event_type = %s AND aggregate_id = %s "
            "ORDER BY created_at",
            [event_type, meter_id],
        )
        rows = cursor.fetchall()
    return [json.loads(row[0]) if isinstance(row[0], str) else dict(row[0]) for row in rows]


def assignments(meter_id: UUID) -> list[UUID]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT customer_id FROM hub.meter_assignments WHERE meter_id = %s "
            "ORDER BY first_assigned_at",
            [meter_id],
        )
        return [row[0] for row in cursor.fetchall()]


def meter_row(meter_id: UUID) -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT serial_no, site_id, customer_id, status, version FROM hub.meters WHERE id = %s",
            [meter_id],
        )
        row = cursor.fetchone()
    assert row is not None, f"meter {meter_id} is missing"
    return tuple(row)


def audit_details(action: str) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT details FROM hub.audit_log WHERE action = %s ORDER BY seq DESC", [action]
        )
        rows = cursor.fetchall()
    return [json.loads(row[0]) if isinstance(row[0], str) else dict(row[0]) for row in rows]


@pytest.mark.tc("TC-FR-01-1")
def test_create_meter_and_read_back_status_provisioned(client: Client, registry: Registry) -> None:
    _user, token = seed_user("operations_engineer", all_sites=True)

    created = create(client, token)
    assert created.status_code == 201, created.json()
    document = created.json()["data"]
    assert document["status"] == "provisioned"
    assert document["version"] == 1
    assert document["serial_number"] == "SGM-0003912"
    assert document["site_id"] == str(registry.site_a)
    assert document["customer_id"] is None
    assert document["gateway_id"] is None
    assert document["firmware_version"] is None
    assert created["Location"] == f"/api/v1/meters/{document['id']}"

    # The contract has no column for the model (Plan notes 3): the document says "unknown" and
    # the value the caller gave lives in the audit detail, where a support call can find it.
    assert document["hardware_model"] == "unknown"
    assert audit_details("registry.meter.created")[0]["hardware_model"] == "SGM-1P-60A"

    read = get(client, f"{METERS}/{document['id']}", token)
    assert read.status_code == 200
    assert read.headers["ETag"] == 'W/"v1"'
    assert read.json()["data"]["serial_number"] == "SGM-0003912"

    # The site document counts it, through the registration seam the meters module installs.
    site = get(client, f"/api/v1/sites/{registry.site_a}", token).json()["data"]
    assert site["meter_count"] == 1


@pytest.mark.tc("TC-FR-01-2")
def test_duplicate_serial_409_serial_conflict(client: Client, registry: Registry) -> None:
    _user, token = seed_user("operations_engineer", all_sites=True)

    assert create(client, token).status_code == 201
    duplicate = create(client, token, serial_number="SGM-0003912")
    assert duplicate.status_code == 409, duplicate.json()
    assert duplicate.json()["code"] == "serial_conflict"


def test_create_with_an_assigned_customer_writes_history(
    client: Client, registry: Registry
) -> None:
    _user, token = seed_user("operations_engineer", all_sites=True)

    created = create(client, token, customer_id=str(registry.customer_a))
    assert created.status_code == 201, created.json()
    document = created.json()["data"]
    assert document["customer_id"] == str(registry.customer_a)
    assert assignments(UUID(document["id"])) == [registry.customer_a]


def test_assign_requires_customer_on_same_site_422_site_mismatch(
    client: Client, registry: Registry
) -> None:
    _user, token = seed_user("operations_engineer", all_sites=True)
    meter_id = seed_meter()

    mismatch = patch(client, str(meter_id), token, {"customer_id": str(registry.customer_b)})
    assert mismatch.status_code == 422, mismatch.json()
    assert mismatch.json()["code"] == "site_mismatch"
    assert mismatch.json()["errors"][0]["field"] == "/customer_id"

    unknown = patch(client, str(meter_id), token, {"customer_id": str(uuid.uuid4())})
    assert unknown.status_code == 422, unknown.json()
    assert unknown.json()["errors"][0]["field"] == "/customer_id"

    assert meter_row(meter_id)[2] is None, "a refused assignment writes nothing"


def test_reassign_keeps_history_in_meter_assignments(client: Client, registry: Registry) -> None:
    _user, token = seed_user("operations_engineer", all_sites=True)
    meter_id = seed_meter()

    first = patch(client, str(meter_id), token, {"customer_id": str(registry.customer_a)})
    assert first.status_code == 200, first.json()
    assert first.headers["ETag"] == 'W/"v2"'
    assert first.json()["data"]["customer_id"] == str(registry.customer_a)

    second = patch(
        client,
        str(meter_id),
        token,
        {"customer_id": str(registry.customer_a)},
        etag='W/"v2"',
    )
    assert second.status_code == 200, second.json()
    assert assignments(meter_id) == [registry.customer_a], "the same pair is not stored twice"

    # Each change of owner is an event, and an unassign is one too (customer_id: null).
    events = outbox_events("meter.assigned", meter_id)
    assert [event["customer_id"] for event in events] == [str(registry.customer_a)]

    unassign = patch(client, str(meter_id), token, {"customer_id": None}, etag='W/"v3"')
    assert unassign.status_code == 200, unassign.json()
    assert assignments(meter_id) == [registry.customer_a], "history outlives the assignment"
    events = outbox_events("meter.assigned", meter_id)
    assert events[-1]["customer_id"] is None
    assert events[-1]["previous_customer_id"] == str(registry.customer_a)

    reassigned = patch(
        client, str(meter_id), token, {"customer_id": str(registry.customer_a)}, etag='W/"v4"'
    )
    assert reassigned.status_code == 200, reassigned.json()
    assert reassigned.json()["data"]["customer_id"] == str(registry.customer_a)
    assert assignments(meter_id) == [registry.customer_a]


def test_unassign_with_null_customer(client: Client, registry: Registry) -> None:
    """``customer_id: null`` means nobody owns it; an absent field leaves the owner alone."""
    _user, token = seed_user("operations_engineer", all_sites=True)
    meter_id = seed_meter(customer_id=registry.customer_a, status="active")

    untouched = patch(client, str(meter_id), token, {"firmware_version": "2.4.1"})
    assert untouched.status_code == 200, untouched.json()
    assert untouched.json()["data"]["customer_id"] == str(registry.customer_a)
    assert untouched.json()["data"]["firmware_version"] == "2.4.1"

    unassigned = patch(client, str(meter_id), token, {"customer_id": None}, etag='W/"v2"')
    assert unassigned.status_code == 200, unassigned.json()
    assert unassigned.json()["data"]["customer_id"] is None
    assert assignments(meter_id) == [registry.customer_a]


@pytest.mark.tc("TC-FR-01-4")
def test_stale_if_match_412_and_missing_428(client: Client, registry: Registry) -> None:
    _user, token = seed_user("operations_engineer", all_sites=True)
    meter_id = seed_meter()

    missing = client.patch(
        f"{METERS}/{meter_id}",
        data={"status": "active"},
        content_type="application/json",
        headers=auth(token),
    )
    assert missing.status_code == 428, missing.json()
    assert missing.json()["code"] == "precondition_required"

    stale = patch(client, str(meter_id), token, {"status": "active"}, etag='W/"v9"')
    assert stale.status_code == 412, stale.json()
    assert stale.json()["code"] == "precondition_failed"

    fresh = patch(client, str(meter_id), token, {"status": "active"})
    assert fresh.status_code == 200, fresh.json()


def test_status_transitions_only_allowed(client: Client, registry: Registry) -> None:
    _user, token = seed_user("operations_engineer", all_sites=True)
    meter_id = seed_meter()

    # `provisioned` is not in the update schema: a meter is activated, never re-provisioned.
    invalid = patch(client, str(meter_id), token, {"status": "provisioned"})
    assert invalid.status_code == 422, invalid.json()

    activated = patch(client, str(meter_id), token, {"status": "active"})
    assert activated.status_code == 200, activated.json()
    assert activated.json()["data"]["status"] == "active"

    offline = patch(client, str(meter_id), token, {"status": "offline"}, etag='W/"v2"')
    assert offline.status_code == 200, offline.json()

    decommissioned = delete(client, f"{METERS}/{meter_id}", token)
    assert decommissioned.status_code == 204
    assert meter_row(meter_id)[3] == "decommissioned"

    # `decommissioned` is final: neither a reactivation nor a second decommission moves it.
    again = patch(client, str(meter_id), token, {"status": "active"}, etag='W/"v4"')
    assert again.status_code == 409, again.json()
    assert again.json()["code"] == "invalid_state_transition"
    assert delete(client, f"{METERS}/{meter_id}", token).status_code == 409


def test_decommission_blocked_while_credit_outstanding_409(
    client: Client, registry: Registry
) -> None:
    _user, token = seed_user("operations_engineer", all_sites=True)
    meter_id = seed_meter()

    meters_wiring.use_credit_probe(_FixedCredit(5))
    blocked = delete(client, f"{METERS}/{meter_id}", token)
    assert blocked.status_code == 409, blocked.json()
    assert blocked.json()["code"] == "meter_has_credit"
    assert meter_row(meter_id)[3] == "provisioned"

    # The same guard covers `status: decommissioned` in a PATCH, not only DELETE.
    patched = patch(client, str(meter_id), token, {"status": "decommissioned"})
    assert patched.status_code == 409, patched.json()

    meters_wiring.use_credit_probe(_FixedCredit(0))
    assert delete(client, f"{METERS}/{meter_id}", token).status_code == 204


@pytest.mark.tc("TC-FR-01-3")
def test_decommissioned_meter_refused_for_credit(registry: Registry) -> None:
    meter = seed_meter(customer_id=registry.customer_a, status="active")
    registry_port = MeterRegistryImpl(meters_wiring.repository())

    meter_ref = registry_port.get(meter)
    assert meter_ref.customer_id == registry.customer_a
    registry_port.assert_usable_for_credit(meter, registry.customer_a)

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE hub.meters SET status = 'decommissioned' WHERE id = %s",
            [meter],
        )
    with pytest.raises(Exception) as decommissioned:
        registry_port.assert_usable_for_credit(meter, registry.customer_a)
    assert getattr(decommissioned.value, "code", None) == "meter_decommissioned"

    other = seed_meter(serial_no="SGM-SEED-2", customer_id=registry.customer_a, status="active")
    with pytest.raises(Exception) as not_owned:
        registry_port.assert_usable_for_credit(other, uuid.uuid4())
    assert getattr(not_owned.value, "code", None) == "meter_not_owned"


def test_ops_outside_site_404(client: Client, registry: Registry) -> None:
    _user, token = seed_user("operations_engineer", sites=(SITE_A,))
    mine = seed_meter(site_id=SITE_A, serial_no="SGM-MINE")
    theirs = seed_meter(site_id=SITE_B, serial_no="SGM-THEIRS")

    assert get(client, f"{METERS}/{mine}", token).status_code == 200
    assert get(client, f"{METERS}/{theirs}", token).status_code == 404
    assert patch(client, str(theirs), token, {"status": "active"}).status_code == 404
    assert delete(client, f"{METERS}/{theirs}", token).status_code == 404

    listed = get(client, METERS, token).json()["data"]
    assert [row["id"] for row in listed] == [str(mine)]
    filtered = get(client, f"{METERS}?site_id={SITE_B}", token).json()["data"]
    assert filtered == []

    # A write for a site outside the caller's scope is refused before the row is looked at.
    assert create(client, token, serial_number="SGM-NEW", site_id=str(SITE_B)).status_code == 404


def test_customer_lists_only_own_meters(client: Client, registry: Registry) -> None:
    mine = seed_meter(customer_id=registry.customer_a, status="active")
    token = customer_token(registry.customer_a)

    listed = get(client, METERS, token).json()["data"]
    assert [row["id"] for row in listed] == [str(mine)]
    assert get(client, f"{METERS}/{mine}", token).status_code == 200

    # Somebody else's meter does not exist for this caller, and neither does a random id.
    other = seed_meter(site_id=SITE_B, serial_no="SGM-OTHER", customer_id=registry.customer_b)
    assert get(client, f"{METERS}/{other}", token).status_code == 404
    assert get(client, f"{METERS}/{uuid.uuid4()}", token).status_code == 404


def test_list_customer_meters_endpoint(client: Client, registry: Registry) -> None:
    _user, token = seed_user("support_agent", all_sites=True)
    mine = seed_meter(customer_id=registry.customer_a, status="active")
    seed_meter(site_id=SITE_B, serial_no="SGM-B", customer_id=registry.customer_b)

    path = f"/api/v1/customers/{registry.customer_a}/meters"
    listed = get(client, path, token)
    assert listed.status_code == 200, listed.json()
    assert [row["id"] for row in listed.json()["data"]] == [str(mine)]

    support_only = seed_user("operations_engineer", sites=(SITE_B,))
    outside = get(client, f"/api/v1/customers/{registry.customer_b}/meters", support_only[1])
    assert outside.status_code == 200, outside.json()
    hidden = get(client, path, support_only[1])
    assert hidden.status_code == 404, hidden.json()


def test_integrator_scope_meters_read(client: Client, registry: Registry) -> None:
    """`listMeters` allows an integrator, but an integrator is an API key (slice 15), not a seat.

    Without a key the answer is 401, which is what makes the gap visible instead of letting a
    staff-seat token stand in for a credential kind the slice has not built yet.
    """
    _user, token = seed_user("support_agent", all_sites=True)
    assert get(client, METERS, token).status_code == 200
    assert client.get(METERS).status_code == 401


@pytest.mark.django_db(transaction=True)
def test_concurrent_assign_exactly_one_wins(registry: Registry, redis_store) -> None:  # noqa: ANN001
    """Two operators hand one meter to two customers with the same `If-Match`."""
    _user, token = seed_user("operations_engineer", all_sites=True)
    meter_id = seed_meter(site_id=SITE_A, serial_no=f"SGM-RACE-{uuid.uuid4().hex[:8]}")
    target = registry.customer_a

    results: list[int] = []
    lock = threading.Lock()

    def assign() -> None:
        client = Client()
        response = patch(client, str(meter_id), token, {"customer_id": str(target)})
        with lock:
            results.append(response.status_code)

    threads = [threading.Thread(target=assign) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert results.count(200) == 1, results
    assert sorted(results)[1] in (409, 412), "the loser is a conflict, never a second write"
    assert assignments(meter_id) == [target], "one assignment row, not two"
    assert meter_row(meter_id)[2] == target


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_serial_exactly_one_wins(registry: Registry, redis_store) -> None:  # noqa: ANN001
    """Two creates with one serial: `uq_meters_serial` decides, the loser gets 409."""
    _user, token = seed_user("operations_engineer", all_sites=True)
    serial_number = f"SGM-RACE-{uuid.uuid4().hex[:8]}"
    results: list[int] = []
    lock = threading.Lock()

    def call() -> None:
        client = Client()
        response = create(client, token, serial_number=serial_number)
        with lock:
            results.append(response.status_code)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == [201, 409], results
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM hub.meters WHERE serial_no = %s", [serial_number])
        assert cursor.fetchone()[0] == 1
