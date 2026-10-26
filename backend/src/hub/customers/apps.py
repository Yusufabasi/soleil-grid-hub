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
        from hub.customers.wiring import register_approval_executors  # noqa: PLC0415

        register_approval_executors()
