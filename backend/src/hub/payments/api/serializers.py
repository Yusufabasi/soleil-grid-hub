"""Input validation and the opaque cursor for the top-up routes (docs/03 7.7).

The document a route answers with is built by ``TopUpView.document`` in the contracts, not
here: a field the API adds is a field the contract must know, and two builders would drift.
What lives here is the other direction — the body a caller sends — plus the page cursor, which
is deliberately opaque so a client cannot page by guessing a uuid.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from rest_framework import serializers

from hub.shared.errors import InvalidCursor

__all__ = [
    "PROVIDERS",
    "TopUpCreateInput",
    "decode_cursor",
    "encode_cursor",
    "page_meta",
    "webhook_event_document",
]

# The providers the contract exposes. `synthetic` is deliberately absent from `TopUpCreate`:
# it is the local stub, and a customer must not be able to choose it in production.
PROVIDERS = ("mpesa_cd", "airtel_cd", "orange_cd")

# E.164: a plus, a non-zero country digit, and 7 to 14 more digits (the contract's pattern).
PAYER_MSISDN = r"^\+[1-9][0-9]{7,14}$"


class MoneyInput(serializers.Serializer[Any]):
    """``Money`` of the contract: an integer count of minor units and a currency."""

    amount_minor = serializers.IntegerField(min_value=1)
    currency = serializers.ChoiceField(choices=("USD", "CDF"))


class TopUpCreateInput(serializers.Serializer[Any]):
    """``TopUpCreate``: the amount is the only money the caller chooses (D-08)."""

    meter_id = serializers.UUIDField()
    amount = MoneyInput()
    provider = serializers.ChoiceField(choices=PROVIDERS)
    payer_msisdn = serializers.RegexField(PAYER_MSISDN, max_length=16)
    # Honoured only for a machine principal (D-14); ignored for a customer, whose id is the
    # token's. Optional in the schema because the portal never sends it.
    customer_id = serializers.UUIDField(required=False, allow_null=True)


def encode_cursor(value: UUID | None) -> str | None:
    """The opaque cursor for the next page, or ``None`` on the last one."""
    if value is None:
        return None
    raw = json.dumps({"i": str(value)}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> UUID | None:
    """The id a cursor carries; anything else is ``invalid_cursor`` (400)."""
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return UUID(str(payload["i"]))
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise InvalidCursor() from exc


def page_meta(limit: int, *, has_next: bool, last: UUID | None) -> dict[str, Any]:
    """``PageMeta``: an opaque cursor only when there is another page."""
    return {
        "has_next": has_next,
        "next_cursor": encode_cursor(last) if has_next and last is not None else None,
        "limit": limit,
    }


def webhook_event_document(row: Mapping[str, Any]) -> dict[str, Any]:
    """``WebhookEventLog`` of one stored delivery, from the columns ``hub_api`` may read.

    ``signature_valid`` is always true: nothing reaches ``provider_events`` without a verified
    signature, so a rejection is a ``webhook_quarantine`` row and not a log entry (FR-03).
    ``error_code`` is the reason stored on the event when it was ignored or failed — the same
    field the alerting dashboards group by.
    """
    return {
        "id": str(row["id"]),
        "provider": row["provider"],
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "signature_valid": True,
        "status": row["status"],
        "top_up_id": str(row["top_up_id"]) if row["top_up_id"] else None,
        "error_code": row["last_error"],
        "received_at": _iso(row["received_at"]),
        "processed_at": _iso(row["processed_at"]) if row["processed_at"] else None,
    }


def _iso(value: Any) -> str:
    """The contract's instant: RFC 3339 in UTC with a ``Z``.

    ``str(...)`` rather than the datetime's own return because the source is a raw SQL cursor:
    mypy cannot see the column's type, and an untyped passthrough is how a non-instant reaches
    a client that is parsing it.
    """
    return str(value.isoformat()).replace("+00:00", "Z")
