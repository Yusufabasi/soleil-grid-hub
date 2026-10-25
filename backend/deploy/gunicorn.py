"""Gunicorn configuration (docs/09 2.5 and 4.8).

The same file serves all three WSGI pools; the worker class, worker count and thread count
come from the environment because the pools are sized differently (api 4x4 gthread,
webhooks 2xCPU+1 sync, telemetry 3x8 gthread) and one image must run all of them.

Draining is the wrapper's job (``deploy/entrypoint-drain.sh``): it touches ``HUB_DRAIN_FILE``
so readiness answers 503 before gunicorn receives SIGTERM, which is why ``graceful_timeout``
is the full 30 s of the stop grace period.
"""

from __future__ import annotations

import os

# Safe at import time: the metrics module reads prometheus_client and the environment only.
from hub.shared.metrics import start_metrics_server

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", "4"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gthread")

# docs/09 1.4: a request may take 30 s, and a rolling replace waits at most 30 s more than
# that for in-flight work; the balancer's own timeouts are shorter, so it never waits here.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

# One JSON record per request comes from the application's structlog configuration, so
# gunicorn's plain-text access log is off: duplicating every line would break parsers.
accesslog = None
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# Never buffer request data in the arbiter, and let the OS refuse stale connections quickly.
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")

# Gunicorn 26 opens a control socket at $HOME/.gunicorn/gunicorn.ctl by default, which fails on
# the read-only image root (and an operator cannot reach it in a container anyway). Draining is
# driven by SIGTERM through entrypoint-drain.sh, so the socket is disabled, not relocated.
# The setting requires gunicorn >= 26; uv.lock pins it.
control_socket_disable = True


def when_ready(server: object) -> None:
    """Start the internal metrics port once per pod (docs/09 section 5)."""
    start_metrics_server()
