"""`GET /audit-log`: the administrator's read of the hash chain (D-14, D-22).

The view is deliberately thin: it turns the query string into a query, calls the use case and
shapes the envelope. Everything that decides *what* is returned — the paging, the filters and
the `audit.read` row — belongs to ``ListAuditLog``.

Only the administrator may call it: `api/permissions.yaml` lists that role for
`listAuditLog`, and the schema grants `audit.read` to nobody else either (Plan notes 2).
"""

from __future__ import annotations

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.admin.api.serializers import encode_cursor, entry_document, parse_query
from hub.admin.wiring import list_audit_log
from hub.identity.contracts import Principal
from hub.shared.api.permissions import PermissionsYamlPermission, operation
from hub.shared.errors import Unauthenticated

__all__ = ["AuditLogView"]


def _principal(request: Request) -> Principal:
    """The caller the authentication class attached; 401 when there is none."""
    principal = getattr(request, "principal", None)
    if not isinstance(principal, Principal):
        raise Unauthenticated()
    return principal


class AuditLogView(APIView):
    """`listAuditLog`: one stream, one page, and a record that the page was read."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listAuditLog")
    def get(self, request: Request) -> Response:
        # `.dict()` because a QueryDict iterates lists: every parameter here is single-valued
        # (`outcome` is comma-separated, per the contract).
        query = parse_query(request.query_params.dict())
        page = list_audit_log().run(_principal(request), query)
        return Response(
            {
                "data": [entry_document(entry) for entry in page.entries],
                "meta": {
                    "has_next": page.next_after_seq is not None,
                    "next_cursor": encode_cursor(page.next_after_seq),
                    "limit": query.limit,
                },
            }
        )
