"""The synthetic provider's probe loop: one signed, real delivery per interval (D-39, NFR-01).

The probe exists because "the webhook path is up" is not something a `/health` endpoint can
answer: the hub's liveness depends on the balancer routing `/api/v1/webhooks/providers/` to the
ingest pool, on the signature verifying, on the database accepting the row and on the ingest role
holding its grants. So the probe posts a *real* signed `payment.confirmed` for the registered
synthetic provider through the real route, at the interval the alerting rule reads, and does
nothing else.

Two properties matter:

* **A failure is data, not an exception.** The hub answering 503 during a deploy is exactly what
  the probe is watching for, so it records `status=503` and keeps going (Task T1.5). The loop
  stops only when the caller asks it to.
* **The amount is the smallest one there is** (1 minor unit, USD). Synthetic activity is probe
  traffic, and a probe that moved real money would be a probe nobody dares run.

The synthetic top-up id comes from `STUB_PROBE_TOP_UP_ID`: the hub side (a top-up opened for the
probe) is slice 17's work, so until then the probe is exercised against a fake hub and the working
value is supplied by whoever runs it. The probe never inspects the hub's answer beyond its status
code — it has no privileges and no view.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from provider_stub.deliver import Sender, http_sender
from provider_stub.signing import build_headers

__all__ = ["PROBE_PROVIDER", "ProbeResult", "probe_once", "run"]

logger = logging.getLogger("provider_stub.probe")

# The provider code the probe registers (D-39). It is deliberately not one of the three real
# channels: synthetic traffic must be separable in the hub's own logs and reports.
PROBE_PROVIDER = "synthetic"

# One minor unit of USD: enough to exercise the whole path, too little to be worth stealing.
PROBE_AMOUNT_MINOR = 1
PROBE_CURRENCY = "USD"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What one probe delivery produced; the loop's own metric."""

    region: str
    event_id: str
    status: int | None
    latency_ms: float


def probe_once(
    *,
    hub_url: str,
    region: str,
    secret: bytes,
    top_up_id: str,
    clock: Callable[[], float] = time.time,
    sender: Sender = http_sender,
    event_id: str | None = None,
) -> ProbeResult:
    """Send one signed confirmation for the synthetic provider and report what came back."""
    moment = clock()
    timestamp = int(moment)
    identifier = event_id or f"probe_{timestamp}"
    body = json.dumps(
        {
            "event_id": identifier,
            "type": "payment.confirmed",
            "occurred_at": datetime.fromtimestamp(moment, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "data": {
                "provider_reference": identifier,
                "external_reference": top_up_id,
                "amount": {"amount_minor": PROBE_AMOUNT_MINOR, "currency": PROBE_CURRENCY},
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    headers = build_headers(
        secret=secret, timestamp=timestamp, event_id=identifier, body=body
    )
    url = f"{hub_url.rstrip('/')}/api/v1/webhooks/providers/{PROBE_PROVIDER}"
    started = time.perf_counter()
    try:
        status = sender(url, body, headers)
    finally:
        latency_ms = (time.perf_counter() - started) * 1000
    result = ProbeResult(
        region=region, event_id=identifier, status=status, latency_ms=round(latency_ms, 3)
    )
    # The alert parses this line (`probe_result`): one JSON object per probe, no secrets, no
    # full phone numbers, and the status the hub answered rather than a boolean.
    logger.info(
        "probe_result",
        extra={
            "probe_result": {
                "status": result.status,
                "latency_ms": result.latency_ms,
                "region": result.region,
                "event_id": result.event_id,
            }
        },
    )
    return result


def run(
    *,
    hub_url: str,
    region: str,
    secret: bytes,
    top_up_id: str,
    interval: float = 30.0,
    clock: Callable[[], float] = time.time,
    sender: Sender = http_sender,
    iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Probe every ``interval`` seconds; ``iterations`` bounds it for a test or a demo.

    Both the clock and the sleep are injectable, which is what lets a test prove "three intervals
    produce three deliveries" without spending three intervals waiting.
    """
    results: list[dict[str, Any]] = []
    sent = 0
    while iterations is None or sent < iterations:
        probe = probe_once(
            hub_url=hub_url,
            region=region,
            secret=secret,
            top_up_id=top_up_id,
            clock=clock,
            sender=sender,
            event_id=f"probe_{int(clock())}_{sent}",
        )
        results.append(
            {
                "event_id": probe.event_id,
                "status": probe.status,
                "latency_ms": probe.latency_ms,
                "region": probe.region,
            }
        )
        sent += 1
        if iterations is not None and sent >= iterations:
            break
        sleep(interval)
    return results
