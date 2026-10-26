"""`/sites`, `/plans` and `/plans/{id}/tariffs`: the registry's non-money resources (FR-01).

Each handler is thin — validate, call one use case, shape one document — because the rules
that matter (site scope, plan locking, the currency of an amount, the maker-checker decision
behind a tariff, the audit row) live in ``customers.application``. The permission class reads
``api/permissions.yaml``; this module never checks a role itself.
"""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.customers import wiring
from hub.customers.api.serializers import (
    CustomerCreateInput,
    CustomerUpdateInput,
    PlanCreateInput,
    PlanUpdateInput,
    SiteCreateInput,
    SiteUpdateInput,
    TariffCreateInput,
    customer_document,
    decode_cursor,
    decode_tariff_cursor,
    encode_cursor,
    plan_document,
    site_document,
    tariff_document,
    tariff_page_meta,
)
from hub.customers.application.customers import (
    CloseCustomer,
    CustomerQuery,
    GetCustomer,
    ListCustomers,
    RegisterCustomer,
    UpdateCustomer,
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
from hub.customers.application.tariffs import ListTariffs, ProposeTariff
from hub.identity.contracts import Principal
from hub.shared.api.concurrency import etag_for, require_if_match
from hub.shared.api.permissions import PermissionsYamlPermission, operation
from hub.shared.errors import InvalidRequest, Unauthenticated

__all__ = [
    "CustomerCollectionView",
    "CustomerDetailView",
    "PlanCollectionView",
    "PlanDetailView",
    "PlanTariffCollectionView",
    "SiteCollectionView",
    "SiteDetailView",
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


class PlanTariffCollectionView(APIView):
    """`listPlanTariffs` and `createPlanTariff` (finance proposes, a second lead decides)."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listPlanTariffs")
    def get(self, request: Request, plan_id: Any) -> Response:
        principal = _principal(request)
        plan = wiring.plans().get(principal, GetPlan(plan_id=plan_id))
        query = ListTariffs(
            plan_id=plan_id,
            after=decode_tariff_cursor(request.query_params.get("cursor")),
            limit=_limit(request),
        )
        rows, has_next = wiring.tariffs().list(principal, query)
        return Response(
            {
                "data": [tariff_document(row, currency=plan.currency) for row in rows],
                "meta": tariff_page_meta(
                    query.limit, has_next=has_next, last=rows[-1] if rows else None
                ),
            }
        )

    @operation("createPlanTariff")
    def post(self, request: Request, plan_id: Any) -> Response:
        payload = TariffCreateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        fee: Any = data.get("daily_fixed_fee")
        receipt = wiring.tariffs().propose(
            _principal(request),
            ProposeTariff(
                plan_id=plan_id,
                price_minor_per_kwh=int(data["price_per_kwh"]["amount_minor"]),
                currency=str(data["price_per_kwh"]["currency"]),
                effective_from=data["effective_from"],
                reason=str(data["reason"]),
                daily_fixed_fee_minor=int(fee["amount_minor"]) if fee else None,
                daily_fixed_fee_currency=str(fee["currency"]) if fee else None,
            ),
        )
        # 202, not 201: the price exists only once somebody else approves it.
        response = Response({"data": dict(receipt.document)}, status=202)
        response["Location"] = f"/api/v1/approvals/{receipt.id}"
        return response


def _customer_rows_to_document(rows: Any) -> list[dict[str, Any]]:
    return [customer_document(row) for row in rows]


class CustomerCollectionView(APIView):
    """`listCustomers` and `createCustomer` (support; finance and operations read)."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listCustomers")
    def get(self, request: Request) -> Response:
        principal = _principal(request)
        params = request.query_params
        statuses = [
            part.strip() for part in (params.get("status") or "").split(",") if part.strip()
        ]
        query = CustomerQuery(
            statuses=statuses,
            site_id=_uuid_param(params.get("site_id")),
            plan_id=_uuid_param(params.get("plan_id")),
            phone_e164=params.get("phone_e164"),
            after_id=decode_cursor(params.get("cursor")),
            limit=_limit(request),
        )
        page, next_cursor = wiring.customer_service().list(principal, ListCustomers(query=query))
        return Response(
            {
                "data": _customer_rows_to_document(page),
                "meta": {
                    "has_next": next_cursor is not None,
                    "next_cursor": encode_cursor(next_cursor),
                    "limit": query.limit,
                },
            }
        )

    @operation("createCustomer")
    def post(self, request: Request) -> Response:
        payload = CustomerCreateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        row = wiring.customer_service().register(
            _principal(request),
            RegisterCustomer(
                full_name=str(data["full_name"]),
                phone=str(data["phone_e164"]),
                site_id=data["site_id"],
                plan_id=data["plan_id"],
                email=data.get("email"),
                locale=str(data.get("locale") or "fr"),
            ),
        )
        response = Response({"data": customer_document(row)}, status=201)
        response["Location"] = f"/api/v1/customers/{row.id}"
        response["ETag"] = etag_for(row.version)
        return response


class CustomerDetailView(APIView):
    """`getCustomer`, `updateCustomer` and `closeCustomer`, all site-scoped."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getCustomer")
    def get(self, request: Request, customer_id: Any) -> Response:
        principal = _principal(request)
        row = wiring.customer_service().get(principal, GetCustomer(customer_id=customer_id))
        response = Response({"data": customer_document(row)})
        response["ETag"] = etag_for(row.version)
        return response

    @operation("updateCustomer")
    def patch(self, request: Request, customer_id: Any) -> Response:
        principal = _principal(request)
        service = wiring.customer_service()
        current = service.get(principal, GetCustomer(customer_id=customer_id))
        # 428 when the header is missing, 412 when it names another version: the write never
        # runs blind, and the repository's `WHERE version = %s` is the second layer.
        require_if_match(request, current.version)

        payload = CustomerUpdateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        row = service.update(
            principal,
            UpdateCustomer(
                customer_id=customer_id,
                full_name=data.get("full_name"),
                email=data.get("email"),
                locale=data.get("locale"),
                site_id=data.get("site_id"),
                plan_id=data.get("plan_id"),
                status=data.get("status"),
            ),
        )
        response = Response({"data": customer_document(row)})
        response["ETag"] = etag_for(row.version)
        return response

    @operation("closeCustomer")
    def delete(self, request: Request, customer_id: Any) -> Response:
        wiring.customer_service().close(_principal(request), CloseCustomer(customer_id=customer_id))
        return Response(status=204)


def _uuid_param(raw: str | None) -> Any:
    """A uuid query filter, or ``invalid_request`` — never a silent "no filter"."""
    if not raw:
        return None
    from uuid import UUID  # noqa: PLC0415

    try:
        return UUID(raw)
    except ValueError as exc:
        raise InvalidRequest() from exc
