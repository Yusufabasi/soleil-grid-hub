"""The provider over HTTP: signature verification, collection initiation and status pull.

The signature half is pure (``payments.domain.signature``) and is the only part the webhook
trusts. The two calls out are deliberately small and use the standard library's HTTP client:
the provider API is a form POST and a JSON GET, and a third-party HTTP library would add a
dependency whose own retry and connection behaviour we would then have to disable.

Three habits this adapter keeps:

* **No secret in an exception or a log.** A failure is a status code and a short reason.
* **A timeout is an outcome, not a crash.** A refused initiation is a definite answer
  (``accepted=False``); a timeout raises ``TimeoutError`` so the use case records
  ``pending_unknown`` — the state the provider may still resolve — rather than ``failed``.
* **The body is never re-serialised before it is verified.** The signature covers the exact
  bytes that arrived.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from hub.payments.application.ports import (
    CollectionAck,
    CollectionRequest,
    ProviderTxnStatus,
    VerifiedEvent,
)
from hub.payments.domain.errors import MalformedWebhook, WebhookIdMismatch
from hub.payments.domain.signature import DEFAULT_TOLERANCE_SECONDS, verify
from hub.shared.money import CURRENCIES, Currency, Money

__all__ = [
    "EVENT_ID_HEADER",
    "HttpProviderGateway",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "envelope_to_event",
    "parse_envelope",
    "verify_delivery",
]

logger = logging.getLogger(__name__)

TIMESTAMP_HEADER = "X-Webhook-Timestamp"
SIGNATURE_HEADER = "X-Webhook-Signature"
EVENT_ID_HEADER = "X-Webhook-Id"

EVENT_TYPES = frozenset({"payment.confirmed", "payment.failed", "payment.reversed"})


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """A header by its canonical name, case-insensitively (HTTP header names are not cased)."""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


class HttpProviderGateway:
    """``ProviderGateway`` for one provider, configured from settings."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        secrets: Sequence[str],
        webhook_tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
        initiate_timeout_seconds: float = 10.0,
        pull_timeout_seconds: float = 1.5,
    ) -> None:
        if not secrets:
            # A provider with no secret cannot verify anything; refuse at composition time
            # rather than answer 401 to every honest delivery in production.
            raise ValueError(f"provider {provider!r} has no webhook secret configured")
        self._provider = provider
        self._base_url = base_url.rstrip("/")
        self._secrets = tuple(secret.encode("utf-8") for secret in secrets)
        self._tolerance = webhook_tolerance_seconds
        self._initiate_timeout = initiate_timeout_seconds
        self._pull_timeout = pull_timeout_seconds

    # --- webhook --------------------------------------------------------------------------
    def verify_webhook(
        self, headers: Mapping[str, str], body: bytes, *, now: datetime
    ) -> VerifiedEvent:
        """Verify the delivery and flatten its envelope; raise a domain error otherwise."""
        return verify_delivery(self._secrets, headers, body, now=now, tolerance=self._tolerance)

    # --- initiation -----------------------------------------------------------------------
    def initiate_collection(self, req: CollectionRequest) -> CollectionAck:
        """Ask the provider to collect; a 4xx is a refusal, a timeout is an unknown."""
        payload = json.dumps(
            {
                "reference": req.reference,
                "amount_minor": req.amount.amount_minor,
                "currency": req.amount.currency,
                "payer_msisdn": req.payer_msisdn,
                "expires_at": req.expires_at.isoformat(),
            }
        ).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - the URL comes from settings
            f"{self._base_url}/collections",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._initiate_timeout) as response:  # noqa: S310
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                # A definite refusal: the provider will not collect this money.
                return CollectionAck(accepted=False, failure_code=f"http_{exc.code}")
            logger.warning(
                "payments.initiation_failed",
                extra={"provider": self._provider, "status": exc.code},
            )
            return CollectionAck(accepted=False, unknown=True)
        except (urllib.error.URLError, TimeoutError, OSError):
            # No answer is not a refusal; the use case records `pending_unknown`.
            raise TimeoutError("the provider did not answer the initiation") from None
        txn_id = body.get("transaction_id") if isinstance(body, dict) else None
        return CollectionAck(accepted=True, provider_txn_id=str(txn_id) if txn_id else None)

    # --- pull -----------------------------------------------------------------------------
    def fetch_status(self, provider_txn_id: str) -> ProviderTxnStatus:
        """The provider's own view of a transaction (the pull of Task 04.8)."""
        request = urllib.request.Request(  # noqa: S310 - the URL comes from settings
            f"{self._base_url}/transactions/{provider_txn_id}",
            method="GET",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._pull_timeout) as response:  # noqa: S310
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return ProviderTxnStatus(status="not_found")
            raise TimeoutError("the provider did not answer the status pull") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TimeoutError("the provider did not answer the status pull") from exc
        if not isinstance(body, dict):
            raise TimeoutError("the provider answered a status pull with a non-object")
        status = str(body.get("status") or "not_found")
        if status not in {"successful", "pending", "failed", "reversed", "not_found"}:
            raise TimeoutError(f"the provider answered an unknown status {status!r}")
        amount = body.get("amount")
        money = (
            Money(
                amount_minor=int(amount["amount_minor"]),
                currency=_currency(amount.get("currency"), on_error="timeout"),
            )
            if isinstance(amount, dict)
            else None
        )
        payer = body.get("payer_msisdn")
        return ProviderTxnStatus(
            status=status,  # type: ignore[arg-type]
            amount=money,
            payer_msisdn=str(payer) if payer else None,
        )


