from pathlib import Path

from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    """The `payments` module (docs/03 section 2)."""

    name = "hub.payments"
    label = "payments"

    def ready(self) -> None:
        """Publish the sweepers, the replay handler and this module's error strings.

        `hub.shared` may not import a business module, so each module registers its own
        catalogue: a French support agent reading a refused top-up must see French, with the
        ``code`` unchanged for the client to branch on (FR-16). The jobs are contributed the
        same way `ledger` contributes its nightly crons: the registry knows the names, and the
        scheduler never imports `payments` (docs/03 section 0).
        """
        from hub.payments.wiring import register_jobs  # noqa: PLC0415
        from hub.shared.api.problem import register_catalogue  # noqa: PLC0415

        register_jobs()
        register_catalogue(Path(__file__).parent / "locale")
