from django.apps import AppConfig


class MetersConfig(AppConfig):
    """The `meters` module (docs/03 section 2)."""

    name = "hub.meters"
    label = "meters"

    def ready(self) -> None:
        """Publish the meter counter ``customers`` needs for a site's ``meter_count``.

        Direction matters: ``hub.customers`` may import only this module's contracts and
        events (import-linter), so the adapter is registered here instead of being imported
        there. Nothing touches the database at start-up — the counter only runs when a site
        document is read.
        """
        from hub.customers.wiring import use_meter_counter  # noqa: PLC0415
        from hub.meters.infrastructure.repositories import MeterRepository  # noqa: PLC0415

        use_meter_counter(MeterRepository())
