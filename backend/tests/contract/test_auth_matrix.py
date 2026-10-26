"""The authorization matrix: every routed operation against every staff role (TC-FR-15-2).

The table is not written here — it is `api/permissions.yaml`, read through the same parser the
permission class uses, so a cell can only fail if the code disagrees with the contract. Each
cell is a real HTTP call with a real EdDSA token for that role, and what is asserted is the
authorization verdict, not the handler's answer: a role the contract allows must get past the
permission class (any status but 401/403), and a role it does not must not.

Only operations this build actually mounts are exercised. The OpenAPI document declares 166
operations across all sixteen slices; the other 133 have no route here, and asserting their
verdicts would be asserting that Django answers 404, which proves nothing about authorization.

A `public` row asserts the complementary half instead: the operation must stay reachable for
every staff role, because a permission class that refused a credential it does not need would
close a door the contract leaves open. Kinds this build cannot yet issue a credential for
(customer bearer, integrator and gateway API keys) are pinned separately below.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.conf import settings
from django.db import connection
from django.test import Client
from django.urls import Resolver404, resolve

from hub.identity import wiring
from hub.identity.contracts import Principal
from hub.shared.api.permissions import operation_rules
from support.principals import SeededUser, seed_staff_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

API_PREFIX = "/api/v1"
STAFF_ROLES = ("support_agent", "finance_officer", "operations_engineer", "administrator")
PUBLIC = "public"
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})

# A path parameter value that parses as a UUID and names no row: the handler's answer is 404,
# which is behind the permission class and therefore still exercises the verdict.
PLACEHOLDER = "00000000-0000-7000-8000-000000000000"


@dataclass(frozen=True)
class Operation:
    """One routed operation: its contract id, method and concrete URL."""

    operation_id: str
    method: str
    url: str
    roles: frozenset[str]


def routed_operations() -> list[Operation]:
    """Every operation `hub.urls.api` serves, with the roles the contract gives it."""
    document = yaml.safe_load(
        (Path(str(settings.HUB_CONTRACT_DIR)) / "openapi.yaml").read_text(encoding="utf-8")
    )
    rules = operation_rules()
    found: list[Operation] = []
    for path, item in document["paths"].items():
        for method, declared in item.items():
            if method not in HTTP_METHODS or not isinstance(declared, dict):
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
                )
            )
    return found


OPERATIONS = routed_operations()


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def staff() -> dict[str, SeededUser]:
    """One active account per role. No password: this file presents tokens only."""
    return {role: seed_staff_user(role) for role in STAFF_ROLES}


def token_for(user: SeededUser) -> str:
    """A real access token for the seeded account, fresh enough for any step-up operation."""
    return wiring.tokens().issue(
        user.principal,
        session_id=uuid.uuid4(),
        auth_time=datetime.now(UTC),
        acr="mfa",
    )


# The two cookie endpoints demand the same-origin marker before anything else (D-46). The
# matrix is about the role verdict, so it presents a well-formed request and then judges it.
COOKIE_ENDPOINTS = frozenset({"refreshToken", "logout"})
CSRF_HEADERS = {"HTTP_ORIGIN": str(settings.SITE_ORIGIN), "HTTP_X_HUB_CSRF": "1"}


def call(client: Client, operation: Operation, token: str | None) -> Any:
    headers: dict[str, str] = (
        dict(CSRF_HEADERS) if operation.operation_id in COOKIE_ENDPOINTS else {}
    )
    if token is not None:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return getattr(client, operation.method.lower())(operation.url, **headers)


def test_the_matrix_covers_the_routed_surface() -> None:
    """A matrix over nothing is a green light over nothing."""
    assert len(OPERATIONS) >= 30, [op.operation_id for op in OPERATIONS]
    assert {op.operation_id for op in OPERATIONS} >= {
        "getMe",
        "listUsers",
        "listAuditLog",
        "createApproval",
    }
    assert all(op.roles for op in OPERATIONS), "every routed operation has a rule"


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op.operation_id)
def test_every_role_by_every_routed_operation(
    operation: Operation, client: Client, staff: dict[str, SeededUser]
) -> None:
    """Allowed roles clear the permission class; every other staff role is refused."""
    if PUBLIC in operation.roles:
        # The row is still a real call. A public operation needs no credential, so the verdict
        # a staff token must produce is the anonymous one: the permission class neither helps
        # nor hurts. (A cookie route still answers 401 to an absent cookie; that is the
        # handler's answer and it is the same with a token.)
        anonymous = call(client, operation, None).status_code
        for role in STAFF_ROLES:
            response = call(client, operation, token_for(staff[role]))
            assert response.status_code == anonymous, (
                f"{operation.operation_id} is public, yet a {role} token changed the answer "
                f"from {anonymous} to {response.status_code}"
            )
        return

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


def test_anonymous_is_refused_everywhere_that_is_not_public(client: Client) -> None:
    """No credential: 401 on every credential-requiring GET, and the public ones stay open."""
    for operation in OPERATIONS:
        if operation.method != "GET":
            continue
        response = call(client, operation, None)
        if PUBLIC in operation.roles:
            assert response.status_code not in {401, 403}, operation.operation_id
        else:
            assert response.status_code == 401, (
                f"{operation.operation_id} answered {response.status_code} without a credential"
            )


def test_a_denied_call_is_written_to_the_audit_trail(
    client: Client, staff: dict[str, SeededUser]
) -> None:
    """The refusal itself is evidence: who asked for what, and why it was refused (S-8)."""
    agent = staff["support_agent"]
    operation = next(op for op in OPERATIONS if op.operation_id == "listUsers")

    response = call(client, operation, token_for(agent))

    assert response.status_code == 403
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT actor_type, actor_id, action, details FROM hub.audit_log "
            " WHERE action = 'authz.denied' ORDER BY seq DESC LIMIT 1"
        )
        row = cursor.fetchone()
    assert row is not None, "the permission class refused without recording it"
    actor_type, actor_id, action, details = row
    if not isinstance(details, dict):
        details = json.loads(details)
    assert (actor_type, actor_id, action) == ("user", agent.user_id, "authz.denied")
    assert details["operation"] == "listUsers"
    assert details["reason"] == "role"
    assert details["outcome"] == "denied"


def test_customer_token_for_an_unknown_customer_is_refused(client: Client) -> None:
    """A portal token names a `hub.customers` row, and a row that is gone signs nobody in.

    Since slice 02's registry the portal's principal is real (task 02.5), so the check moved
    from "a customer is not a `hub.users` row" to "this customer still exists and is active".
    The id here is random, which is the case a closed or deleted account leaves behind: the
    session is not trusted to outlive the account it names. A live customer's reach is pinned
    in `tests/integration/customers/test_scope_and_masking.py`.
    """
    customer_id = uuid.uuid4()
    token = wiring.tokens().issue(
        Principal(subject_id=customer_id, kind="customer", customer_id=customer_id),
        session_id=uuid.uuid4(),
        auth_time=datetime.now(UTC),
        acr="otp",
        amr=("otp",),
    )

    headers: Any = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    response = client.get(f"{API_PREFIX}/me", **headers)

    assert response.status_code == 401
    assert response.json()["code"] == "token_revoked"


def test_machine_operations_are_not_open_without_a_key(client: Client) -> None:
    """Integrator and gateway keys are slice 15: no key means 401, never an anonymous pass."""
    machine_only = [op for op in OPERATIONS if op.roles and op.roles <= {"integrator", "gateway"}]
    for operation in machine_only:
        assert call(client, operation, None).status_code == 401, operation.operation_id
