"""Clock and IdGenerator ports (docs/03 section 2.1, executor guide section 4 rule 8)."""

from datetime import UTC, datetime, timedelta

import pytest

from hub.shared.clock import FrozenClock, SystemClock
from hub.shared.ids import SequentialIdGenerator, Uuid7Generator
from hub.shared.ports import Clock, IdGenerator

START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


class MonotonicClock:
    """Test double: one millisecond newer on every read."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        self._now += timedelta(milliseconds=1)
        return self._now


def test_uuid7_is_version_7_and_time_ordered():
    generator: IdGenerator = Uuid7Generator(clock=MonotonicClock(START))

    ids = [generator.new_id() for _ in range(1000)]

    assert all(identifier.version == 7 for identifier in ids)
    assert len(set(ids)) == 1000
    assert sorted(ids) == ids


def test_sequential_generator_is_deterministic_and_ordered():
    first = SequentialIdGenerator()
    second = SequentialIdGenerator()

    ids = [first.new_id() for _ in range(5)]

    assert [second.new_id() for _ in range(5)] == ids
    assert sorted(ids) == ids
    assert all(identifier.version == 7 for identifier in ids)


def test_frozen_clock_advances():
    clock = FrozenClock(START)

    assert clock.now() == START
    assert clock.advance(timedelta(seconds=5)).now() == START + timedelta(seconds=5)
    assert clock.now().tzinfo is not None
    assert clock.now().utcoffset() == timedelta(0)
    # advancing is the only way time moves
    assert clock.now() == START + timedelta(seconds=5)


def test_frozen_clock_rejects_a_naive_instant():
    with pytest.raises(ValueError):
        FrozenClock(datetime(2026, 1, 1))


def test_system_clock_is_tz_aware():
    now = SystemClock().now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert now <= datetime.now(UTC)


def test_ids_and_clock_satisfy_the_port_protocols():
    assert isinstance(SystemClock(), Clock)
    assert isinstance(Uuid7Generator(clock=SystemClock()), IdGenerator)
