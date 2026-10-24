"""Settings for ``manage.py migrate`` only, as the migrator role (D-49, docs/04 10.5).

Runbook: point ``HUB_MIGRATE_URL`` at a superuser login for ``migrate hubdb 0000`` once on
a new environment (the baseline creates roles and grants), then use the ``migrator`` login
for everything after. No URLConf: migrate serves no HTTP.
"""

from hub.settings.base import *  # noqa: F403
from hub.settings.base import build_database

SERVICE_NAME = "hub-migrate"

DATABASES = {
    "default": build_database("HUB_MIGRATE_URL", app_name="hub-migrate"),
}
