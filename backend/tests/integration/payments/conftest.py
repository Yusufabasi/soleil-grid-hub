"""Fixtures for the payments suite: a seeded customer, a frozen clock, one client.

The clock is frozen for the reason the registry suite freezes it: expiry, the seven-day
idempotency window and the 300-second webhook tolerance are all boundaries, and a test that
sleeps for them is a test that fails on a slow machine. The ordering note on ``frozen`` is the
lesson slice 02 learned the hard way — ``redis_store`` resets `identity`'s wiring, which drops
whatever clock was installed before it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client

from hub.customers import wiring as customers_wiring
from hub.identity import wiring as identity_wiring
from hub.payments import wiring as payments_wiring
from hub.shared.clock import FrozenClock
from support.payments import NOW, World, make_world


@pytest.fixture(autouse=True)
def frozen(redis_store) -> Iterator[FrozenClock]:  # noqa: ANN001 - the fixture's job is the store
    """A frozen clock installed *after* the store fixture, which resets identity's wiring."""
    clock = FrozenClock(NOW)
    identity_wiring.use_clock(clock)
    customers_wiring.reset()
    payments_wiring.reset()
    yield clock
    payments_wiring.reset()
    customers_wiring.reset()
    identity_wiring.use_clock(None)
    identity_wiring.reset()
    payments_wiring.reset()


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def world(frozen: FrozenClock) -> World:
    return make_world(clock=frozen)


@pytest.fixture
def make_world_factory(frozen: FrozenClock) -> Any:
    """The seeding helper as a fixture: tests never import another test module."""

    def build(**kwargs: Any) -> World:
        return make_world(clock=frozen, **kwargs)

    return build
