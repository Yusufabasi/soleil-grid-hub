"""URLConf of the stream pool (be_stream): /api/v1/dashboard/stream only (D-04, D-18)."""

from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("hub.shared.api.urls")),
]
