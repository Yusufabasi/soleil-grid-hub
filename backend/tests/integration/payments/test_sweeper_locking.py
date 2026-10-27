"""Two sweepers must not work on one row (`FOR UPDATE SKIP LOCKED`, Task 04.10).

This module is separate from `test_sweepers.py` because it is the one test that cannot run inside
pytest-django's transaction: the lock has to be held by a *second* connection and be visible to
the sweeper, which means the seeded rows must be committed. The teardown therefore removes them
itself — Django's ``flush`` does not know these unmanaged tables.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from hub.payments import wiring as payments_wiring
from hub.shared.jobs import run_job
from support.cleanup import purge_world
from support.payments import NOW, World
from support.webhooks import seed_top_up, top_up_row

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

LONG_AGO = NOW - timedelta(minutes=5)


@pytest.fixture
def committed(world: World) -> Iterator[World]:
    """A world whose rows are committed, and a guarantee they are removed afterwards."""
    try:
        yield world
    finally:
        purge_world(world)


def test_sweepers_skip_locked_rows(committed: World, postgres_server: Any) -> None:
    first = seed_top_up(committed, status="pending", expires_at=LONG_AGO)
    second = seed_top_up(committed, status="pending", expires_at=LONG_AGO)

    dsn = postgres_server.dsn("hub_test")
    with psycopg.connect(dsn) as locker:
        locker.execute("SELECT id FROM hub.top_ups WHERE id = %s FOR UPDATE", [first])
        counted = locker.execute("SELECT count(*) FROM hub.top_ups").fetchone()
        assert counted is not None and counted[0] >= 2

        outcome = run_job("payments.expire_top_ups", now=NOW, ids=payments_wiring.ids())

        # The locked row is skipped, not waited for; the other one is swept in this pass.
        assert outcome.changed == 1
        assert top_up_row(first)["status"] == "pending"
        assert top_up_row(second)["status"] == "expired"
        locker.rollback()

    # Once the lock is gone the row is picked up by the next run.
    run_job("payments.expire_top_ups", now=NOW, ids=payments_wiring.ids())
    assert top_up_row(first)["status"] == "expired"
