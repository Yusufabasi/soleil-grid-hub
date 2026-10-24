from django.apps import AppConfig


class HubdbConfig(AppConfig):
    """Owns the migration chain only; it has no models (ADR-0020)."""

    name = "hub.hubdb"
    label = "hubdb"
