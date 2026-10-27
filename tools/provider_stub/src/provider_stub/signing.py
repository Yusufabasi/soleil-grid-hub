"""The signature a provider puts on a delivery, exactly as the hub verifies it (D-09).

The signed string is ``timestamp.event_id.body`` — three parts, two dots — and the header is
``v1=<hex>`` over HMAC-SHA256 of those bytes. The body is signed as *bytes*, not as a document:
the hub verifies the bytes it received, so anything that re-serialises between signing and
sending would produce a signature that only matches a body nobody sent. That is why
:func:`build_headers` takes bytes and why the delivery module signs immediately before the POST.

Header names are the OpenAPI ones (plan note 4: docs/09 sketches `X-Signature`; the contract
wins).
"""

from __future__ import annotations

import hashlib
import hmac

__all__ = [
    "EVENT_ID_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "build_headers",
    "signed_string",
    "verify",
]

EVENT_ID_HEADER = "X-Webhook-Id"
TIMESTAMP_HEADER = "X-Webhook-Timestamp"
SIGNATURE_HEADER = "X-Webhook-Signature"

# Every signature starts with this: a version prefix leaves room for a rotation without
# ambiguity about which algorithm produced a value.
_PREFIX = "v1="


def signed_string(timestamp: int | str, event_id: str, body: bytes) -> bytes:
    """``<timestamp>.<event_id>.<body>`` as the exact bytes that are hashed."""
    return str(timestamp).encode("ascii") + b"." + event_id.encode("utf-8") + b"." + body


def sign(secret: bytes, timestamp: int, event_id: str, body: bytes) -> str:
    """``v1=`` plus the hex HMAC-SHA256 of the signed string."""
    digest = hmac.new(secret, signed_string(timestamp, event_id, body), hashlib.sha256).hexdigest()
    return _PREFIX + digest


def verify(secret: bytes, signature: str, timestamp: int | str, event_id: str, body: bytes) -> bool:
    """Whether ``signature`` is ``sign`` of these bytes — used by the double's own tests."""
    expected = sign(secret, int(timestamp), event_id, body)
    return hmac.compare_digest(expected, signature)


def build_headers(*, secret: bytes, timestamp: int, event_id: str, body: bytes) -> dict[str, str]:
    """The three headers of a delivery of ``body``, for the exact bytes passed in."""
    return {
        EVENT_ID_HEADER: event_id,
        TIMESTAMP_HEADER: str(int(timestamp)),
        SIGNATURE_HEADER: sign(secret, int(timestamp), event_id, body),
    }
