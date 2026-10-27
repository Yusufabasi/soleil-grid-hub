"""`/top-ups`: initiate, read, list and cancel (FR-02, docs/03 6.1).

Each handler validates, calls one use case and shapes one document; the rules that matter
(scope, limits, the quote snapshot, the state machine) live in ``payments.application``. The
permission class reads ``api/permissions.yaml`` and this module never checks a role itself.

Two transport decisions belong here rather than in a use case:

* **The Idempotency-Key is reserved before the provider is called and completed after** the
  effect is durable. The slice's window is the database's (seven days for this scope), so the
  key outlives any process.
* **Creating is 202, not 201.** The top-up exists, but the money has not arrived: the final
  state comes from the provider's webhook, and a 201 would claim a resource is finished.
"""

from __future__ import annotations

from typing import Any

from django.utils.dateparse import parse_datetime
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.identity.contracts import Principal
from hub.payments import wiring
from hub.payments.api.serializers import TopUpCreateInput, decode_cursor, page_meta
from hub.payments.application.top_ups import CancelTopUp, GetTopUp, TopUpQuery
from hub.payments.contracts import InitiateTopUp
from hub.shared.api.idempotency import (
    IdempotentOutcome,
    complete,
    idempotent_response,
    reserve,
    scope_for,
)
from hub.shared.api.permissions import PermissionsYamlPermission, operation
from hub.shared.errors import InvalidRequest, Unauthenticated

__all__ = ["TopUpCollectionView", "TopUpDetailView"]

TOP_UP_ROUTE = "top-ups"

# The statuses a caller may filter on: the schema's enum, so a typo is a 400 rather than an
# empty page that looks like "no top-ups".
STATUSES = frozenset(
    {
        "initiated",
        "pending",
        "pending_unknown",
        "confirmed",
        "failed",
        "expired",
        "cancelled",
        "reversed",
    }
)


def _principal(request: Request) -> Principal:
    principal = getattr(request, "principal", None)
    if not isinstance(principal, Principal):
        raise Unauthenticated()
    return principal


def _limit(request: Request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return 25
    try:
        return max(1, min(int(raw), 100))
    except ValueError as exc:
        raise InvalidRequest() from exc


def _instant(raw: str | None) -> Any:
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        raise InvalidRequest()
    return parsed


def _uuid(raw: str | None) -> Any:
    from uuid import UUID  # noqa: PLC0415 - one local import keeps the header short

    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise InvalidRequest() from exc


def _statuses(request: Request) -> tuple[str, ...]:
    """``?status=`` as a tuple, refusing an unknown one instead of quietly matching nothing."""
    raw = request.query_params.get("status") or ""
    chosen = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = sorted(set(chosen) - STATUSES)
    if unknown:
        raise InvalidRequest(details={"unknown": unknown})
    return chosen


def _late(request: Request) -> bool | None:
    raw = request.query_params.get("late")
    if raw is None or raw == "":
        return None
    if raw.lower() in {"true", "1"}:
        return True
    if raw.lower() in {"false", "0"}:
        return False
    raise InvalidRequest()


class TopUpCollectionView(APIView):
    """`createTopUp` (customer or integrator) and `listTopUps`."""

    permission_classes = [PermissionsYamlPermission]

    @operation("createTopUp")
    def post(self, request: Request) -> Response:
        principal = _principal(request)
        payload = TopUpCreateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        amount = data["amount"]
        command = InitiateTopUp(
            customer_id=data.get("customer_id"),
            meter_id=data["meter_id"],
            amount_minor=int(amount["amount_minor"]),
            currency=str(amount["currency"]),
            payer_msisdn=str(data["payer_msisdn"]),
            provider=str(data["provider"]),
        )

        reservation = reserve(
            request,
            scope=scope_for(request, TOP_UP_ROUTE),
            subject_customer_id=principal.customer_id,
        )
        if isinstance(reservation, IdempotentOutcome):
            # The same key with the same body: the first answer, including its 202.
            return idempotent_response(reservation)

        top_up = wiring.initiation()(command, principal)
        outcome = complete(reservation, status=202, body={"data": top_up.document()})
        response = idempotent_response(outcome)
        response["Location"] = f"/api/v1/top-ups/{top_up.id}"
        return response

    @operation("listTopUps")
    def get(self, request: Request) -> Response:
        principal = _principal(request)
        params = request.query_params
        if params.get("sort") not in (None, "", "-created_at", "created_at"):
            # One keyset order is implemented (id, which is time-ordered). Refusing is honest;
            # silently ignoring `sort` would return a page the caller did not ask for.
            raise InvalidRequest(details={"sort": params.get("sort")})
        query = TopUpQuery(
            statuses=_statuses(request),
            customer_id=_uuid(params.get("customer_id")),
            meter_id=_uuid(params.get("meter_id")),
            provider=params.get("provider"),
            provider_reference=params.get("provider_reference"),
            late=_late(request),
            created_after=_instant(params.get("created_after")),
            created_before=_instant(params.get("created_before")),
            after_id=decode_cursor(params.get("cursor")),
            limit=_limit(request),
        )
        rows, next_cursor = wiring.top_ups().list(principal, query)
        return Response(
            {
                "data": [top_up.document() for top_up in rows],
                "meta": page_meta(
                    query.limit,
                    has_next=next_cursor is not None,
                    last=rows[-1].id if rows else None,
                ),
            }
        )


class TopUpDetailView(APIView):
    """`getTopUp`, and `cancelTopUp` under ``{id}/cancel``."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getTopUp")
    def get(self, request: Request, top_up_id: Any) -> Response:
        top_up = wiring.top_ups().get(_principal(request), GetTopUp(top_up_id=top_up_id))
        response = Response({"data": top_up.document()})
        # The contract asks for no-store: a polled status must never come from a cache.
        response["Cache-Control"] = "no-store"
        return response


class TopUpCancelView(APIView):
    """`cancelTopUp`: only while no confirmation has been recorded."""

    permission_classes = [PermissionsYamlPermission]

    @operation("cancelTopUp")
    def post(self, request: Request, top_up_id: Any) -> Response:
        top_up = wiring.top_ups().cancel(_principal(request), CancelTopUp(top_up_id=top_up_id))
        return Response({"data": top_up.document()})
