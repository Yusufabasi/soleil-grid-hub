"""The domain event every module publishes through the outbox (docs/03 2.1, docs/04 5.4).

One frozen dataclass, no behaviour beyond the row it becomes, so a module's own ``events``
module can wrap it in a typed constructor (``PaymentConfirmed.from_payload``) without the
kernel learning business vocabulary. The envelope is written into the ``payload`` column
together with the event's own fields: the relay publishes that document as-is, and a
consumer can route on ``name`` without a second lookup.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

__all__ = ["ENVELOPE_KEYS", "DomainEvent"]

# Keys the envelope owns; an event payload may not shadow one of them.
ENVELOPE_KEYS = (
    "event_id",
    "name",
    "version",
    "occurred_at",
    "correlation_id",
    "causation_id",
    "aggregate_type",
    "aggregate_id",
)

# One outbox row as the writer inserts it: (id, aggregate_type, aggregate_id, event_type,
# payload). The payload column is jsonb; psycopg adapts the mapping through Jsonb.
OutboxRow = tuple[UUID, str, UUID, str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """A fact that already happened, ready to be staged in the caller's transaction."""

    event_id: UUID
    name: str
    version: int
    occurred_at: datetime
    correlation_id: UUID
    causation_id: UUID | None
    aggregate_type: str
    aggregate_id: UUID
    payload: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """The jsonb body: envelope plus the event's fields (docs/04 5.4)."""
        shadowed = set(self.payload) & set(ENVELOPE_KEYS)
        if shadowed:
            raise ValueError(f"event payload may not shadow envelope keys: {sorted(shadowed)}")
        envelope: dict[str, Any] = {
            "event_id": str(self.event_id),
            "name": self.name,
            "version": self.version,
            "occurred_at": _iso(self.occurred_at),
            "correlation_id": str(self.correlation_id),
            "causation_id": str(self.causation_id) if self.causation_id is not None else None,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": str(self.aggregate_id),
        }
        return {**envelope, **self.payload}

    def to_row(self) -> OutboxRow:
        """``(id, aggregate_type, aggregate_id, event_type, payload)`` for ``hub.outbox``."""
        return (
            self.event_id,
            self.aggregate_type,
            self.aggregate_id,
            self.name,
            self.to_payload(),
        )


def _iso(moment: datetime) -> str:
    """RFC 3339 in UTC; a naive timestamp is a bug, not a formatting choice."""
    if moment.tzinfo is None:
        raise ValueError("DomainEvent.occurred_at must be timezone-aware (UTC)")
    return moment.astimezone(UTC).isoformat()
