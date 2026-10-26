from pathlib import Path

from django.apps import AppConfig


class CustomersConfig(AppConfig):
    """The `customers` module (docs/03 section 2)."""

    name = "hub.customers"
    label = "customers"

    def ready(self) -> None:
        """Publish the ``tariff.change`` executor to `identity`'s maker-checker registry.

        Direction matters: a tariff is decided through `identity`'s `/approvals` routes, which
        only know the executors registered at composition time, and `identity` may import this
        module's contracts but never its application layer (import-linter). Registering here
        keeps the dependency pointing at the registry seam instead of at a business module.
        Nothing touches the database at start-up; the executor runs when a decision is made.
        """
        from hub.customers.wiring import (  # noqa: PLC0415
            register_approval_executors,
            register_csv_import,
        )
        from hub.shared.api.problem import register_catalogue  # noqa: PLC0415

        register_approval_executors()
        # `manage.py import_registry --kind customers`: the same registration pattern, because
        # the command lives in `hub.shared` and may not import this module (docs/03 3.1).
        register_csv_import()
        # The registry's own strings (English and French), merged into the one problem
        # handler: `hub.shared` may not import this module, so the module registers them.
        register_catalogue(Path(__file__).parent / "locale")
