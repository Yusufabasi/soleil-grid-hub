from django.apps import AppConfig
from django.conf import settings


class IdentityConfig(AppConfig):
    """The `identity` module (docs/03 section 2)."""

    name = "hub.identity"
    label = "identity"

    def ready(self) -> None:
        """Install the audit hook the permission class calls when it refuses a request.

        `hub.shared.api.permissions` may not import a business module, so it exposes a
        registration seam instead; the module that owns the audit log fills it.

        Only the API deployable does this. The hook's import chain reaches the HTTP problem
        layer and its metrics registry, and the one-shot `migrate` container has no metrics
        directory and no permission class to feed; installing it there would fail the boot for
        nothing. `ROOT_URLCONF` names the deployment precisely: the permission class is only
        reachable through `hub.urls.api`.
        """
        if getattr(settings, "ROOT_URLCONF", "") != "hub.urls.api":
            return
        from hub.identity.infrastructure.audit import record_access_denied  # noqa: PLC0415
        from hub.shared.api.permissions import set_denial_recorder  # noqa: PLC0415

        set_denial_recorder(record_access_denied)
