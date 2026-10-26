"""`/sites` and `/plans`: the registry's two non-money resources (FR-01).

Each handler is thin — validate, call one use case, shape one document — because the rules
that matter (site scope, plan locking, the currency of an amount, the audit row) live in
``customers.application.sites_plans``. The permission class reads ``api/permissions.yaml``;
this module never checks a role itself.
"""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.customers import wiring
from hub.customers.api.serializers import (
    PlanCreateInput,
    PlanUpdateInput,
    SiteCreateInput,
    SiteUpdateInput,
    decode_cursor,
    encode_cursor,
    plan_document,
    site_document,
)
from hub.customers.application.sites_plans import (
    CreatePlan,
    CreateSite,
    GetPlan,
    GetSite,
    ListPlans,
    ListSites,
    PlanInput,
    SiteInput,
    UpdatePlan,
    UpdateSite,
)
from hub.identity.contracts import Principal
from hub.shared.api.permissions import PermissionsYamlPermission, operation
from hub.shared.errors import InvalidRequest, Unauthenticated

__all__ = ["PlanCollectionView", "PlanDetailView", "SiteCollectionView", "SiteDetailView"]


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


def _meter_counts() -> dict[Any, int]:
    return wiring.meter_counter().per_site()


class SiteCollectionView(APIView):
    """`listSites` and `createSite` (operations and integrator)."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listSites")
    def get(self, request: Request) -> Response:
        principal = _principal(request)
        query = ListSites(
            after_id=decode_cursor(request.query_params.get("cursor")), limit=_limit(request)
        )
        page, next_cursor = wiring.sites().list(principal, query)
        counts = _meter_counts()
        return Response(
            {
                "data": [site_document(site, meter_count=counts.get(site.id, 0)) for site in page],
                "meta": {
                    "has_next": next_cursor is not None,
                    "next_cursor": encode_cursor(next_cursor),
                    "limit": query.limit,
                },
            }
        )

    @operation("createSite")
    def post(self, request: Request) -> Response:
        payload = SiteCreateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        site = wiring.sites().create(
            _principal(request),
            CreateSite(
                site=SiteInput(
                    code=str(data["code"]), name=str(data["name"]), timezone=str(data["timezone"])
                )
            ),
        )
        response = Response(
            {"data": site_document(site, meter_count=0)},
            status=201,
        )
        response["Location"] = f"/api/v1/sites/{site.id}"
        return response


class SiteDetailView(APIView):
    """`getSite` and `updateSite`."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getSite")
    def get(self, request: Request, site_id: Any) -> Response:
        site = wiring.sites().get(_principal(request), GetSite(site_id=site_id))
        return Response({"data": site_document(site, meter_count=_meter_counts().get(site.id, 0))})

    @operation("updateSite")
    def patch(self, request: Request, site_id: Any) -> Response:
        payload = SiteUpdateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        site = wiring.sites().update(
            _principal(request),
            UpdateSite(
                site_id=site_id,
                name=data.get("name"),
                timezone=data.get("timezone"),
                status=data.get("status"),
            ),
        )
        return Response({"data": site_document(site, meter_count=_meter_counts().get(site.id, 0))})


class PlanCollectionView(APIView):
    """`listPlans` and `createPlan` (finance)."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listPlans")
    def get(self, request: Request) -> Response:
        principal = _principal(request)
        query = ListPlans(
            after_id=decode_cursor(request.query_params.get("cursor")), limit=_limit(request)
        )
        page, next_cursor = wiring.plans().list(principal, query)
        return Response(
            {
                "data": [plan_document(plan) for plan in page],
                "meta": {
                    "has_next": next_cursor is not None,
                    "next_cursor": encode_cursor(next_cursor),
                    "limit": query.limit,
                },
            }
        )

    @operation("createPlan")
    def post(self, request: Request) -> Response:
        payload = PlanCreateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        plan = wiring.plans().create(
            _principal(request),
            CreatePlan(
                plan=PlanInput(
                    code=str(data["code"]),
                    name=str(data["name"]),
                    currency=str(data["currency"]),
                    min_topup_minor=int(data["min_top_up"]["amount_minor"]),
                    max_topup_minor=int(data["max_top_up"]["amount_minor"]),
                )
            ),
        )
        response = Response({"data": plan_document(plan)}, status=201)
        response["Location"] = f"/api/v1/plans/{plan.id}"
        return response


class PlanDetailView(APIView):
    """`getPlan` and `updatePlan`; the document carries the tariff in force."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getPlan")
    def get(self, request: Request, plan_id: Any) -> Response:
        principal = _principal(request)
        plan = wiring.plans().get(principal, GetPlan(plan_id=plan_id))
        tariff = wiring.plans().current_tariff(principal, plan.id)
        return Response({"data": plan_document(plan, current_tariff=tariff)})

    @operation("updatePlan")
    def patch(self, request: Request, plan_id: Any) -> Response:
        payload = PlanUpdateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        principal = _principal(request)
        plan = wiring.plans().update(
            principal,
            UpdatePlan(
                plan_id=plan_id,
                name=data.get("name"),
                min_topup_minor=data.get("min_top_up", {}).get("amount_minor"),
                max_topup_minor=data.get("max_top_up", {}).get("amount_minor"),
                status=data.get("status"),
            ),
        )
        tariff = wiring.plans().current_tariff(principal, plan.id)
        return Response({"data": plan_document(plan, current_tariff=tariff)})
