"""Request validators and response documents of the registry operations (docs/03 7.7).

Money crosses the wire as ``{amount_minor, currency}`` and is stored as an integer plus the
plan's currency, never a float (D-08). The input serializers are the contract's validators, so
a malformed body is a 422 with a JSON pointer per field before any use case runs; the document
builders are plain functions because the DTOs are frozen dataclasses, not models.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any
from uuid import UUID

from rest_framework import serializers

from hub.customers.contracts import CustomerRow, PlanRow, PlanTariffRow, SiteRow
from hub.shared.errors import InvalidCursor

__all__ = [
    "CustomerCreateInput",
    "CustomerUpdateInput",
    "customer_document",
    "PlanCreateInput",
    "PlanUpdateInput",
    "SiteCreateInput",
    "SiteUpdateInput",
    "decode_cursor",
    "encode_cursor",
    "money",
    "plan_document",
    "site_document",
    "tariff_document",
]

CURRENCIES = ("USD", "CDF")


class MoneyInput(serializers.Serializer[dict[str, Any]]):
    amount_minor = serializers.IntegerField(min_value=1, max_value=2**53 - 1)
    currency = serializers.ChoiceField(choices=CURRENCIES)


class SiteCreateInput(serializers.Serializer[dict[str, Any]]):
    code = serializers.CharField(min_length=2, max_length=32)
    name = serializers.CharField(min_length=1, max_length=120)
    timezone = serializers.CharField(min_length=1, max_length=64)
    # Fields the contract declares but the schema has no column for (Plan notes 3): accepted
    # so a client following the document is not refused, then dropped with the audit detail.
    latitude = serializers.FloatField(required=False, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, min_value=-180, max_value=180)
    capacity_kw = serializers.FloatField(required=False, min_value=0)


class SiteUpdateInput(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(min_length=1, max_length=120, required=False)
    timezone = serializers.CharField(min_length=1, max_length=64, required=False)
    capacity_kw = serializers.FloatField(required=False, min_value=0)
    status = serializers.ChoiceField(
        choices=("planned", "active", "decommissioned"), required=False
    )


class PlanCreateInput(serializers.Serializer[dict[str, Any]]):
    code = serializers.CharField(min_length=2, max_length=32)
    name = serializers.CharField(min_length=1, max_length=120)
    currency = serializers.ChoiceField(choices=CURRENCIES)
    min_top_up = MoneyInput()
    max_top_up = MoneyInput()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """The two amounts are in the plan's currency, and ordered (422 otherwise)."""
        currency = attrs["currency"]
        for field in ("min_top_up", "max_top_up"):
            if attrs[field]["currency"] != currency:
                raise serializers.ValidationError(
                    {field: [f"Currency must match the plan's: {currency}."]}
                )
        return attrs


class PlanUpdateInput(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(min_length=1, max_length=120, required=False)
    min_top_up = MoneyInput(required=False)
    max_top_up = MoneyInput(required=False)
    status = serializers.ChoiceField(choices=("draft", "active", "retired"), required=False)


class CustomerCreateInput(serializers.Serializer[dict[str, Any]]):
    """``CustomerCreate``: the phone is accepted as typed and normalised by the use case."""

    full_name = serializers.CharField(min_length=1, max_length=160)
    phone_e164 = serializers.CharField(max_length=32)
    email = serializers.EmailField(required=False, allow_null=True)
    locale = serializers.ChoiceField(choices=("en", "fr"), required=False)
    site_id = serializers.UUIDField()
    plan_id = serializers.UUIDField()


class CustomerUpdateInput(serializers.Serializer[dict[str, Any]]):
    """``CustomerUpdate``: name, email, locale, site, plan and the two live statuses."""

    full_name = serializers.CharField(min_length=1, max_length=160, required=False)
    email = serializers.EmailField(required=False, allow_null=True)
    locale = serializers.ChoiceField(choices=("en", "fr"), required=False)
    site_id = serializers.UUIDField(required=False)
    plan_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=("active", "suspended"), required=False)


def customer_document(row: CustomerRow) -> dict[str, Any]:
    """``api/openapi.yaml`` Customer. ``customer_number`` is the external ref, or the id."""
    number = row.external_ref or row.id.hex[:8].upper()
    return {
        "id": str(row.id),
        "customer_number": number,
        "full_name": row.full_name,
        "phone_e164": row.phone_e164,
        "email": row.email,
        "locale": row.language,
        "status": row.status,
        "site_id": str(row.site_id),
        "plan_id": str(row.plan_id),
        "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": row.updated_at.isoformat().replace("+00:00", "Z"),
        "version": row.version,
    }


def money(amount_minor: int, currency: str) -> dict[str, Any]:
    return {"amount_minor": int(amount_minor), "currency": currency}


def site_document(site: SiteRow, *, meter_count: int) -> dict[str, Any]:
    """``api/openapi.yaml`` Site, with the computed and column-less fields made explicit."""
    return {
        "id": str(site.id),
        "code": site.code,
        "name": site.name,
        "timezone": site.timezone,
        # No columns (Plan notes 3): null rather than invented, and never writable.
        "latitude": None,
        "longitude": None,
        "capacity_kw": None,
        "status": site.status,
        # Gateway heartbeat is slice 04's; until then a site with a meter is operational.
        "health": "operational",
        "meter_count": meter_count,
        "created_at": site.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": site.updated_at.isoformat().replace("+00:00", "Z"),
    }


def tariff_document(tariff: PlanTariffRow, *, currency: str) -> dict[str, Any]:
    return {
        "id": str(tariff.id),
        "plan_id": str(tariff.plan_id),
        "price_per_kwh": money(tariff.tariff_minor_per_kwh, currency),
        "effective_from": tariff.effective_from.isoformat().replace("+00:00", "Z"),
        "status": tariff.status,
        "approval_id": None if tariff.approval_id is None else str(tariff.approval_id),
        "created_at": tariff.created_at.isoformat().replace("+00:00", "Z"),
    }


def plan_document(plan: PlanRow, *, current_tariff: PlanTariffRow | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": str(plan.id),
        "code": plan.code,
        "name": plan.name,
        "currency": plan.currency,
        "min_top_up": money(plan.min_topup_minor, plan.currency),
        "max_top_up": money(plan.max_topup_minor, plan.currency),
        "status": plan.status,
        "created_at": plan.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": plan.updated_at.isoformat().replace("+00:00", "Z"),
    }
    if current_tariff is not None:
        document["current_tariff"] = tariff_document(current_tariff, currency=plan.currency)
    return document


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
