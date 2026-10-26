"""Domain events produced by `customers` (docs/03 section 4).

Two builders, one envelope. ``correlation_id`` is the request's, when a request is what caused
the event; a management command (the CSV import) has none, and then the event id doubles as
the correlation, which keeps the envelope complete rather than nullable.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from hub.shared.context import correlation_id_var
from hub.shared.events import DomainEvent

__all__ = ["customer_registered", "customer_status_changed"]

AGGREGATE = "customer"


def _correlation(fallback: UUID) -> UUID:
    raw = correlation_id_var.get()
    if not raw:
        return fallback
    try:
        return UUID(raw)
    except ValueError:
        return fallback


def _event(
    *,
    name: str,
    ids: Any,
    occurred_at: datetime,
    customer_id: UUID,
    payload: Mapping[str, Any],
) -> DomainEvent:
    event_id = ids.new_id()
    return DomainEvent(
        event_id=event_id,
        name=name,
        version=1,
        occurred_at=occurred_at,
        correlation_id=_correlation(event_id),
        causation_id=None,
        aggregate_type=AGGREGATE,
        aggregate_id=customer_id,
        payload=payload,
    )


def customer_registered(
    *,
    ids: Any,
    occurred_at: datetime,
    customer_id: UUID,
    payload: Mapping[str, Any],
) -> DomainEvent:
    """``customer.registered``: the row exists; slice 03 opens its wallet from this."""
    return _event(
        name="customer.registered",
        ids=ids,
        occurred_at=occurred_at,
        customer_id=customer_id,
        payload=payload,
    )


def customer_status_changed(
    *,
    ids: Any,
    occurred_at: datetime,
    customer_id: UUID,
    payload: Mapping[str, Any],
) -> DomainEvent:
    """``customer.status_changed``: suspended, reactivated or closed, with the new status."""
    return _event(
        name="customer.status_changed",
        ids=ids,
        occurred_at=occurred_at,
        customer_id=customer_id,
        payload=payload,
    )
