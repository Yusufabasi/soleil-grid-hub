"""WSGI entrypoint of the ingest pools: gunicorn hub.wsgi_ingest:application (docs/03 1.1).

``HUB_INGEST_ROLE`` selects the URLConf (webhooks or telemetry).
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hub.settings.ingest")

from django.conf import settings  # noqa: E402
from django.core.wsgi import get_wsgi_application  # noqa: E402

from hub.shared.logging import configure_logging  # noqa: E402
from hub.shared.tracing import configure_tracing  # noqa: E402

configure_logging(service=settings.SERVICE_NAME)
configure_tracing(settings.SERVICE_NAME)

application = get_wsgi_application()
