"""UUIDv7 id generation.

``Uuid7Generator`` wraps the one pinned library (``uuid-utils``, locked by version and hash
in ``backend/uv.lock``) and takes its time from the :class:`~hub.shared.ports.Clock`, so a
frozen clock produces reproducible ids. Ordering within a millisecond is guaranteed by the
library's monotonic counter. ``SequentialIdGenerator`` is the deterministic test double.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import uuid_utils

from hub.shared.ports import Clock

__all__ = ["SequentialIdGenerator", "Uuid7Generator", "uuid7"]

_NANOSECONDS = 1_000_000_000
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# A fixed v7-shaped prefix for deterministic ids: version nibble 7, variant 10.
_SEQUENCE_PREFIX = bytes([0x01, 0x90, 0x00, 0x00, 0x00, 0x00])


def _nanoseconds(instant: datetime) -> int:
    """Whole nanoseconds since the epoch, in integer arithmetic.

    ``timestamp() * 1e9`` goes through a float and loses nanoseconds, which is enough to
    pull two ids into the same millisecond and break the time ordering of UUIDv7.
    """
    delta = instant - _UNIX_EPOCH
    return (delta.days * 86_400 + delta.seconds) * _NANOSECONDS + delta.microseconds * 1_000


class Uuid7Generator:
    """The production adapter: time-ordered UUIDv7 from the injected clock."""

    __slots__ = ("_clock",)

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def new_id(self) -> UUID:
        generated = uuid_utils.uuid7(nanoseconds=_nanoseconds(self._clock.now()))
        # uuid_utils ships its own UUID class; the port speaks the standard library one.
        return UUID(hex=generated.hex)


class SequentialIdGenerator:
    """Deterministic ids for tests: a counter with the version and variant bits of a v7."""

    __slots__ = ("_counter",)

    def __init__(self) -> None:
        self._counter = 0

    def new_id(self) -> UUID:
        self._counter += 1
        body = bytearray(_SEQUENCE_PREFIX + self._counter.to_bytes(10, "big"))
        body[6] = (body[6] & 0x0F) | 0x70  # version 7
        body[8] = (body[8] & 0x3F) | 0x80  # RFC 4122 variant
        return UUID(bytes=bytes(body))


def uuid7() -> UUID:
    """Plain helper for column defaults in unmanaged models (docs/04 section 10.3).

    The database has no default for ids, so every insert goes through the ``IdGenerator``
    port; this exists for ``models.UUIDField(primary_key=True, default=uuid7)``.
    """
    return UUID(hex=uuid_utils.uuid7().hex)
