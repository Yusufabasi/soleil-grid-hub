"""ASGI entrypoint of the stream pool: uvicorn hub.asgi_stream:application (docs/03 1.1)."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hub.settings.stream")

from django.core.asgi import get_asgi_application  # noqa: E402

application = get_asgi_application()
