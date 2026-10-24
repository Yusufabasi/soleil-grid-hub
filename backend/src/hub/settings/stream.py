"""Dashboard stream pool (be_stream): role hub_api on the read replica (docs/04 10.10, D-49)."""

from hub.settings.base import *  # noqa: F403
from hub.settings.base import build_database

ROOT_URLCONF = "hub.urls.stream"

DATABASES = {
    "default": build_database("HUB_DB_API_REPLICA_URL", app_name="hub-stream"),
}
