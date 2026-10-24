"""WSGI entrypoint of the api pool: gunicorn hub.wsgi_api:application (docs/03 1.1)."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hub.settings.api")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
