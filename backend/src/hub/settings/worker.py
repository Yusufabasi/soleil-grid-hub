"""hub-worker: queue consumers, relay and scheduler, database role hub_worker (docs/04 10.10).

No URLConf: the worker serves no HTTP.
"""

from hub.settings.base import *  # noqa: F403
from hub.settings.base import build_database

SERVICE_NAME = "hub-worker"

DATABASES = {
    "default": build_database("HUB_DB_WORKER_URL", app_name="hub-worker"),
}
