"""``POST /webhooks/providers/{provider}``: the one plain-Django route of the API (FR-03).

It is not a DRF view on purpose. The signature covers the **raw bytes**, and DRF's parsers
consume the stream while validating it; a view that reads ``request.body`` once and hands those
bytes to the verifier is the only way to sign exactly what arrived (docs/03 6.1).

The order of the checks is the one the contract promises, and each answer tells the sender
something different:

* **413 before a byte is read.** ``Content-Length`` over the cap is refused from the header, so
  an oversized body never reaches memory.
* **401 for a bad signature, 400 for a stale timestamp.** The signature is checked first, so a
  forger learns nothing about our clock (docs/05 5).
* **422 for a signed body that is not the envelope**, and **400 when the header names another
  event than the body**, because the two are different mistakes.
* **Nothing rejected reaches ``provider_events``.** A rejected delivery is a row in
  ``webhook_quarantine``: a hash and a length, never the body, so a forger's payload cannot
  become stored personal data.

Quarantine writes must never change the answer: a failure to log a rejection is a metric and a
warning, not a 500 that a provider would retry forever.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from hub.payments import wiring
from hub.shared.api.problem import MethodNotAllowed, problem_response, retry_after, to_problem
from hub.shared.context import request_id_var
from hub.shared.errors import DomainError, NotFound, PayloadTooLarge
from hub.shared.metrics import counter

__all__ = ["WEBHOOK_QUARANTINE_TOTAL", "provider_webhook"]

logger = logging.getLogger(__name__)

# `webhook_quarantine_total{provider,reason}`: the series the alert watches (docs/09 5).
WEBHOOK_QUARANTINE_TOTAL = counter(
    "webhook_quarantine", ["provider", "reason"], "Webhook deliveries rejected before storage"
)

# Which rejection reason a refusal is logged under; the answer keeps its own code.
#
# The vocabulary is closed by `ck_webhook_quarantine_reason` (docs/09): four reasons, and a
# header that names another event than the signed body is logged as ``malformed`` because that
# is what the delivery is — its transport and its payload do not describe the same event.
_REASONS = {
    "invalid_signature": "bad_signature",
    "stale_timestamp": "stale_timestamp",
    "malformed_webhook": "malformed",
    "webhook_id_mismatch": "malformed",
}


@csrf_exempt  # noqa: S450 - the caller is a provider with an HMAC, not a browser with cookies
def provider_webhook(request: HttpRequest, provider: str) -> HttpResponse:
    """Receive one signed delivery and answer ``accepted`` or ``duplicate``."""
    if request.method != "POST":
        return _problem(MethodNotAllowed(), request)

    if not wiring.knows_provider(provider):
        return _problem(NotFound(), request)

    cap = wiring.webhook_max_bytes()
    declared = request.headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > cap:
                return _problem(PayloadTooLarge(details={"max_bytes": cap}), request)
        except ValueError:
            return _problem(PayloadTooLarge(details={"max_bytes": cap}), request)

    body = request.body
    if len(body) > cap:
        # A chunked request has no Content-Length; the read is bounded by the same cap here.
        return _problem(PayloadTooLarge(details={"max_bytes": cap}), request)

    try:
        ack = wiring.confirm_payment().run(body, dict(request.headers.items()), provider)
    except DomainError as exc:
        reason = _REASONS.get(exc.code)
        if reason is not None:
            _quarantine(request, provider, reason=reason, body=body)
        return _problem(exc, request)

    return HttpResponse(
        _json({"data": ack.document()}),
        status=200,
        content_type="application/json",
    )


def _quarantine(request: HttpRequest, provider: str, *, reason: str, body: bytes) -> None:
    """Log the rejection: a hash, a length and the header — never the body (FR-03)."""
    try:
        WEBHOOK_QUARANTINE_TOTAL.labels(provider=provider, reason=reason).inc()
    except Exception:  # noqa: BLE001 - a metric must never fail a request
        logger.warning("payments.quarantine_metric_failed", extra={"provider": provider})

    try:
        wiring.quarantine_reservations().add(
            provider=provider,
            reason=reason,
            source_ip=_client_ip(request),
            body_sha256=hashlib.sha256(body).hexdigest(),
            body_length=len(body),
            signature_header=request.headers.get("X-Webhook-Signature"),
        )
    except Exception:  # noqa: BLE001 - the rejection stands even if the log write fails
        logger.warning(
            "payments.quarantine_write_failed",
            extra={"provider": provider, "reason": reason},
            exc_info=True,
        )


def _client_ip(request: HttpRequest) -> str | None:
    """The caller's address: the balancing proxy's header first, then the peer."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if isinstance(forwarded, str) and forwarded:
        candidate = forwarded.split(",", 1)[0].strip()
        return candidate or None
    peer = request.META.get("REMOTE_ADDR")
    return str(peer) if peer else None


def _problem(exc: Exception, request: HttpRequest) -> HttpResponse:
    """Render a problem document for a plain view, exactly as the DRF handler would."""
    status, body = to_problem(
        exc,
        instance=request.path,
        request_id=request_id_var.get() or "",
        accept_language=request.headers.get("Accept-Language"),
    )
    # A 503 has to carry Retry-After: the provider's retry policy is the whole point of the
    # answer, and DRF's handler would have added it if this were a DRF view.
    return problem_response(status, body, retry_after_seconds=retry_after(exc))


def _json(document: Any) -> bytes:
    import json  # noqa: PLC0415 - one local import keeps the module header short

    return json.dumps(document).encode("utf-8")
