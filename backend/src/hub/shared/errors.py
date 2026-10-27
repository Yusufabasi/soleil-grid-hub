"""The error taxonomy of the kernel (docs/03 section 7.1).

Every failure that reaches a client derives from :class:`DomainError` and carries a stable
code from the ``docs/05`` catalogue, a localisation key and optional structured details.
The single RFC 9457 converter lives in ``hub.shared.api.problem`` and maps these classes
to the status of the 7.1 table.

``InvariantViolation`` is the one class that acts on construction: an invariant breach
pages on-call and is never swallowed, so it logs at ERROR and counts
``invariant_violations_total`` the moment it is raised (docs/03 7.1).
"""

from __future__ import annotations

import logging
from typing import Any

from hub.shared.metrics import counter

__all__ = [
    "Conflict",
    "DomainError",
    "Duplicate",
    "ExternalUnavailable",
    "InvalidCursor",
    "InvalidRequest",
    "InvariantViolation",
    "PayloadTooLarge",
    "NotFound",
    "PermissionDenied",
    "RateLimited",
    "Unexpected",
    "Unauthenticated",
    "ValidationFailed",
]

logger = logging.getLogger(__name__)

# Prometheus appends the ``_total`` suffix itself: the series is invariant_violations_total.
INVARIANT_VIOLATIONS = counter(
    "invariant_violations", [], "Invariant violations raised to the API (page on-call)"
)


class DomainError(Exception):
    """A business failure with a stable code (docs/03 7.1, docs/05 section 5).

    ``code`` is what clients branch on; ``message_key`` selects the localised ``detail``;
    ``details`` carries structured extras (field errors, retry hints) and never secrets.
    """

    # Plain class attributes (not ClassVar): subclasses narrow them and __init__ may
    # override per instance, which mypy only allows on instance-declared attributes.
    code = "domain_error"
    message_key = "domain_error"
    http_status = 500

    def __init__(
        self,
        code: str | None = None,
        *,
        message_key: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code is not None:
            self.code = code
        if message_key is not None:
            self.message_key = message_key
        self.details: dict[str, Any] = details or {}
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, http_status={self.http_status})"


class ValidationFailed(DomainError):
    """Input is semantically invalid: 422 with an ``errors[]`` list (docs/03 7.1)."""

    code = "validation_error"
    message_key = "validation_error"
    http_status = 422


class Unauthenticated(DomainError):
    """No (or an expired) identity: 401, client refreshes or steps up (docs/03 7.1)."""

    code = "unauthenticated"
    message_key = "unauthenticated"
    http_status = 401


class PermissionDenied(DomainError):
    """Identity lacks the role, scope or attribute: 403 (docs/03 7.1)."""

    code = "forbidden"
    message_key = "forbidden"
    http_status = 403


class InvalidRequest(DomainError):
    """A malformed request that is not a schema violation: 400 (docs/05 section 5).

    Query parameters and their combination live here: ``ValidationFailed`` (422) is for a
    body that parsed and failed the schema, this is for a request the contract cannot serve.
    """

    code = "invalid_request"
    message_key = "invalid_request"
    http_status = 400


class InvalidCursor(DomainError):
    """A cursor that does not decode or has expired: 400, the client restarts (docs/03 7.7.5)."""

    code = "invalid_cursor"
    message_key = "invalid_cursor"
    http_status = 400


class PayloadTooLarge(DomainError):
    """A body over the route's cap, refused before it is read: 413 ``payload_too_large``.

    The cap is per route (a webhook's 64 KB, a telemetry batch's 256 KB), so the class carries
    no number; the route that raises it names the limit in ``details``.
    """

    code = "payload_too_large"
    message_key = "payload_too_large"
    http_status = 413


class NotFound(DomainError):
    """Missing or not visible to this principal: 404 (docs/03 7.1, docs/05)."""

    code = "not_found"
    message_key = "not_found"
    http_status = 404


class Conflict(DomainError):
    """State conflict: 409 (idempotency reuse, four eyes, version conflict)."""

    code = "conflict"
    message_key = "conflict"
    http_status = 409


class Duplicate(DomainError):
    """Natural-key clash: 200 with ``status: duplicate`` on webhooks, 409 elsewhere."""

    code = "duplicate_resource"
    message_key = "duplicate_resource"
    http_status = 409


class RateLimited(DomainError):
    """Rate or budget limit hit: 429 with ``Retry-After: retry_after_seconds``."""

    code = "rate_limited"
    message_key = "rate_limited"
    http_status = 429

    def __init__(
        self,
        code: str | None = None,
        *,
        message_key: str | None = None,
        details: dict[str, Any] | None = None,
        retry_after_seconds: int = 30,
    ) -> None:
        super().__init__(code, message_key=message_key, details=details)
        self.retry_after_seconds = retry_after_seconds


class ExternalUnavailable(DomainError):
    """Upstream provider or dependency failed: 502 or 503, retryable (docs/03 7.1)."""

    code = "provider_unavailable"
    message_key = "provider_unavailable"
    http_status = 503


class Unexpected(DomainError):
    """Unmapped failure: 500 with no detail, only the request id (docs/03 7.1)."""

    code = "internal_error"
    message_key = "internal_error"
    http_status = 500


class InvariantViolation(DomainError):
    """A ledger or domain invariant broke: 500, page on-call, never swallowed.

    Construction already logs at ERROR and increments ``invariant_violations_total``,
    because the class exists to be noticed even if a caller mishandles it.
    """

    code = "invariant_violation"
    message_key = "invariant_violation"
    http_status = 500

    def __init__(
        self,
        code: str | None = None,
        *,
        message_key: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message_key=message_key, details=details)
        INVARIANT_VIOLATIONS.inc()
        logger.error(
            "invariant_violation",
            extra={"error_code": self.code, "details": self.details},
        )
