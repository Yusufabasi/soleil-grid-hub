"""The registry authorization matrix (Task 02.9, TC-FR-15-2 registry half).

The table is not written here: it is `api/permissions.yaml`, read through the same parser the
permission class uses, filtered to the operations tagged **Customers** or **Meters**. Every
routed registry operation is called, once per staff role plus once with a real portal token,
and only the authorization verdict is judged — an allowed caller must get past the permission
class (any status but 401/403) and every other caller must not.

Two things this file adds beyond `test_auth_matrix.py`, which sweeps the whole surface:

* the **portal** is a first-class row here, because a customer is a real credential for the
  registry operations `permissions.yaml` opens to it (`getCustomer`, `listMeters`, …), and
  slice 02 is the first slice with customer-facing routes;
* `test_x_site_scoped_operations_never_leak_existence` pins the other half of site scope: a
  foreign id must answer **404**, never 403 — the status must not tell an outsider that the row
  exists (D-14). The contract marks the operations this applies to with `x-site-scoped`.

Operations declared for slices that are not built yet (`getCustomerWallet`, `listMeterReadings`,
`listMeterTokens`) have no route here, so they are skipped rather than asserted to be 404: a
404 proves nothing about authorization. Integrator and gateway keys are issued from slice 15;
until then their cells are pinned as *refused* for every credential this build can present.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from django.conf import settings
from django.db import connection
from django.test import Client
from django.urls import Resolver404, resolve

from hub.identity import wiring as identity_wiring
from hub.identity.contracts import Principal
from hub.shared.api.permissions import operation_rules

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

API_PREFIX = "/api/v1"
STAFF_ROLES = ("support_agent", "finance_officer", "operations_engineer", "administrator")
REGISTRY_TAGS = frozenset({"Customers", "Meters"})
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
# Parses as a UUID and names no row: the handler's 404 sits behind the permission class, so the
# verdict is still exercised. A portal token that names no customer is refused at authentication
# (401), which is why the portal fixture seeds a real row instead of reusing this value.
PLACEHOLDER = "00000000-0000-7000-8000-000000000000"


@dataclass(frozen=True)
class Operation:
    """One routed registry operation: its contract id, method, URL and allowed roles."""

    operation_id: str
    method: str
    url: str
    roles: frozenset[str]
    site_scoped: bool


def registry_operations() -> list[Operation]:
    """Every routed operation tagged Customers or Meters, with the roles the contract gives it."""
    document = yaml.safe_load(
        (Path(str(settings.HUB_CONTRACT_DIR)) / "openapi.yaml").read_text(encoding="utf-8")
    )
    rules = operation_rules()
    found: list[Operation] = []
    for path, item in document["paths"].items():
        for method, declared in item.items():
            if method not in HTTP_METHODS or not isinstance(declared, dict):
                continue
            tags = frozenset(declared.get("tags") or ())
            if not (tags & REGISTRY_TAGS):
                continue
            operation_id = declared.get("operationId")
            rule = rules.get(operation_id) if isinstance(operation_id, str) else None
            if rule is None:
                continue
            concrete = re.sub(r"\{[^}]+\}", PLACEHOLDER, path)
            try:
                resolve(API_PREFIX + concrete)
            except Resolver404:
                continue
            found.append(
                Operation(
                    operation_id=str(operation_id),
                    method=method.upper(),
                    url=API_PREFIX + concrete,
                    roles=rule.roles,
                    site_scoped=bool(declared.get("x-site-scoped", False)),
                )
            )
    return found


OPERATIONS = registry_operations()
SITE_SCOPED_IDS = frozenset(
    {
        "getCustomer",
        "updateCustomer",
        "closeCustomer",
        "listCustomerMeters",
        "listMeters",
        "createMeter",
        "getMeter",
        "updateMeter",
        "decommissionMeter",
    }
)


def test_the_registry_matrix_covers_the_slice_surface() -> None:
    """A matrix over nothing is a green light over nothing."""
    ids = {op.operation_id for op in OPERATIONS}
    assert len(OPERATIONS) >= 20, sorted(ids)
    assert ids >= {
        "listCustomers",
        "createCustomer",
        "getCustomer",
        "updateCustomer",
        "closeCustomer",
        "listSites",
        "createSite",
        "listPlans",
        "createPlanTariff",
        "listMeters",
        "createMeter",
        "decommissionMeter",
        "listCustomerMeters",
    }
    assert all(op.roles for op in OPERATIONS), "every routed registry operation has a rule"
    assert {op.operation_id for op in OPERATIONS if op.site_scoped} == SITE_SCOPED_IDS


def seed_staff(*roles: str, sites: tuple[UUID, ...] = ()) -> Principal:
    """An active staff user holding ``roles`` and scoped to ``sites``, read back from the rows."""
    from hub.identity.infrastructure.models import User, UserRole  # noqa: PLC0415

    user_id = uuid.uuid4()
    User.objects.create(
        id=user_id,
        email=f"{user_id}@example.test",
        display_name="Matrix Caller",
        status="active",
        all_sites=not sites,
        token_version=1,
    )
    for role in roles:
        UserRole.objects.create(user_id=user_id, role_code=role)
    for site_id in sites:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.user_sites (user_id, site_id) VALUES (%s, %s)", [user_id, site_id]
            )
    return identity_wiring.users().build_principal(
        user_id, session_id=uuid.uuid4(), auth_time=datetime.now(UTC), acr="mfa"
    )


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def staff() -> dict[str, Principal]:
    """One active, all-sites account per role, with a fresh token (step-up included)."""
    return {role: seed_staff(role) for role in STAFF_ROLES}


@pytest.fixture
def portal() -> str:
    """A real active customer and a portal token: an unknown subject never authenticates."""
    site_id, plan_id, customer_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency) "
            "VALUES (%s, 'MAT-A', 'Matrix site', 'USD')",
            [site_id],
        )
        cursor.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor) "
            "VALUES (%s, 'MAT-P', 'Matrix plan', 'USD', 100, 1000000)",
            [plan_id],
        )
        cursor.execute(
            "INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164, language) "
            "VALUES (%s, %s, %s, 'Matrix Customer', '+243810000700', 'fr')",
            [customer_id, site_id, plan_id],
        )
    principal = Principal(subject_id=customer_id, kind="customer", customer_id=customer_id)
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=datetime.now(UTC), acr="otp", amr=("otp",)
    )


def token_for(principal: Principal) -> str:
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=datetime.now(UTC), acr="mfa"
    )


def call(client: Client, operation: Operation, token: str) -> Any:
    return getattr(client, operation.method.lower())(
        operation.url, headers={"Authorization": f"Bearer {token}"}
    )


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op.operation_id)
def test_registry_operations_role_matrix(
    operation: Operation, client: Client, staff: dict[str, Principal], portal: str
) -> None:
    """Allowed callers clear the permission class; every other credential is refused."""
    for role in STAFF_ROLES:
        response = call(client, operation, token_for(staff[role]))
        if role in operation.roles:
            assert response.status_code not in {401, 403}, (
                f"{role} may call {operation.operation_id} per permissions.yaml, "
                f"got {response.status_code}"
            )
        else:
            assert response.status_code in {401, 403}, (
                f"{role} may not call {operation.operation_id}, got {response.status_code}"
            )

    response = call(client, operation, portal)
    if "customer" in operation.roles:
        assert response.status_code not in {401, 403}, (
            f"a customer may call {operation.operation_id} per permissions.yaml, "
            f"got {response.status_code}"
        )
    else:
        assert response.status_code in {401, 403}, (
            f"a customer may not call {operation.operation_id}, got {response.status_code}"
        )


def test_a_denied_registry_call_is_written_to_the_audit_trail(
    client: Client, staff: dict[str, Principal]
) -> None:
    """The refusal is evidence: which registry operation was denied, and why (S-8)."""
    operation = next(op for op in OPERATIONS if op.operation_id == "listCustomers")

    response = call(client, operation, token_for(staff["administrator"]))

    assert response.status_code == 403
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT action, details FROM hub.audit_log WHERE action = 'authz.denied' "
            "ORDER BY seq DESC LIMIT 1"
        )
        row = cursor.fetchone()
    assert row is not None, "the permission class refused without recording it"
    action, details = row
    if not isinstance(details, dict):
        details = json.loads(details)
    assert (action, details["operation"], details["outcome"]) == (
        "authz.denied",
        "listCustomers",
        "denied",
    )


def test_x_site_scoped_operations_never_leak_existence(client: Client) -> None:
    """A foreign-site id answers 404 on every site-scoped registry operation, never 403."""
    site_a, site_b = uuid.uuid4(), uuid.uuid4()
    plan = uuid.uuid4()
    customer_a, customer_b = uuid.uuid4(), uuid.uuid4()
    meter_a, meter_b = uuid.uuid4(), uuid.uuid4()
    with connection.cursor() as cursor:
        for site_id, code in ((site_a, "SCOPE-C"), (site_b, "SCOPE-D")):
            cursor.execute(
                "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, 'USD')",
                [site_id, code, f"Site {code[-1]}"],
            )
        cursor.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor) "
            "VALUES (%s, 'SCOPE-P2', 'Scope plan', 'USD', 100, 1000000)",
            [plan],
        )
        for customer_id, site_id, phone in (
            (customer_a, site_a, "+243810000801"),
            (customer_b, site_b, "+243810000802"),
        ):
            cursor.execute(
                "INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164, "
                "language) VALUES (%s, %s, %s, 'Scope customer', %s, 'fr')",
                [customer_id, site_id, plan, phone],
            )
        for meter_id, site_id, serial in (
            (meter_a, site_a, "SCOPE-SN-1"),
            (meter_b, site_b, "SCOPE-SN-2"),
        ):
            cursor.execute(
                "INSERT INTO hub.meters (id, serial_no, site_id, status) "
                "VALUES (%s, %s, %s, 'provisioned')",
                [meter_id, serial, site_id],
            )

    support = token_for(seed_staff("support_agent", sites=(site_a,)))
    operator = token_for(seed_staff("operations_engineer", sites=(site_a,)))

    # Customer resources: the foreign id is "not found" on every verb the contract site-scopes.
    for method, url in (
        ("get", f"{API_PREFIX}/customers/{customer_b}"),
        ("patch", f"{API_PREFIX}/customers/{customer_b}"),
        ("delete", f"{API_PREFIX}/customers/{customer_b}"),
        ("get", f"{API_PREFIX}/customers/{customer_b}/meters"),
    ):
        response = getattr(client, method)(url, headers={"Authorization": f"Bearer {support}"})
        assert response.status_code == 404, f"{method.upper()} {url}: {response.status_code}"
        assert response.status_code != 403

    # Meter resources, as an operator scoped to the other site.
    for method, url, body in (
        ("get", f"{API_PREFIX}/meters/{meter_b}", None),
        ("patch", f"{API_PREFIX}/meters/{meter_b}", {"firmware_version": "1.2.3"}),
        ("delete", f"{API_PREFIX}/meters/{meter_b}", None),
        (
            "post",
            f"{API_PREFIX}/meters",
            {"serial_number": "SCOPE-SN-9", "site_id": str(site_b), "hardware_model": "SG-1"},
        ),
    ):
        kwargs: dict[str, Any] = {"headers": {"Authorization": f"Bearer {operator}"}}
        if body is not None:
            kwargs["data"] = body
            kwargs["content_type"] = "application/json"
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 404, f"{method.upper()} {url}: {response.status_code}"

    # The lists are silence too: the foreign rows are simply absent, and the filter is empty.
    listed = client.get(f"{API_PREFIX}/meters", headers={"Authorization": f"Bearer {operator}"})
    assert listed.status_code == 200
    assert str(meter_b) not in {row["id"] for row in listed.json()["data"]}
    assert str(meter_a) in {row["id"] for row in listed.json()["data"]}
    filtered = client.get(
        f"{API_PREFIX}/meters?site_id={site_b}", headers={"Authorization": f"Bearer {operator}"}
    )
    assert filtered.json()["data"] == []

    # The refusal really is scope, not a blanket deny: the same caller reads its own site.
    own = client.get(
        f"{API_PREFIX}/customers/{customer_a}", headers={"Authorization": f"Bearer {support}"}
    )
    assert own.status_code == 200
