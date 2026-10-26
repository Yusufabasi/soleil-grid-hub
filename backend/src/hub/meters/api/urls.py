"""Routes of the meters module. The URL name is the ``operationId`` (docs/03 7.7).

Only operations ``api/openapi.yaml`` declares are mounted (executor guide rule 4): the meter
registry here, tokens and gateway routes from slices 05 and 06.
"""

from typing import Any

from django.urls import path

from hub.meters.api.views import (
    CustomerMeterCollectionView,
    MeterCollectionView,
    MeterDetailView,
)

__all__ = ["urlpatterns"]

urlpatterns: list[Any] = [
    path("meters", MeterCollectionView.as_view(), name="listMeters"),
    path("meters/<uuid:meter_id>", MeterDetailView.as_view(), name="getMeter"),
    path(
        "customers/<uuid:customer_id>/meters",
        CustomerMeterCollectionView.as_view(),
        name="listCustomerMeters",
    ),
]
