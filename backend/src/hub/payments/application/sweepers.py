"""The three sweepers that keep a waiting top-up from waiting forever (Task 04.10).

Each one follows the same shape, because each one has the same two hazards:

* **The provider call is outside the transaction.** Claiming rows locks them; calling a third
  party while holding the lock would make a slow provider stall the sweeper's own transaction
  and, through it, the rows it touches.
* **The claim is the only concurrency control.** ``claim_due`` takes rows ``FOR UPDATE SKIP
  LOCKED`` in a short transaction, so two replicas of the sweeper split the work instead of
  racing over it, and a row one replica is working on is not handed to the other.

What they do *not* do is decide money. A poll that finds a successful charge leaves the credit
to the webhook (the provider is contractually the one that must deliver it) and, after a grace
window, raises a reconciliation exception — the money is visible to a human rather than credited
by a guess.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from hub.payments.application.ports import CollectionRequest, ProviderGateway, ProviderResolver
from hub.payments.contracts import TopUpRow
from hub.payments.domain.top_up import TopUp
from hub.payments.events import reconciliation_exception
from hub.shared.contracts import UnitOfWork
from hub.shared.errors import DomainError
from hub.shared.money import Currency, Money
from hub.shared.ports import Clock, IdGenerator

__all__ = [
    "ExpireTopUps",
    "RetryInitiation",
    "SweepPendingUnknown",
    "SweeperOutcome",
]

UnitOfWorkFactory = Callable[[Any], UnitOfWork]

# How long an initiation may sit unanswered before the sweeper asks the provider again. The
# provider call has its own ten-second timeout, so a row older than this has definitely failed
# rather than being in flight.
RETRY_INITIATION_AFTER = timedelta(seconds=60)

# How long a `pending_unknown` row may stay unresolved before a successful pull is reported to a
# human instead of being credited by the sweeper (the webhook remains the credit path).
PENDING_UNKNOWN_GRACE = timedelta(minutes=30)


class SweeperStore(Protocol):
    """The claims and writes the sweepers make of ``hub.top_ups``."""

    def claim_due(
        self,
        *,
        statuses: Sequence[str],
        due_column: str,
        before: datetime,
        limit: int,
        uow: UnitOfWork,
    ) -> list[TopUpRow]: ...

    def update_fields(self, top_up_id: UUID, **columns: Any) -> None: ...


class SweeperOutcome:
    """How many rows one run changed, for the job's log line and metrics."""

    __slots__ = ("changed", "scanned")

    def __init__(self, *, scanned: int, changed: int) -> None:
        self.scanned = scanned
        self.changed = changed

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"SweeperOutcome(scanned={self.scanned}, changed={self.changed})"


class ExpireTopUps:
    """``payments.expire_top_ups``: a waiting top-up whose window closed becomes ``expired``.

    Only ``initiated`` and ``pending`` expire. ``pending_unknown`` never does: the provider may
    still be holding the money, and expiring it would hide a charge that is about to arrive.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        top_ups: SweeperStore,
        unit_of_work: UnitOfWorkFactory,
        batch: int = 200,
    ) -> None:
        self._clock = clock
        self._top_ups = top_ups
        self._unit_of_work = unit_of_work
        self._batch = batch

    def run(self) -> SweeperOutcome:
        now = self._clock.now()
        with self._unit_of_work(None) as uow:
            rows = self._top_ups.claim_due(
                statuses=("initiated", "pending"),
                due_column="expires_at",
                before=now,
                limit=self._batch,
                uow=uow,
            )
            for row in rows:
                self._top_ups.update_fields(row.id, status="expired", updated_at=now)
        return SweeperOutcome(scanned=len(rows), changed=len(rows))


class RetryInitiation:
    """``payments.retry_initiation``: ask the provider again for a collection nobody answered.

    The reference is the top-up's own id, so the provider's idempotency makes the retry safe: a
    collection that already exists is the same collection, not a second charge.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        top_ups: SweeperStore,
        provider: ProviderResolver,
        unit_of_work: UnitOfWorkFactory,
        batch: int = 50,
    ) -> None:
        self._clock = clock
        self._top_ups = top_ups
        self._provider = provider
        self._unit_of_work = unit_of_work
        self._batch = batch

    def run(self) -> SweeperOutcome:
        now = self._clock.now()
        with self._unit_of_work(None) as uow:
            rows = self._top_ups.claim_due(
                statuses=("initiated",),
                due_column="initiated_at",
                before=now - RETRY_INITIATION_AFTER,
                limit=self._batch,
                uow=uow,
            )

        changed = 0
        for row in rows:
            status = _initiation_status(self._provider(row.provider), row)
            if status is None:
                continue
            with self._unit_of_work(None):
                # The guard has no `initiated -> pending_unknown` edge, so the timeout answer
                # walks through `pending` (the same two-step path a confirmation takes). The
                # path comes from the domain's table, so this cannot invent an edge.
                current = TopUp(
                    id=row.id, status=row.status, expires_at=row.expires_at, late=row.late
                )
                for hop in current.path_to(status):
                    self._top_ups.update_fields(row.id, status=hop, updated_at=now)
            changed += 1
        return SweeperOutcome(scanned=len(rows), changed=changed)


