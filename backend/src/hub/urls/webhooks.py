"""URLConf of the webhooks pool (be_webhooks): /api/v1/webhooks/providers/* only (D-04)."""

from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("hub.shared.api.urls")),
]
