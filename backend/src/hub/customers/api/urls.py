"""Routes of the customers module. The URL name is the ``operationId`` (docs/03 7.7).

Only operations ``api/openapi.yaml`` declares are mounted (executor guide rule 4): sites and
plans here, customers from Task 02.3, tariff versions from 02.4, meters from 02.6.
"""

from typing import Any

from django.urls import path

from hub.customers.api.views import (
    CustomerCollectionView,
    CustomerDetailView,
    PlanCollectionView,
    PlanDetailView,
    PlanTariffCollectionView,
    SiteCollectionView,
    SiteDetailView,
)

__all__ = ["urlpatterns"]

urlpatterns: list[Any] = [
    path("customers", CustomerCollectionView.as_view(), name="listCustomers"),
    path(
        "customers/<uuid:customer_id>",
        CustomerDetailView.as_view(),
        name="getCustomer",
    ),
    path("sites", SiteCollectionView.as_view(), name="listSites"),
    path("sites/<uuid:site_id>", SiteDetailView.as_view(), name="getSite"),
    path("plans", PlanCollectionView.as_view(), name="listPlans"),
    path("plans/<uuid:plan_id>", PlanDetailView.as_view(), name="getPlan"),
    path(
        "plans/<uuid:plan_id>/tariffs",
        PlanTariffCollectionView.as_view(),
        name="listPlanTariffs",
    ),
]
