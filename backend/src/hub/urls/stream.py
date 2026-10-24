"""URLConf of the stream pool (be_stream): /api/v1/dashboard/stream and health (D-04, D-18)."""

from django.urls import include, path

# Unmatched paths and unconverted failures answer problem+json, never an HTML page.
handler404 = "hub.shared.api.problem.handler404"
handler500 = "hub.shared.api.problem.handler500"

urlpatterns = [
    path("api/v1/", include("hub.shared.api.urls")),
]
