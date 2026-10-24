"""Prometheus metrics (docs/09 section 5).

A single process-wide registry. The request metrics are defined here and served from the
internal metrics port for the collector to scrape; they are never exposed through a Django
route. ``start_metrics_server`` is called from gunicorn's ``when_ready``, the ASGI entrypoint
and the worker commands, never twice in one process.
"""

from __future__ import annotations

import os

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, start_http_server
from prometheus_client.multiprocess import MultiProcessCollector

__all__ = ["DEFAULT_BUCKETS", "REGISTRY", "counter", "histogram", "start_metrics_server"]

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

    Under gunicorn's pre-fork model ``PROMETHEUS_MULTIPROC_DIR`` is set and the server then
    exposes a ``MultiProcessCollector`` over the workers' files instead of this process's
    registry.
    """
    global _started  # noqa: PLW0603 - the metrics port is bound exactly once per process
    if _started:
        return
    _started = True

    registry = REGISTRY
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        # The class ships no annotations (prometheus_client.multiprocess).
        MultiProcessCollector(registry)  # type: ignore[no-untyped-call]

    listen_port = port if port is not None else int(os.environ.get("HUB_METRICS_PORT", "9102"))
    # noqa below: the metrics port is internal-only, firewalled from the edge (docs/09 section 5).
    start_http_server(listen_port, addr="0.0.0.0", registry=registry)  # noqa: S104
