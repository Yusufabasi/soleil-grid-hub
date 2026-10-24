"""URLConf of the telemetry pool (be_telemetry): /api/v1/telemetry/* and /api/v1/gateway/* (D-04)."""

from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("hub.shared.api.urls")),
]
