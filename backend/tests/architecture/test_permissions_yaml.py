"""The one permission class, generated from the contract files (D-14).

`api/permissions.yaml` is the single source of who may call what, and `api/openapi.yaml`
declares the operations themselves. The class is proven against both files here: every
operation in the contract has a rule (TC-FR-15-1), a role the rule does not list is refused,
and a handler the contract does not describe is refused for everyone (TC-FR-15-18).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import cast

import pytest
import yaml
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from hub.identity.contracts import Principal
from hub.shared.api.permissions import PermissionsYamlPermission, operation

REPO_ROOT = Path(__file__).resolve().parents[3]
OPENAPI = REPO_ROOT / "api" / "openapi.yaml"
PERMISSIONS = REPO_ROOT / "api" / "permissions.yaml"

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})

# The four staff roles and the customer role (D-13); `getUser` allows the administrator only.
DENIED_ROLES = ("support_agent", "finance_officer", "operations_engineer", "customer")


def declared_operations() -> set[str]:
    """Every ``operationId`` the contract declares."""
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    found: set[str] = set()
    for item in document["paths"].values():
        for method, declared in item.items():
            if method in HTTP_METHODS and isinstance(declared, dict):
                found.add(str(declared["operationId"]))
    return found


class GetUserView(APIView):
    """A handler that mirrors one contracted operation, gated by the permission class."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getUser")
    def get(self, request):  # noqa: ARG002 - the body is irrelevant to the permission check
        return Response({"data": {"id": str(uuid.uuid4())}})


class UndeclaredView(APIView):
    """Nothing in the contract describes this handler, so nobody may call it."""

    permission_classes = [PermissionsYamlPermission]

    def get(self, request):  # noqa: ARG002
        return Response({"data": {}})


def principal(role: str | None) -> Principal | None:
    if role is None:
        return None
    return Principal(
        subject_id=uuid.uuid4(),
        kind="staff",
        roles=frozenset({role}),
        session_id=uuid.uuid4(),
        token_version=1,
        locale="en",
    )


def call(view: type[APIView], role: str | None):
    request = APIRequestFactory().get("/api/v1/users/0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0001")
    # BearerJwtAuthentication sets this before any permission runs (Task 01.3).
    # `setattr` because the Django request type declares no such attribute; the permission
    # class reads it, exactly as the authentication class will set it.
    setattr(request, "principal", principal(role))  # noqa: B010
    return view.as_view()(request)


def problem(response) -> dict[str, object]:
    """The rendered problem document (a DRF Response is only rendered by the test client)."""
    response.render()
    return cast("dict[str, object]", json.loads(response.content))


@pytest.mark.tc("TC-FR-15-1")
def test_every_openapi_operation_is_in_permissions_yaml():
    operations = yaml.safe_load(PERMISSIONS.read_text(encoding="utf-8"))["operations"]
    contracted = declared_operations()

    assert contracted - set(operations) == set(), "operations without a rule"
    # A rule for an operation that does not exist is a typo waiting to allow nothing.
    assert set(operations) - contracted == set(), "rules for operations that do not exist"


@pytest.mark.tc("TC-FR-15-1")
@pytest.mark.parametrize("role", DENIED_ROLES)
def test_permission_class_denies_unlisted_role(role: str):
    response = call(GetUserView, role)

    assert response.status_code == 403
    assert problem(response)["code"] == "forbidden"


@pytest.mark.tc("TC-FR-15-1")
def test_permission_class_allows_the_listed_role():
    response = call(GetUserView, "administrator")

    assert response.status_code == 200


@pytest.mark.tc("TC-FR-15-1")
def test_unauthenticated_request_is_401():
    response = call(GetUserView, None)

    assert response.status_code == 401
    assert problem(response)["code"] == "unauthenticated"


@pytest.mark.tc("TC-FR-15-18")
def test_view_without_operation_is_refused():
    # Even the role that may call everything else: an undeclared handler is closed by default.
    response = call(UndeclaredView, "administrator")

    assert response.status_code == 403
    assert problem(response)["code"] == "forbidden"
