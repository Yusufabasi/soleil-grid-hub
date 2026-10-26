"""`/meters` and `/customers/{id}/meters` (Task 02.6).

The permission verdict is ``api/permissions.yaml``'s, through ``@operation`` and
``PermissionsYamlPermission``; the projection is the service's, through the principal. Nothing
here decides who may see what: the view parses, calls one use case and renders the answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.identity.contracts import Principal
from hub.meters import wiring
from hub.meters.api.serializers import (
    MeterCreateInput,
    MeterUpdateInput,
    decode_meter_cursor,
    encode_meter_cursor,
    meter_document,
)
from hub.meters.application.registry import (
    DecommissionMeter,
    GetMeter,
    ListCustomerMeters,
    ListMeters,
    MeterInput,
    MeterQuery,
    RegisterMeter,
    UpdateMeter,
)
from hub.meters.contracts import MeterRow
from hub.shared.api.concurrency import etag_for, require_if_match
from hub.shared.api.permissions import PermissionsYamlPermission, operation
from hub.shared.errors import InvalidRequest, Unauthenticated

__all__ = [
    "CustomerMeterCollectionView",
    "MeterCollectionView",
    "MeterDetailView",
]


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


def _uuid_param(raw: str | None) -> UUID | None:
    """A uuid query filter, or ``invalid_request`` — never a silent "no filter"."""
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise InvalidRequest() from exc


def _query(request: Request) -> MeterQuery:
    params = request.query_params
    statuses = [part.strip() for part in (params.get("status") or "").split(",") if part.strip()]
    if params.get("sort") or params.get("last_seen_before"):
        # The contract lists both, and neither can be honoured yet: the registry pages on `id`
        # only and `last_seen_at` is `meter_state`'s (slice 05). Refusing says so, where
        # ignoring the parameter would answer a different question than the one asked.
        raise InvalidRequest()
    return MeterQuery(
        statuses=statuses,
        site_id=_uuid_param(params.get("site_id")),
        customer_id=_uuid_param(params.get("customer_id")),
        serial_no=params.get("serial_number"),
        q=params.get("q"),
        after_id=decode_meter_cursor(params.get("cursor")),
        limit=_limit(request),
    )


def _page(rows: Sequence[MeterRow], cursor: UUID | None, limit: int) -> Response:
    return Response(
        {
            "data": [meter_document(row) for row in rows],
            "meta": {
                "has_next": cursor is not None,
                "next_cursor": encode_meter_cursor(cursor),
                "limit": limit,
            },
        }
    )


class MeterCollectionView(APIView):
    """`listMeters` and `createMeter`."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listMeters")
    def get(self, request: Request) -> Response:
        principal = _principal(request)
        query = _query(request)
        rows, cursor = wiring.meter_service().list(principal, ListMeters(query=query))
        return _page(rows, cursor, query.limit)

    @operation("createMeter")
    def post(self, request: Request) -> Response:
        payload = MeterCreateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        meter = wiring.meter_service().register(
            _principal(request),
            RegisterMeter(
                meter=MeterInput(
                    serial_number=str(data["serial_number"]),
                    site_id=data["site_id"],
                    hardware_model=str(data["hardware_model"]),
                    customer_id=data.get("customer_id"),
                    firmware=data.get("firmware_version"),
                )
            ),
        )
        response = Response({"data": meter_document(meter)}, status=201)
        response["Location"] = f"/api/v1/meters/{meter.id}"
        return response


class MeterDetailView(APIView):
    """`getMeter`, `updateMeter` and `decommissionMeter`."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getMeter")
    def get(self, request: Request, meter_id: UUID) -> Response:
        row = wiring.meter_service().get(_principal(request), GetMeter(meter_id=meter_id))
        response = Response({"data": meter_document(row)})
        response["ETag"] = etag_for(row.version)
        return response

    @operation("updateMeter")
    def patch(self, request: Request, meter_id: UUID) -> Response:
        principal = _principal(request)
        service = wiring.meter_service()
        current = service.get(principal, GetMeter(meter_id=meter_id))
        # 428 when the header is missing, 412 when it names another version: the race that
        # slips past both is the repository's `WHERE version = %s` (409 version_conflict).
        require_if_match(request, current.version)

        payload = MeterUpdateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        updated = service.update(
            principal,
            UpdateMeter(
                meter_id=meter_id,
                expected_version=current.version,
                customer_id=data.get("customer_id"),
                customer_supplied="customer_id" in data,
                site_id=data.get("site_id"),
                status=data.get("status"),
                firmware=data.get("firmware_version"),
                firmware_supplied="firmware_version" in data,
            ),
        )
        response = Response({"data": meter_document(updated)})
        response["ETag"] = etag_for(updated.version)
        return response

    @operation("decommissionMeter")
    def delete(self, request: Request, meter_id: UUID) -> Response:
        wiring.meter_service().decommission(
            _principal(request), DecommissionMeter(meter_id=meter_id)
        )
        return Response(status=204)


class CustomerMeterCollectionView(APIView):
    """`listCustomerMeters`: the meters a customer holds, and never another customer's."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listCustomerMeters")
    def get(self, request: Request, customer_id: UUID) -> Response:
        query = _query(request)
        rows, cursor = wiring.meter_service().list_for_customer(
            _principal(request), ListCustomerMeters(customer_id=customer_id, query=query)
        )
        return _page(rows, cursor, query.limit)
