"""Documents and inputs of the meter API (``api/openapi.yaml`` Meter, MeterCreate, MeterUpdate).

The document is built field by field rather than by serialising the row: the contract's names
(``serial_number``, ``firmware_version``) and the table's (``serial_no``, ``firmware``) differ,
and two fields have no column at all — ``hardware_model`` is a constant ``"unknown"`` (Plan
notes 3) and ``credit_remaining_wh`` is the last reported credit, which no read has until the
token ledger lands (slice 06). Both are ``null``/``"unknown"`` rather than invented.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rest_framework import serializers

from hub.meters.contracts import MeterRow

__all__ = [
    "MeterCreateInput",
    "MeterUpdateInput",
    "decode_meter_cursor",
    "encode_meter_cursor",
    "meter_document",
]

UPDATABLE_STATUSES = ("active", "offline", "disconnected", "decommissioned")
# No column, and inventing one would be worse than saying so (Plan notes 3).
HARDWARE_MODEL_UNKNOWN = "unknown"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return str(value.isoformat().replace("+00:00", "Z"))


def meter_document(row: MeterRow) -> dict[str, Any]:
    """``Meter`` of the contract."""
    return {
        "id": str(row.id),
        "serial_number": row.serial_no,
        "customer_id": str(row.customer_id) if row.customer_id is not None else None,
        "site_id": str(row.site_id),
        "hardware_model": HARDWARE_MODEL_UNKNOWN,
        "status": row.status,
        "firmware_version": row.firmware,
        "credit_remaining_wh": None,
        "last_seen_at": None,
        "last_reading_at": None,
        "installed_at": _iso(row.installed_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "version": row.version,
        "gateway_id": str(row.gateway_id) if row.gateway_id is not None else None,
    }


def encode_meter_cursor(meter_id: Any) -> str | None:
    return str(meter_id) if meter_id is not None else None


def decode_meter_cursor(raw: str | None) -> Any:
    """The cursor is the last row's id; a malformed one is the client's mistake (400)."""
    if not raw:
        return None
    from uuid import UUID  # noqa: PLC0415

    try:
        return UUID(raw)
    except ValueError:
        from hub.shared.errors import InvalidCursor  # noqa: PLC0415

        raise InvalidCursor() from None


class MeterCreateInput(serializers.Serializer[Any]):
    """``MeterCreate``: a serial, a site and the model the contract asks for."""

    serial_number = serializers.CharField(max_length=100)
    site_id = serializers.UUIDField()
    customer_id = serializers.UUIDField(required=False, allow_null=True)
    hardware_model = serializers.CharField(max_length=100)
    firmware_version = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class MeterUpdateInput(serializers.Serializer[Any]):
    """``MeterUpdate``: an absent field is untouched, ``customer_id: null`` is an unassign.

    ``allow_null`` on ``customer_id`` is the whole point: the difference between "leave the
    owner alone" and "nobody owns this meter now" cannot be expressed by a missing key.
    """

    customer_id = serializers.UUIDField(required=False, allow_null=True)
    site_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=UPDATABLE_STATUSES, required=False)
    firmware_version = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError(
                {"errors": [{"field": "/", "code": "empty", "message": "Nothing to change."}]}
            )
        return attrs
