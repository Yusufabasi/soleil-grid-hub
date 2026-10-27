"""Routes of the payments module on the webhooks pool (be_webhooks, D-04).

The endpoint is a plain Django function view: the signature covers the raw bytes, and DRF's
parsers would consume them while validating. ``<str:provider>`` rather than a converter with a
choices list, because the handler must answer 404 problem+json for an unknown provider rather
than Django's HTML 404.
"""

from django.urls import path

from hub.payments.api.webhooks import provider_webhook

urlpatterns = [
    path(
        "webhooks/providers/<str:provider>",
        provider_webhook,
        name="provider-webhook",
    ),
]
