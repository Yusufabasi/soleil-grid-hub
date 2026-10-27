"""Prometheus metrics (docs/09 section 5).

A single process-wide registry. The request metrics are defined here and served from the
internal metrics port for the collector to scrape; they are never exposed through a Django
route. ``start_metrics_server`` is called from gunicorn's ``when_ready``, the ASGI entrypoint
and the worker commands, never twice in one process.
"""

from __future__ import annotations

import errno
import logging
import os

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, start_http_server
from prometheus_client.multiprocess import MultiProcessCollector

__all__ = ["DEFAULT_BUCKETS", "REGISTRY", "counter", "histogram", "start_metrics_server"]

logger = logging.getLogger(__name__)

# The counters below are declared at *import*, which is before any entrypoint could have called
# `start_metrics_server`. In a container that sets `PROMETHEUS_MULTIPROC_DIR` the first counter
# opens a mmapped file in that directory immediately, so the directory has to exist by then: a
# short-lived command (`manage.py migrate`, the sweepers) never reaches the server call, and on a
# fresh /tmp tmpfs it would die with FileNotFoundError before it read a single argument.
_MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
if _MULTIPROC_DIR:
    os.makedirs(_MULTIPROC_DIR, exist_ok=True)

# Golden-signal buckets from docs/09 section 5 (ack latency targets of the system).
DEFAULT_BUCKETS = (0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0, 8.0)


def counter(name: str, labelnames: list[str], documentation: str = "") -> Counter:
    """Declare a counter on the process registry (slice 05 consumes this helper)."""
    return Counter(name, documentation or name, labelnames)


def histogram(
    name: str,
    labelnames: list[str],
    buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    documentation: str = "",
) -> Histogram:
    """Declare a histogram with the shared latency buckets."""
    return Histogram(name, documentation or name, labelnames, buckets=buckets)


# http_requests_total{service,route,code}: the client appends the _total suffix itself.
HTTP_REQUESTS = counter(
    "http_requests", ["service", "route", "code"], "HTTP requests served by templated route"
)
HTTP_DURATION = histogram(
    "http_request_duration_seconds",
    ["service", "route", "code"],
    DEFAULT_BUCKETS,
    "HTTP request duration in seconds",
)

_started = False


def start_metrics_server(port: int | None = None) -> None:
    """Serve the registry on the internal metrics port in a daemon thread.

    Multi-process deployments set ``PROMETHEUS_MULTIPROC_DIR``: every process then writes one
    file per worker into that directory and the server exposes a ``MultiProcessCollector`` over
    all of them, so the pool's metrics are complete whichever process happens to hold the port.

    Exactly one process in a container can bind the port, and losing that race must not take a
    worker down: ``uvicorn --workers`` starts independent processes that each import the ASGI
    module, so the second one is expected to find the port taken. It logs and returns; the
    winner is already serving the shared registry, so no series is lost.
    """
    global _started  # noqa: PLW0603 - the metrics port is bound exactly once per process
    if _started:
        return

    registry = REGISTRY
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir:
        # Workers open one mmapped file each in this directory from their first counter, so it
        # must exist before any of them runs; the image's /tmp is writable and per container.
        os.makedirs(multiproc_dir, exist_ok=True)
        registry = CollectorRegistry()
        # The class ships no annotations (prometheus_client.multiprocess).
        MultiProcessCollector(registry)  # type: ignore[no-untyped-call]

    listen_port = port if port is not None else int(os.environ.get("HUB_METRICS_PORT", "9102"))
    try:
        # noqa below: the metrics port is internal-only, firewalled from the edge (docs/09 5).
        start_http_server(listen_port, addr="0.0.0.0", registry=registry)  # noqa: S104
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        logger.warning(
            "metrics port already bound in this container; this process serves no metrics",
            extra={"port": listen_port},
        )
    _started = True
