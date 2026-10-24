"""ASGI entrypoint of the stream pool: uvicorn hub.asgi_stream:application (docs/03 1.1)."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hub.settings.stream")

from django.conf import settings  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402

from hub.shared.logging import configure_logging  # noqa: E402
from hub.shared.metrics import start_metrics_server  # noqa: E402
from hub.shared.tracing import configure_tracing  # noqa: E402

configure_logging(service=settings.SERVICE_NAME)
configure_tracing(settings.SERVICE_NAME)
start_metrics_server()

application = get_asgi_application()
