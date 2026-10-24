from django.apps import AppConfig


class SharedConfig(AppConfig):
    """The kernel app owns the technical tables outbox, consumer_inbox, idempotency_keys."""

    name = "hub.shared"
    label = "shared"

    def ready(self) -> None:
        """Build the composition root once per process (docs/03 section 1.1)."""
        from hub.bootstrap import build_container  # noqa: PLC0415 - deferred so Django finishes loading the app registry first

        build_container()
