from pathlib import Path

from django.apps import AppConfig


class LedgerConfig(AppConfig):
    """The `ledger` module (docs/03 section 2.5)."""

    name = "hub.ledger"
    label = "ledger"
    # The models are unmanaged shims without a declared id in one case (`LedgerBalanceSnapshot`
    # has a composite key Django cannot express); this keeps the ORM's default key a bigint.
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Publish the four approval executors, the cron jobs and the module's messages.

        The executors live in `identity`'s maker-checker registry, which this module reaches
        through the kernel's named ports (``identity.register_approval_executor``) rather than
        by importing identity's wiring — the import contract allows the contracts and events
        only. `identity` is listed before `ledger` in ``INSTALLED_APPS``, so its ``ready()``
        has published those ports by the time this runs; registering later would be a wiring
        bug, not a missing feature, which is why it happens here instead of on first use.
        Nothing touches the database at start-up.
        """
        from hub.ledger.wiring import register_approval_executors, register_jobs  # noqa: PLC0415
        from hub.shared.api.problem import register_catalogue  # noqa: PLC0415

        register_approval_executors()
        register_jobs()
        # English and French strings for the codes this module raises; `hub.shared` may not
        # import `hub.ledger`, so the module registers its own catalogue.
        register_catalogue(Path(__file__).parent / "locale")