def verify_delivery(
    secrets: Sequence[bytes],
    headers: Mapping[str, str],
    body: bytes,
    *,
    now: datetime,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
) -> VerifiedEvent:
    """The whole verification, shared by the HTTP gateway and the stub (FR-03).

    The header's id is the one the signature covers: it is the only id available before the
    body is parsed, and a signed body must agree with it afterwards. Both adapters call this,
    so a test that forges a delivery exercises the same checks a production one does.
    """
    timestamp = _header(headers, TIMESTAMP_HEADER)
    signature = _header(headers, SIGNATURE_HEADER)
    event_id = _header(headers, EVENT_ID_HEADER)

    verify(
        secrets,
        signature,
        timestamp,
        event_id,
        body,
        now=int(now.timestamp()),
        tolerance=tolerance,
    )

    envelope = parse_envelope(body)
    if envelope["event_id"] != event_id:
        raise WebhookIdMismatch(details={"header": event_id, "body": envelope["event_id"]})
    return envelope_to_event(envelope)


def parse_envelope(body: bytes) -> dict[str, Any]:
    """The signed body as ``WebhookEnvelope``, or 422 ``malformed_webhook``."""
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MalformedWebhook(details={"reason": "invalid_json"}) from exc
    if not isinstance(document, dict):
        raise MalformedWebhook(details={"reason": "not_an_object"})
    required = {"event_id", "type", "occurred_at", "data"}
    missing = sorted(required - set(document))
    if missing:
        raise MalformedWebhook(details={"reason": "missing_fields", "fields": missing})
    if not isinstance(document["event_id"], str) or not document["event_id"]:
        raise MalformedWebhook(details={"reason": "invalid_event_id"})
    if document["type"] not in EVENT_TYPES:
        raise MalformedWebhook(details={"reason": "unknown_event_type"})
    if not isinstance(document["data"], dict):
        raise MalformedWebhook(details={"reason": "invalid_data"})
    return document


def envelope_to_event(envelope: Mapping[str, Any]) -> VerifiedEvent:
    """``WebhookEnvelope`` to :class:`VerifiedEvent`, refusing an amount or id it cannot read.

    Shared by the verifying adapters and by the replay job: the stored payload *is* the
    envelope that was verified, so a replay decodes it with the same strictness instead of
    carrying a second parser that could disagree with this one.
    """
    data = envelope["data"]
    amount = data.get("amount")
    if not isinstance(amount, dict):
        raise MalformedWebhook(details={"reason": "missing_amount"})
    try:
        money = Money(
            amount_minor=int(amount["amount_minor"]),
            currency=_currency(amount.get("currency"), on_error="malformed"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedWebhook(details={"reason": "invalid_amount"}) from exc
    try:
        external = UUID(str(data["external_reference"]))
    except (KeyError, ValueError) as exc:
        raise MalformedWebhook(details={"reason": "invalid_external_reference"}) from exc
    reference = data.get("provider_reference")
    if not reference:
        raise MalformedWebhook(details={"reason": "missing_provider_reference"})
    occurred_at = _parse_instant(envelope["occurred_at"])
    payer = data.get("payer_msisdn")
    failure = data.get("failure_code")
    return VerifiedEvent(
        event_id=str(envelope["event_id"]),
        type=str(envelope["type"]),  # type: ignore[arg-type]
        occurred_at=occurred_at,
        provider_reference=str(reference),
        external_reference=external,
        amount=money,
        payer_msisdn=str(payer) if payer else None,
        failure_code=str(failure) if failure else None,
        payload=dict(envelope),
    )


def _currency(raw: Any, *, on_error: str) -> Currency:
    """A currency the hub supports, or the failure the caller's contract allows.

    The value comes from the provider, so it is text until proven otherwise: an unsupported
    code is not something to coerce, because paying in a currency the ledger cannot hold would
    post a number nobody can interpret. A webhook raises ``malformed_webhook`` (422); a status
    pull raises ``TimeoutError``, which the sweeper treats as no reliable answer.
    """
    text = str(raw)
    if text not in CURRENCIES:
        if on_error == "malformed":
            raise MalformedWebhook(details={"reason": "unsupported_currency", "currency": text})
        raise TimeoutError(f"the provider answered an unsupported currency {text!r}")
    return cast("Currency", text)


def _parse_instant(raw: Any) -> datetime:
    from datetime import UTC  # noqa: PLC0415 - one local import keeps the module header short

    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise MalformedWebhook(details={"reason": "invalid_occurred_at"}) from exc
    if parsed.tzinfo is None:
        # A provider that omits the zone is telling us UTC by convention; making it explicit
        # here beats letting a naive datetime reach a comparison against a tz-aware clock.
        return parsed.replace(tzinfo=UTC)
    return parsed
