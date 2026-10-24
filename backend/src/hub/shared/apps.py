from django.apps import AppConfig


class SharedConfig(AppConfig):
    """The kernel app owns the technical tables outbox, consumer_inbox, idempotency_keys."""

    name = "hub.shared"
    label = "shared"
