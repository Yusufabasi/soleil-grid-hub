"""The provider webhook signature, as one pure function (FR-03, docs/03 6.1).

The signed string is ``{timestamp}.{event_id}.{raw body}`` and the header is
``X-Webhook-Signature: v1=<hex HMAC-SHA256>``. Two properties matter more than the algorithm:

* **The raw bytes are signed, not a re-serialisation.** Parsing the JSON first and signing the
  normalised document would accept a body the sender never signed (``{"a":1}`` and ``{"a": 1}``
  hash differently) and reject one they did.
* **The signature is checked before the timestamp window.** A forgery must answer 401
  ``invalid_signature``, never 400 ``stale_timestamp``: the second tells an attacker their
  clock is off, and tells a provider with a broken key nothing about the real problem.

Secrets are compared in constant time and every secret of the provider is tried, because a
rotation keeps the previous secret valid for 24 hours. No secret, signature or body ever
reaches a log from here.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from typing import Final

from hub.payments.domain.errors import InvalidSignature, StaleTimestamp

__all__ = [
    "DEFAULT_TOLERANCE_SECONDS",
    "SIGNATURE_PREFIX",
    "compute_signature",
    "signed_string",
    "verify",
]

SIGNATURE_PREFIX: Final = "v1="
DEFAULT_TOLERANCE_SECONDS: Final = 300


def signed_string(timestamp: str, event_id: str, body: bytes) -> bytes:
    """The exact bytes the HMAC covers: ``{timestamp}.{event_id}.{body}``."""
    return timestamp.encode("utf-8") + b"." + event_id.encode("utf-8") + b"." + body


def compute_signature(secret: bytes, timestamp: str, event_id: str, body: bytes) -> str:
    """``v1=<hex>`` for one secret: what an honest sender puts in the header."""
    digest = hmac.new(secret, signed_string(timestamp, event_id, body), hashlib.sha256)
    return SIGNATURE_PREFIX + digest.hexdigest()


def verify(
    secrets: Sequence[bytes],
    signature_header: str | None,
    timestamp: str | None,
    event_id: str | None,
    body: bytes,
    *,
    now: int,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
) -> None:
    """Raise :class:`InvalidSignature` or :class:`StaleTimestamp`, or return None.

    ``now`` is Unix seconds taken from the injected clock, so the window is provable with a
    frozen clock rather than with ``sleep``.
    """
    if not signature_header or not timestamp or not event_id:
        # A delivery with no signature is not a stale one: it is not signed at all.
        raise InvalidSignature(details={"reason": "missing_signature_headers"})

    provided = signature_header.strip()
    if not provided.startswith(SIGNATURE_PREFIX):
        raise InvalidSignature(details={"reason": "unsupported_signature_scheme"})

    if not any(
        hmac.compare_digest(provided, compute_signature(secret, timestamp, event_id, body))
        for secret in secrets
    ):
        # No detail about which part disagreed: that is a hint for a forger.
        raise InvalidSignature()

    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise InvalidSignature(details={"reason": "timestamp_not_integer"}) from exc

    if abs(now - sent_at) > tolerance:
        raise StaleTimestamp(details={"tolerance_seconds": tolerance})
