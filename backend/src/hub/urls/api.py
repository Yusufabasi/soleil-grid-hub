"""URLConf of the api pool (be_api). Routes arrive slice by slice (D-04, docs/03 7.7)."""

from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("hub.shared.api.urls")),
]
