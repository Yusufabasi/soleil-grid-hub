from pathlib import Path

from django.apps import AppConfig


class MetersConfig(AppConfig):
    """The `meters` module (docs/03 section 2)."""

    name = "hub.meters"
    label = "meters"

    def ready(self) -> None:
        """Publish the meter counter ``customers`` needs, and take the facts it offers.

        Both directions are registrations, and the asymmetry is the architecture's: this module
        may import ``hub.customers.contracts`` (so it builds its own adapter over the two store
        Protocols), while ``hub.customers`` may not import this module at all — which is why
        its ``meter_count`` arrives as a registration here instead of an import there. Nothing
        touches the database at start-up; both adapters only run when a request needs them.
        """
        from hub.customers.wiring import (  # noqa: PLC0415
            customer_store,
            site_store,
            use_meter_counter,
            use_meter_link,
        )
        from hub.meters.application.directory import (  # noqa: PLC0415
            MeterLinkAdapter,
            RegistryFactsAdapter,
        )
        from hub.meters.infrastructure.repositories import MeterRepository  # noqa: PLC0415
        from hub.meters.wiring import register_csv_import, use_registry_facts  # noqa: PLC0415
        from hub.shared.api.problem import register_catalogue  # noqa: PLC0415

        use_meter_counter(MeterRepository())
        # The customer-to-meter link `customers` publishes in its directory (Task 02.9): the
        # rows are this module's, so the adapter is built here and installed on their seam.
        use_meter_link(MeterLinkAdapter(MeterRepository()))
        use_registry_facts(RegistryFactsAdapter(customers=customer_store(), sites=site_store()))
        # `manage.py import_registry --kind meters`, for the same reason as the counter: the
        # kernel's command dispatches through a registry instead of importing this module.
        register_csv_import()
        # The meter refusals a credit path reports (English and French): a top-up that is
        # told "meter_decommissioned" has to say so in the caller's language.
        register_catalogue(Path(__file__).parent / "locale")
