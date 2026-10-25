"""The five approval operations (D-15): the queue, one approval, and the two decisions.

Every handler is thin: validate against the contract, call one use case, return one document.
The interesting rules — who may decide what, the conflict check, the payload hash, the
transaction that executes an approved action — live in ``application.approvals``, so the API
cannot be the place where four eyes are quietly relaxed.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.identity import wiring
from hub.identity.api.cookie_views import _client_ip
from hub.identity.api.serializers import ApprovalCreateInput, ApprovalDecisionInput
from hub.identity.contracts import Principal
from hub.shared.api.permissions import PermissionsYamlPermission, operation
from hub.shared.errors import InvalidRequest, Unauthenticated

__all__ = [
    "ApprovalApproveView",
    "ApprovalCollectionView",
    "ApprovalDetailView",
    "ApprovalRejectView",
]


def _principal(request: Request) -> Principal:
    principal = getattr(request, "principal", None)
    if not isinstance(principal, Principal):
        raise Unauthenticated()
    return principal


def _ip(request: Request) -> str | None:
    return _client_ip(request._request)  # noqa: SLF001 - DRF wraps the Django request


def _csv(value: str | None) -> list[str]:
    """A comma-separated filter parameter, trimmed and without empties."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise InvalidRequest() from exc


def _limit(request: Request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return 25
    try:
        limit = int(raw)
    except ValueError as exc:
        raise InvalidRequest() from exc
    return max(1, min(limit, 100))


class ApprovalCollectionView(APIView):
    """`listApprovals` and `createApproval`."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listApprovals")
    def get(self, request: Request) -> Response:
        page = wiring.approvals().list(
            _principal(request),
            statuses=_csv(request.query_params.get("status")),
            kinds=_csv(request.query_params.get("kind")),
            maker_id=_uuid(request.query_params.get("maker_id")),
            cursor=request.query_params.get("cursor"),
            limit=_limit(request),
        )
        return Response(
            {
                "data": [approval.document() for approval in page.approvals],
                "meta": {
                    "has_next": page.next_cursor is not None,
                    "next_cursor": page.next_cursor,
                    "limit": _limit(request),
                },
            }
        )

    @operation("createApproval")
    def post(self, request: Request) -> Response:
        payload = ApprovalCreateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        approval = wiring.approvals().propose(
            _principal(request),
            kind=str(payload.validated_data["kind"]),
            payload=dict(payload.validated_data["payload"]),
            reason=str(payload.validated_data["reason"]),
            subject_id=payload.validated_data.get("subject_id"),
            ip=_ip(request),
        )
        response = Response({"data": approval.document()}, status=202)
        response["Location"] = f"/api/v1/approvals/{approval.id}"
        return response


class ApprovalDetailView(APIView):
    """`getApproval`: the stored proposal and, once executed, its result."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getApproval")
    def get(self, request: Request, approval_id: Any) -> Response:
        approval = wiring.approvals().get(_principal(request), approval_id)
        return Response({"data": approval.document()})


class ApprovalApproveView(APIView):
    """`approveApproval`: decide, execute and consume in one transaction."""

    permission_classes = [PermissionsYamlPermission]

    @operation("approveApproval")
    def post(self, request: Request, approval_id: Any) -> Response:
        payload = ApprovalDecisionInput(data=request.data)
        payload.is_valid(raise_exception=True)
        approval = wiring.approvals().approve(
            _principal(request),
            approval_id,
            payload_hash=payload.validated_data.get("payload_hash"),
            note=payload.validated_data.get("note"),
            ip=_ip(request),
        )
        return Response({"data": approval.document()})


class ApprovalRejectView(APIView):
    """`rejectApproval`: the proposal is closed; the maker starts again."""

    permission_classes = [PermissionsYamlPermission]

    @operation("rejectApproval")
    def post(self, request: Request, approval_id: Any) -> Response:
        payload = ApprovalDecisionInput(data=request.data)
        payload.is_valid(raise_exception=True)
        approval = wiring.approvals().reject(
            _principal(request),
            approval_id,
            note=payload.validated_data.get("note"),
            ip=_ip(request),
        )
        return Response({"data": approval.document()})
