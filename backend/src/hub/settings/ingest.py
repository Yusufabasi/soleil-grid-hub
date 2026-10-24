"""hub-ingest: webhook and telemetry pools, database role hub_ingest (docs/04 10.10).

``HUB_INGEST_ROLE`` is required and selects the URLConf: one deployable run as two
processes (D-04).
"""

from django.core.exceptions import ImproperlyConfigured

from hub.settings.base import *  # noqa: F403
from hub.settings.base import build_database
from hub.shared.config import env

HUB_INGEST_ROLE = env.str("HUB_INGEST_ROLE")
if HUB_INGEST_ROLE not in {"webhooks", "telemetry"}:
    raise ImproperlyConfigured(
        "HUB_INGEST_ROLE must be 'webhooks' or 'telemetry', got "
        f"{HUB_INGEST_ROLE!r} (docs/03 section 1.1)"
    )

ROOT_URLCONF = "hub.urls.webhooks" if HUB_INGEST_ROLE == "webhooks" else "hub.urls.telemetry"
SERVICE_NAME = "hub-ingest"

DATABASES = {
    "default": build_database("HUB_DB_INGEST_URL", app_name="hub-ingest"),
}
