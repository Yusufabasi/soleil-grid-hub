"""URLConf of the telemetry pool (be_telemetry): /api/v1/telemetry/* and /api/v1/gateway/* (D-04)."""

from django.urls import include, path

# Unmatched paths and unconverted failures answer problem+json, never an HTML page.
handler404 = "hub.shared.api.problem.handler404"
handler500 = "hub.shared.api.problem.handler500"

urlpatterns = [
    path("api/v1/", include("hub.shared.api.urls")),
]
