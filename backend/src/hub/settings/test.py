"""Test settings: the api deployable with the test database alias (docs/03 section 1, A-19).

The module is imported by pytest before any fixture can set the environment, so it supplies
development defaults for the variables that are required everywhere. They are ``setdefault``
only: a real environment (or a local ``.env``) always wins, and nothing here is a secret.
The ``fixtures`` database alias and the ``OffsetClock`` time control exist only in test and
staging settings, never in production (A-19).
"""

import os

_TEST_DEFAULTS = {
    "HUB_ENV": "test",
    "HUB_SECRET_KEY": "test-settings-only-not-a-secret-0123456789-abcdefghijkl",
    "HUB_ALLOWED_HOSTS": "localhost",
    "SITE_ORIGIN": "http://localhost:8080",
    "HUB_REDIS_URL": "redis://localhost:6379/0",
    "HUB_DB_API_URL": "postgresql+psycopg://hub_api:dev@localhost:5432/hub_rw",
    "HUB_DB_API_REPLICA_URL": "postgresql+psycopg://hub_api:dev@localhost:5432/hub_ro",
    "HUB_TEST_DB_URL": "postgresql+psycopg://hub_api:dev@localhost:5432/hub_test",
    # A real collection channel, so a suite can initiate a top-up on `mpesa_cd` and deliver
    # its webhook on the same provider's route: the two names have to be the same provider,
    # or the delivery can never find the top-up it pays for. The secret is the development
    # stub's, because no real provider is reachable from a test.
    "HUB_PROVIDER_WEBHOOK_SECRETS": (
        '{"synthetic": ["dev-synthetic-webhook-secret"],'
        ' "mpesa_cd": ["dev-synthetic-webhook-secret"]}'
    ),
}
for _name, _value in _TEST_DEFAULTS.items():
    os.environ.setdefault(_name, _value)

from hub.settings.api import *  # noqa: E402, F403
from hub.settings.base import build_database  # noqa: E402

SERVICE_NAME = "hub-test"

DATABASES = {
    "default": build_database("HUB_TEST_DB_URL", app_name="hub-test"),
}
