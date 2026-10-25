"""Clock adapters. The only place in the codebase allowed to read wall-clock time.

``current`` and ``use_clock`` are the kernel's process-wide seam. A use case gets its clock
injected, but the permission class judges a step-up before any use case runs, and a test that
proves the five-minute boundary cannot do it against the wall clock. ``use_clock`` is a test
seam only: production never calls it, and ``SystemClock`` stays the default.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hub.shared.ports import Clock

__all__ = ["FrozenClock", "SystemClock", "current", "use_clock"]


class SystemClock:
    """The real clock: UTC, tz-aware, never naive (executor guide section 4 rule 8)."""

    def now(self) -> datetime:
        return datetime.now(UTC)  # noqa: TID251 - this adapter owns wall-clock time


_process_clock: Clock | None = None


def current() -> datetime:
    """The kernel's now: the injected clock in a test, the system clock otherwise."""
    return (_process_clock or SystemClock()).now()


def use_clock(clock: Clock | None) -> None:
    """Install (or, with ``None``, remove) the process clock. A test seam."""
    global _process_clock  # noqa: PLW0603
    _process_clock = clock


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
