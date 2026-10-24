"""WSGI entrypoint of the ingest pools: gunicorn hub.wsgi_ingest:application (docs/03 1.1).

``HUB_INGEST_ROLE`` selects the URLConf (webhooks or telemetry).
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hub.settings.ingest")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