class SweepPendingUnknown:
    """``payments.sweep_pending_unknown``: resolve a top-up whose initiation timed out.

    A pull that says ``pending`` puts the row back to ``pending`` (the collection exists). A pull
    that says ``failed`` fails it. A pull that says ``successful`` does **not** credit: the
    provider still owes the webhook, and crediting here would be a second path to the same money.
    After :data:`PENDING_UNKNOWN_GRACE` that state is raised as a reconciliation exception, once.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        top_ups: SweeperStore,
        provider: ProviderResolver,
        unit_of_work: UnitOfWorkFactory,
        batch: int = 50,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._top_ups = top_ups
        self._provider = provider
        self._unit_of_work = unit_of_work
        self._batch = batch

    def run(self) -> SweeperOutcome:
        now = self._clock.now()
        with self._unit_of_work(None) as uow:
            rows = self._top_ups.claim_due(
                statuses=("pending_unknown",),
                due_column="updated_at",
                before=now - PENDING_UNKNOWN_GRACE,
                limit=self._batch,
                uow=uow,
            )

        changed = 0
        for row in rows:
            reference = row.provider_txn_id or row.provider_ref or str(row.id)
            try:
                status = self._provider(row.provider).fetch_status(reference)
            except TimeoutError:
                # Still no answer: leave the row alone and let the next run try again.
                continue
            if status.status == "failed":
                with self._unit_of_work(None) as uow:
                    self._top_ups.update_fields(row.id, status="failed", updated_at=now)
                    uow.require_active()
                changed += 1
            elif status.status == "pending":
                with self._unit_of_work(None) as uow:
                    self._top_ups.update_fields(row.id, status="pending", updated_at=now)
                    uow.require_active()
                changed += 1
            elif status.status == "successful" and row.status_pull_at is None:
                # The provider took the money and the webhook never came. Record the pull (so a
                # second run does not raise the same exception) and hand it to a human.
                with self._unit_of_work(None) as uow:
                    self._top_ups.update_fields(row.id, status_pull_at=now, updated_at=now)
                    uow.outbox.add(
                        reconciliation_exception(
                            ids=self._ids,
                            occurred_at=now,
                            aggregate_id=row.id,
                            customer_id=row.customer_id,
                            amount=row.amount,
                            reason_code="pull_confirmed_no_webhook",
                            provider_event_id=None,
                            kind="statement_only",
                        )
                    )
                changed += 1
        return SweeperOutcome(scanned=len(rows), changed=changed)


def _initiation_status(provider: ProviderGateway, row: TopUpRow) -> str | None:
    """The status a retried initiation earns, or ``None`` when the answer changed nothing."""
    currency: Currency = row.amount.currency
    try:
        ack = provider.initiate_collection(
            CollectionRequest(
                top_up_id=row.id,
                customer_id=row.customer_id,
                meter_id=row.meter_id,
                amount=Money(row.amount_minor, currency),
                payer_msisdn=row.payer_msisdn or "",
                reference=row.provider_ref or str(row.id),
                expires_at=row.expires_at,
            )
        )
    except TimeoutError:
        return "pending_unknown"
    except DomainError:
        # A refusal from the provider is a definite answer about this collection.
        return "failed"
    if ack.accepted:
        return "pending"
    return "pending_unknown" if ack.unknown else "failed"
