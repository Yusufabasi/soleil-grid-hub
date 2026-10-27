"""The three nightly jobs that keep the ledger's books honest (NFR-05, docs/09 3.4).

* ``ledger.snapshot_balances`` (00:30 UTC) materialises every system account's balance for the
  previous day. It is incremental and idempotent: the SQL adds the day's delta to the previous
  snapshot and ``ON CONFLICT DO NOTHING`` makes a re-run a no-op.
* ``ledger.wallet_drift`` (01:00 UTC) compares the maintained ``wallet_balances`` row with the
  fold of its entries. A difference means a bug in the posting path or a tampered row, so the
  job counts it, logs account ids only (never a customer), and stages ``ledger.drift_detected``
  for whoever is on call.
* ``ledger.imbalance_check`` (01:30 UTC) proves the two invariants the schema makes
  unrepresentable: every transaction sums to zero per currency, and one top-up was credited
  once. Expect zero rows, forever.

Each returns the number of problems found, which is what the job runner logs and the test
asserts; none of them raises on a finding, because a failed job that stops the schedule is
worse than a job that reports and lets the next one run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog

from hub.ledger.contracts import LedgerReaderPort, LedgerWriterPort
from hub.shared.events import DomainEvent
from hub.shared.jobs import JobContext
from hub.shared.metrics import counter

__all__ = [
    "SNAPSHOT_CRON",
    "DRIFT_CRON",
    "IMBALANCE_CRON",
    "drift_detected",
    "imbalance_check_job",
    "register_jobs",
    "snapshot_balances_job",
    "wallet_drift_job",
]

logger = structlog.get_logger(__name__)

SNAPSHOT_CRON = "30 0 * * *"
DRIFT_CRON = "0 1 * * *"
IMBALANCE_CRON = "30 1 * * *"

# Prometheus appends _total: the series is ledger_wallet_drift_total.
WALLET_DRIFT = counter(
    "ledger_wallet_drift", [], "Wallet balances whose maintained value differs from their entries"
)


@dataclass(frozen=True, slots=True)
class _DriftRow:
    """One drifting wallet, with ids only (no customer data leaves the job)."""

    account_id: str
    maintained_minor: int
    derived_minor: int


def snapshot_balances_job(ctx: JobContext, *, writer: LedgerWriterPort) -> int:
    """Snapshot yesterday (UTC): the day that is over, never the one in progress."""
    day = (ctx.now - timedelta(days=1)).date()
    written = writer.snapshot(day)
    logger.info("ledger.snapshot_balances", snapshot_date=day.isoformat(), rows=written)
    return written


def wallet_drift_job(
    ctx: JobContext, *, reader: LedgerReaderPort, unit_of_work: Any, ids: Any
) -> int:
    """Count the wallets whose maintained balance disagrees with their entries."""
    rows = [
        _DriftRow(
            account_id=str(row["account_id"]),
            maintained_minor=int(row["maintained_minor"]),
            derived_minor=int(row["derived_minor"]),
        )
        for row in reader.drift()
    ]
    if not rows:
        logger.info("ledger.wallet_drift.clean")
        return 0
    WALLET_DRIFT.inc(len(rows))
    logger.error(
        "ledger.wallet_drift.found",
        drift_count=len(rows),
        account_ids=[row.account_id for row in rows],
    )
    with unit_of_work(None) as uow:
        uow.outbox.add(
            drift_detected(
                ids=ids,
                occurred_at=ctx.now,
                account_ids=[row.account_id for row in rows],
            )
        )
    return len(rows)


def imbalance_check_job(ctx: JobContext, *, reader: LedgerReaderPort) -> int:
    """Run both integrity queries; anything found is a page, and the count says how bad."""
    found = reader.imbalances()
    if found:
        logger.error("ledger.imbalance.found", problems=len(found), checks=found)
    else:
        logger.info("ledger.imbalance.clean")
    return len(found)


def drift_detected(*, ids: Any, occurred_at: datetime, account_ids: list[str]) -> DomainEvent:
    """``ledger.drift_detected``: the outbox row the alerting consumer reacts to."""
    event_id = ids.new_id()
    return DomainEvent(
        event_id=event_id,
        name="ledger.drift_detected",
        version=1,
        occurred_at=occurred_at,
        correlation_id=event_id,
        causation_id=None,
        aggregate_type="ledger_account",
        aggregate_id=UUID(account_ids[0]) if account_ids else UUID(int=0),
        payload={"drift_count": len(account_ids), "account_ids": account_ids},
    )


def register_jobs(
    *, writer: LedgerWriterPort, reader: LedgerReaderPort, unit_of_work: Any, ids: Any
) -> None:
    """Contribute the three crons to the kernel's registry (``LedgerConfig.ready``)."""
    from hub.shared.jobs import register_cron  # noqa: PLC0415

    register_cron(
        name="ledger.snapshot_balances",
        cron=SNAPSHOT_CRON,
        handler=lambda ctx: snapshot_balances_job(ctx, writer=writer),
    )
    register_cron(
        name="ledger.wallet_drift",
        cron=DRIFT_CRON,
        handler=lambda ctx: wallet_drift_job(
            ctx, reader=reader, unit_of_work=unit_of_work, ids=ids
        ),
    )
    register_cron(
        name="ledger.imbalance_check",
        cron=IMBALANCE_CRON,
        handler=lambda ctx: imbalance_check_job(ctx, reader=reader),
    )
