"""Routes of the payments module on the api pool (be_api).

The provider webhook is deliberately not here: it belongs to the webhooks pool (D-04), so this
URLConf is mounted by ``hub.urls.api`` and the webhook by ``hub.urls.webhooks``. One route set
served by both pools would put a public HMAC endpoint behind the staff balancer.
"""

from django.urls import path

from hub.payments.api.views import TopUpCancelView, TopUpCollectionView, TopUpDetailView

urlpatterns = [
    path("top-ups", TopUpCollectionView.as_view(), name="top-ups"),
    path("top-ups/<uuid:top_up_id>", TopUpDetailView.as_view(), name="top-up"),
    path("top-ups/<uuid:top_up_id>/cancel", TopUpCancelView.as_view(), name="top-up-cancel"),
]
