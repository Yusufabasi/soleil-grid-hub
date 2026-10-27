"""Snapshots, drift and the imbalance check (TC-FR-04-3, TC-NFR-05-2).

The three nightly jobs are the ledger's smoke detectors:

* ``snapshot_ledger_balances`` materialises system balances incrementally, so finance can read a
  historical month without folding its entries (and a re-run writes nothing);
* ``wallet_drift`` finds a maintained balance that disagrees with its entries — the signature of
  a tampered row or a bug in the posting path — and reports counts and account ids, never a
  customer;
* ``imbalance_check`` proves the two invariants the schema makes unrepresentable.

A job that raises on a finding would stop the schedule, so these return the count instead and
the test asserts it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.db import connection
from prometheus_client import REGISTRY

from hub.ledger import wiring
from hub.ledger.application.jobs import imbalance_check_job, snapshot_balances_job, wallet_drift_job
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.shared.jobs import JobContext
from hub.shared.money import Money

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def _fees_id() -> uuid.UUID:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM hub.ledger_accounts WHERE code = 'fees.USD'")
        row = cursor.fetchone()
    assert row is not None, "fees.USD"
    return uuid.UUID(str(row[0]))


def _fees_fold() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT coalesce(sum(amount_minor), 0) FROM hub.ledger_entries WHERE account_id = %s",
            [_fees_id()],
        )
        return int(cursor.fetchone()[0])


def _fee(world: Any, amount: int) -> None:

    with wiring.unit_of_work() as uow:
        wiring.service().post(
            PostingRequest(
                posting_key=f"fee:{uuid.uuid4()}",
                kind="fee",
                lines=[
                    PostingLine(
                        account=world.wallet, direction="debit", amount=Money(amount, "USD")
                    ),
                    PostingLine(
                        account=AccountRef(kind="fees", currency="USD"),
                        direction="credit",
                        amount=Money(amount, "USD"),
                    ),
                ],
                effective_at=NOW,
                source_top_up_id=world.top_up_id,
            ),
            uow=uow,
        )


def test_snapshot_is_incremental_and_idempotent(world: Any) -> None:
    """Yesterday's snapshot, then today's: today is yesterday plus the day's entries.

    The function windows by ``ledger_entries.created_at``, which the schema does not let a test
    backdate (the table is append-only). So the increment is proved the way the job sees it: a
    snapshot of the day before the entries (zero), then a snapshot of their day, which must be
    that zero plus the delta. Re-running either day inserts nothing.
    """
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    writer = wiring.writer()
    _fee(world, 100)

    assert writer.snapshot(yesterday) >= 1
    baseline = _snapshot(yesterday)
    assert baseline == 0, "no entry predates today"

    _fee(world, 250)
    assert writer.snapshot(today) >= 1
    after = _snapshot(today)
    # The table stores the raw sum of entries (debit positive), and a fee is a credit:
    # `db/schema.sql`'s comment on `ledger_balance_snapshots` is the authority.
    assert after == baseline - 350, "the prior balance plus the delta of the day"

    assert writer.snapshot(today) == 0, "re-running a day inserts nothing"
    assert writer.snapshot(yesterday) == 0


def _snapshot(day: Any) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT balance_minor FROM hub.ledger_balance_snapshots"
            " WHERE account_id = %s AND snapshot_date = %s",
            [_fees_id(), day],
        )
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def test_system_balance_equals_entry_sum(world: Any) -> None:
    """The reader's incremental balance is the fold, without folding history."""
    _fee(world, 100)
    _fee(world, 300)
    balance, count = wiring.reader().system_balance(_fees_id())
    assert balance == _fees_fold() == -400, "the raw fold, as the schema stores it"
    assert count == 2


def test_drift_job_clean_returns_zero(world: Any) -> None:
    ctx = JobContext(name="ledger.wallet_drift", now=NOW, ids=wiring.ids())
    before = REGISTRY.get_sample_value("ledger_wallet_drift_total") or 0.0
    assert (
        wallet_drift_job(
            ctx, reader=wiring.reader(), unit_of_work=wiring.unit_of_work, ids=wiring.ids()
        )
        == 0
    )
    assert (REGISTRY.get_sample_value("ledger_wallet_drift_total") or 0.0) == before


@pytest.mark.tc("TC-FR-04-3")
def test_drift_job_detects_tampered_wallet(world: Any) -> None:
    """One minor unit of drift is found, counted, logged by account id, and emitted."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE hub.wallet_balances SET balance_minor = balance_minor + 1 WHERE account_id = %s",
            [world.wallet_id],
        )
    before = REGISTRY.get_sample_value("ledger_wallet_drift_total") or 0.0
    ctx = JobContext(name="ledger.wallet_drift", now=NOW, ids=wiring.ids())

    found = wallet_drift_job(
        ctx, reader=wiring.reader(), unit_of_work=wiring.unit_of_work, ids=wiring.ids()
    )

    assert found == 1
    assert (REGISTRY.get_sample_value("ledger_wallet_drift_total") or 0.0) == before + 1
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM hub.outbox WHERE event_type = 'ledger.drift_detected'")
        assert cursor.fetchone()[0] == 1


@pytest.mark.tc("TC-NFR-05-2")
def test_imbalance_check_zero_rows(world: Any) -> None:
    """Every transaction sums to zero per currency, and one top-up was credited once."""
    _fee(world, 100)
    ctx = JobContext(name="ledger.imbalance_check", now=NOW, ids=wiring.ids())
    assert imbalance_check_job(ctx, reader=wiring.reader()) == 0


def test_snapshot_job_uses_yesterday() -> None:
    """The scheduled job snapshots the day that is over, never the one in progress."""

    written = snapshot_balances_job(
        JobContext(name="ledger.snapshot_balances", now=NOW, ids=wiring.ids()),
        writer=wiring.writer(),
    )
    assert written >= 1
    yesterday = (NOW - timedelta(days=1)).date()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM hub.ledger_balance_snapshots WHERE snapshot_date = %s",
            [yesterday],
        )
        assert cursor.fetchone()[0] >= 1
