"""URLConf of the webhooks pool (be_webhooks): /api/v1/webhooks/providers/* only (D-04).

This pool holds the signed provider endpoints. They are unreachable from the api pool on
purpose: an HMAC endpoint is public by nature, and the balancer routes it to its own workers so
a burst of provider deliveries cannot starve the staff console.
"""

from django.urls import include, path

# Unmatched paths and unconverted failures answer problem+json, never an HTML page.
handler404 = "hub.shared.api.problem.handler404"
handler500 = "hub.shared.api.problem.handler500"

urlpatterns = [
    path("api/v1/", include("hub.shared.api.urls")),
    path("api/v1/", include("hub.payments.api.webhook_urls")),
]
