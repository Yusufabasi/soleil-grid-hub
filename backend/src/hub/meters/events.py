"""Domain events produced by `meters` (docs/03 section 4).

One builder, matching the envelope ``customers.events`` writes: the correlation is the
request's when a request caused the event, and the event id otherwise. ``meter.assigned`` is
the only event this slice produces; the token and heartbeat events belong to slices 05 and 06,
which own their facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from hub.shared.context import correlation_id_var
from hub.shared.events import DomainEvent

__all__ = ["meter_assigned"]

AGGREGATE = "meter"


def _correlation(fallback: UUID) -> UUID:
    raw = correlation_id_var.get()
    if not raw:
        return fallback
    try:
        return UUID(raw)
    except ValueError:
        return fallback


def meter_assigned(
    *,
    ids: Any,
    occurred_at: datetime,
    meter_id: UUID,
    payload: Mapping[str, Any],
) -> DomainEvent:
    """``meter.assigned``: the meter now serves this customer, or nobody when it is null.

    ``docs/03`` lists the event under `customers`' produced events, but the column that changes
    is ``hub.meters.customer_id`` and this module is its only writer, so the producer is the
    module that can tell the truth about it.
    """
    event_id = ids.new_id()
    return DomainEvent(
        event_id=event_id,
        name="meter.assigned",
        version=1,
        occurred_at=occurred_at,
        correlation_id=_correlation(event_id),
        causation_id=None,
        aggregate_type=AGGREGATE,
        aggregate_id=meter_id,
        payload=payload,
    )
