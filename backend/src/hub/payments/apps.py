from pathlib import Path

from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    """The `payments` module (docs/03 section 2)."""

    name = "hub.payments"
    label = "payments"

    def ready(self) -> None:
        """Publish this module's error strings (English and French) to the problem handler.

        `hub.shared` may not import a business module, so each module registers its own
        catalogue: a French support agent reading a refused top-up must see French, with the
        ``code`` unchanged for the client to branch on (FR-16).
        """
        from hub.shared.api.problem import register_catalogue  # noqa: PLC0415

        register_catalogue(Path(__file__).parent / "locale")
