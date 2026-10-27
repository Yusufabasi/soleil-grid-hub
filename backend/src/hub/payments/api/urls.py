"""Routes of the payments module on the api pool (be_api).

The provider webhook is deliberately not here: it belongs to the webhooks pool (D-04), so this
URLConf is mounted by ``hub.urls.api`` and the webhook by ``hub.urls.webhooks``. One route set
served by both pools would put a public HMAC endpoint behind the staff balancer.

``webhook-events`` is the staff log (finance and operations only, per ``permissions.yaml``);
replay is a ``POST`` under the event's id, answered 202 because the work happens in a worker.
"""

from django.urls import path

from hub.payments.api.views import (
    TopUpCancelView,
    TopUpCollectionView,
    TopUpDetailView,
    WebhookEventCollectionView,
    WebhookEventDetailView,
    WebhookEventReplayView,
)

urlpatterns = [
    path("top-ups", TopUpCollectionView.as_view(), name="top-ups"),
    path("top-ups/<uuid:top_up_id>", TopUpDetailView.as_view(), name="top-up"),
    path("top-ups/<uuid:top_up_id>/cancel", TopUpCancelView.as_view(), name="top-up-cancel"),
    path("webhook-events", WebhookEventCollectionView.as_view(), name="webhook-events"),
    path(
        "webhook-events/<uuid:webhook_event_id>",
        WebhookEventDetailView.as_view(),
        name="webhook-event",
    ),
    path(
        "webhook-events/<uuid:webhook_event_id>/replay",
        WebhookEventReplayView.as_view(),
        name="webhook-event-replay",
    ),
]
