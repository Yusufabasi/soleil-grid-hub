"""``/users`` and ``/roles``: the administrator's surface (FR-15, D-13, D-14).

The four privileged operations answer **202 with an approval**, never 200 with a changed row:
`createUser`, `replaceUserRoles`, `replaceUserSites` and `resetUserMfa` are proposals until a
second administrator decides them, so this module hands the body to the service and returns what
came back. `updateUser` is the one direct write — a display name, a status or a locale — and it
is audited for the same reason the others need approval (D-15).

Nothing here decides who may call what: `api/permissions.yaml` lists `administrator` alone for
all eight operations, and the permission class reads it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.identity import wiring
from hub.identity.api.cookie_views import _client_ip
from hub.identity.api.serializers import (
    MfaResetInput,
    RolesUpdateInput,
    UserCreateInput,
    UserSitesInput,
    UserUpdateInput,
)
from hub.identity.contracts import Principal
from hub.shared.api.permissions import PermissionsYamlPermission, operation
from hub.shared.errors import InvalidRequest, Unauthenticated

__all__ = [
    "RoleCollectionView",
    "UserCollectionView",
    "UserDetailView",
    "UserMfaResetView",
    "UserRolesView",
    "UserSitesView",
]


def _principal(request: Request) -> Principal:
    principal = getattr(request, "principal", None)
    if not isinstance(principal, Principal):
        raise Unauthenticated()
    return principal


def _ip(request: Request) -> str | None:
    return _client_ip(request._request)  # noqa: SLF001 - DRF wraps the Django request


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _limit(request: Request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return 25
    try:
        return max(1, min(int(raw), 100))
    except ValueError as exc:
        raise InvalidRequest() from exc


def _approval_response(approval: Any) -> Response:
    """202 with the stored proposal, and the approval's own address as ``Location``."""
    response = Response({"data": approval.document()}, status=202)
    response["Location"] = f"/api/v1/approvals/{approval.id}"
    return response


def _page(page: Any) -> Response:
    return Response(
        {
            "data": [user.document() for user in page.users],
            "meta": {
                "has_next": page.next_cursor is not None,
                "next_cursor": page.next_cursor,
                "limit": len(page.users),
            },
        }
    )


class UserCollectionView(APIView):
    """`listUsers` and `createUser`."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listUsers")
    def get(self, request: Request) -> Response:
        page = wiring.user_admin().list(
            _principal(request),
            statuses=_csv(request.query_params.get("status")),
            role=request.query_params.get("role") or None,
            query=request.query_params.get("q") or None,
            cursor=request.query_params.get("cursor"),
            limit=_limit(request),
        )
        return _page(page)

    @operation("createUser")
    def post(self, request: Request) -> Response:
        body = UserCreateInput(data=request.data)
        body.is_valid(raise_exception=True)
        approval = wiring.user_admin().create(
            _principal(request),
            email=str(body.validated_data["email"]),
            full_name=str(body.validated_data["full_name"]),
            roles=list(body.validated_data["roles"]),
            attributes=list(body.validated_data.get("attributes") or ()),
            site_ids=list(body.validated_data.get("site_ids") or ()),
            locale=str(body.validated_data.get("locale") or "fr"),
            reason=str(body.validated_data["reason"]),
            ip=_ip(request),
        )
        return _approval_response(approval)


class UserDetailView(APIView):
    """`getUser` and `updateUser`."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getUser")
    def get(self, request: Request, user_id: UUID) -> Response:
        return Response({"data": wiring.user_admin().get(_principal(request), user_id).document()})

    @operation("updateUser")
    def patch(self, request: Request, user_id: UUID) -> Response:
        body = UserUpdateInput(data=request.data)
        body.is_valid(raise_exception=True)
        user = wiring.user_admin().update(
            _principal(request),
            user_id,
            full_name=body.validated_data.get("full_name"),
            status=body.validated_data.get("status"),
            locale=body.validated_data.get("locale"),
            ip=_ip(request),
        )
        return Response({"data": user.document()})


class UserRolesView(APIView):
    """`replaceUserRoles`: always a proposal, and the last administrator cannot be demoted."""

    permission_classes = [PermissionsYamlPermission]

    @operation("replaceUserRoles")
    def put(self, request: Request, user_id: UUID) -> Response:
        body = RolesUpdateInput(data=request.data)
        body.is_valid(raise_exception=True)
        approval = wiring.user_admin().replace_roles(
            _principal(request),
            user_id,
            roles=list(body.validated_data["roles"]),
            attributes=list(body.validated_data.get("attributes") or ()),
            reason=str(body.validated_data["reason"]),
            ip=_ip(request),
        )
        return _approval_response(approval)


class UserSitesView(APIView):
    """`replaceUserSites`: a proposal too — site scope is part of what a role means here."""

    permission_classes = [PermissionsYamlPermission]

    @operation("replaceUserSites")
    def put(self, request: Request, user_id: UUID) -> Response:
        body = UserSitesInput(data=request.data)
        body.is_valid(raise_exception=True)
        approval = wiring.user_admin().replace_sites(
            _principal(request),
            user_id,
            site_ids=list(body.validated_data["site_ids"]),
            reason=str(body.validated_data["reason"]),
            ip=_ip(request),
        )
        return _approval_response(approval)


class UserMfaResetView(APIView):
    """`resetUserMfa`: the factor keeps working until a second administrator approves."""

    permission_classes = [PermissionsYamlPermission]

    @operation("resetUserMfa")
    def post(self, request: Request, user_id: UUID) -> Response:
        body = MfaResetInput(data=request.data)
        body.is_valid(raise_exception=True)
        approval = wiring.user_admin().reset_mfa(
            _principal(request),
            user_id,
            reason=str(body.validated_data["reason"]),
            ip=_ip(request),
        )
        return _approval_response(approval)


class RoleCollectionView(APIView):
    """`listRoles`: the catalogue and what each role may do, so the screen can explain itself."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listRoles")
    def get(self, request: Request) -> Response:
        roles = wiring.user_admin().roles(_principal(request))
        return Response(
            {
                "data": [role.document() for role in roles],
                "meta": {"has_next": False, "next_cursor": None, "limit": len(roles)},
            }
        )
