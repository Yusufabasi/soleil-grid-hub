"""hub-api: staff and customer API pool, database role hub_api (docs/04 10.10)."""

from hub.settings.base import *  # noqa: F403
from hub.settings.base import build_database

ROOT_URLCONF = "hub.urls.api"

SERVICE_NAME = "hub-api"

DATABASES = {
    "default": build_database("HUB_DB_API_URL", app_name="hub-api"),
    "replica": build_database("HUB_DB_API_REPLICA_URL", app_name="hub-api-ro"),
}
