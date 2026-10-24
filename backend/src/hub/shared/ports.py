"""Cross-cutting ports the domain and application layers depend on (docs/03 section 2.1).

Business code never calls ``datetime.now()``, ``uuid.uuid4`` or ``random`` directly: it asks
the container for these ports, so tests can inject a frozen clock and deterministic ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

__all__ = ["Clock", "IdGenerator"]


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, tz-aware and always UTC."""
        ...


@runtime_checkable
class IdGenerator(Protocol):
    def new_id(self) -> UUID:
        """A new UUIDv7: time ordered, so the default sort key needs no extra column."""
        ...
