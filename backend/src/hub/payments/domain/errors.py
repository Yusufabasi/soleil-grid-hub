"""Payments failures and the codes a client branches on (docs/05 section 5).

The signature trio is separate from the rest on purpose: a rejected delivery has to be
distinguishable from a payment that was refused, because the provider retries the first and
nothing retries the second. Each class carries a ``message_key`` so the French catalogue in
``payments/locale`` can answer in the caller's language.
"""

from __future__ import annotations

from hub.shared.errors import (
    Conflict,
    ExternalUnavailable,
    InvalidRequest,
    NotFound,
    PermissionDenied,
    RateLimited,
    Unauthenticated,
    ValidationFailed,
)

__all__ = [
    "AmountOutOfRange",
    "CreditPathUnavailable",
    "InsufficientScope",
    "InvalidSignature",
    "InvalidTopUpState",
    "MalformedWebhook",
    "MeterNotActive",
    "PaymentNotFound",
    "StaleTimestamp",
    "TopUpLimit",
    "WebhookIdMismatch",
]


class AmountOutOfRange(ValidationFailed):
    """An amount outside the plan's floor and ceiling, or not in the plan's currency: 422."""

    code = "amount_out_of_range"
    message_key = "amount_out_of_range"


class MeterNotActive(ValidationFailed):
    """A top-up or a token for a meter that is not ``active``: 422 ``meter_not_active``."""

    code = "meter_not_active"
    message_key = "meter_not_active"


class InsufficientScope(PermissionDenied):
    """An API key without the scope the operation needs: 403 ``insufficient_scope``."""

    code = "insufficient_scope"
    message_key = "insufficient_scope"


class TopUpLimit(RateLimited):
    """Too many top-ups for one meter or one payer in the window: 429 ``topup_limit``."""

    code = "topup_limit"
    message_key = "topup_limit"


class InvalidTopUpState(Conflict):
    """A transition the state machine does not allow, such as cancelling a confirmed top-up."""

    code = "invalid_state_transition"
    message_key = "invalid_top_up_state"


class PaymentNotFound(NotFound):
    """A top-up that does not exist, or is outside the caller's scope: 404."""

    message_key = "not_found"


class InvalidSignature(Unauthenticated):
    """A delivery whose signature does not verify: 401 ``invalid_signature`` (FR-03).

    Checked before the timestamp window, so a forgery is never reported as a stale timestamp —
    the two answers tell the sender different things and only one of them is about their clock.
    """

    code = "invalid_signature"
    message_key = "invalid_signature"


class StaleTimestamp(InvalidRequest):
    """A correctly signed delivery outside the 300-second window: 400 ``stale_timestamp``.

    ``InvalidRequest`` rather than ``ValidationFailed``: the body parsed and the signature
    verified, so nothing about the payload is wrong — the delivery arrived too late, which is
    a request-level refusal and carries no ``errors[]`` (docs/05 section 5).
    """

    code = "stale_timestamp"
    message_key = "stale_timestamp"


class MalformedWebhook(ValidationFailed):
    """A correctly signed body that is not the event envelope: 422 ``malformed_webhook``."""

    code = "malformed_webhook"
    message_key = "malformed_webhook"


class WebhookIdMismatch(InvalidRequest):
    """``X-Webhook-Id`` disagrees with the signed body's ``event_id``: 400.

    A transport-level disagreement rather than a semantic one: the signature covered some
    event, and the header names another, so nothing can be trusted about which one arrived.
    """

    code = "webhook_id_mismatch"
    message_key = "webhook_id_mismatch"


class CreditPathUnavailable(ExternalUnavailable):
    """The confirmation was verified and recorded, but the credit path is not in this build.

    Slice 04 lands in tasks and the money path (Task 04.5) is not part of the shell: a
    confirmed delivery therefore answers 503 with ``Retry-After`` and commits nothing, which
    is the honest answer for a provider that should deliver again rather than one that is
    told a payment was taken when it was not.
    """

    code = "service_unavailable"
    message_key = "service_unavailable"
