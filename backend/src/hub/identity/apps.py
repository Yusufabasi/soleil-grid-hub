from django.apps import AppConfig

from hub.identity.infrastructure.audit import record_access_denied
from hub.shared.api.permissions import set_denial_recorder


class IdentityConfig(AppConfig):
    """The `identity` module (docs/03 section 2)."""

    name = "hub.identity"
    label = "identity"

    def ready(self) -> None:
        """Install the audit hook the permission class calls when it refuses a request.

        `hub.shared.api.permissions` may not import a business module, so it exposes a
        registration seam instead; the module that owns the audit log fills it. No database
        work happens at start-up — the hook only runs when a request is actually denied.
        """
        set_denial_recorder(record_access_denied)
