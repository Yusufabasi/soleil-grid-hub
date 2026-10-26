"""Routes of the customers module. The URL name is the ``operationId`` (docs/03 7.7).

Only operations ``api/openapi.yaml`` declares are mounted (executor guide rule 4): sites and
plans here, customers and their meters from Task 02.3 and 02.6, tariffs from 02.4.
"""

from typing import Any

from django.urls import path

from hub.customers.api.views import (
    PlanCollectionView,
    PlanDetailView,
    SiteCollectionView,
    SiteDetailView,
)

__all__ = ["urlpatterns"]

urlpatterns: list[Any] = [
    path("sites", SiteCollectionView.as_view(), name="listSites"),
    path("sites/<uuid:site_id>", SiteDetailView.as_view(), name="getSite"),
    path("plans", PlanCollectionView.as_view(), name="listPlans"),
    path("plans/<uuid:plan_id>", PlanDetailView.as_view(), name="getPlan"),
]
