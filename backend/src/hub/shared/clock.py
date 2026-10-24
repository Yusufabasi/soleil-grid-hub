"""Clock adapters. The only place in the codebase allowed to read wall-clock time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

__all__ = ["FrozenClock", "SystemClock"]


class SystemClock:
    """The real clock: UTC, tz-aware, never naive (executor guide section 4 rule 8)."""

    def now(self) -> datetime:
        return datetime.now(UTC)  # noqa: TID251 - this adapter owns wall-clock time


class FrozenClock:
    """A clock that only moves when a test says so."""

    __slots__ = ("_now",)

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FrozenClock needs a tz-aware instant, got a naive one")
        self._now = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> FrozenClock:
        """Move time forward and return the clock, so calls chain."""
        self._now += delta
        return self
