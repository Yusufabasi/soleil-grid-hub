"""URLConf of the api pool (be_api). Routes arrive slice by slice (D-04, docs/03 7.7)."""

from django.urls import include, path

# Unmatched paths and unconverted failures answer problem+json, never an HTML page.
handler404 = "hub.shared.api.problem.handler404"
handler500 = "hub.shared.api.problem.handler500"

urlpatterns = [
    path("api/v1/", include("hub.shared.api.urls")),
    path("api/v1/", include("hub.identity.api.urls")),
    path("api/v1/", include("hub.customers.api.urls")),
    path("api/v1/", include("hub.meters.api.urls")),
    path("api/v1/", include("hub.ledger.api.urls")),
    path("api/v1/", include("hub.payments.api.urls")),
    path("api/v1/", include("hub.admin.api.urls")),
]
