"""Accounting periods: UTC boundaries, the lock, and the two rules before it (TC-FR-04-5).

The lock is a date rule, and dates are where time zones hurt. ``guard_period`` compares
``(effective_at AT TIME ZONE 'UTC')::date`` with the period, which means a posting at
``2026-10-01T00:30+02:00`` belongs to September and is refused. That is the case this file
exists for: a naive comparison on the wall-clock date would let it through.

The rest is maker-checker: a lock is proposed by one finance lead and executed by another,
refused while exceptions are open (with the count), and reversible only through a second
approval. Corrections after a lock are reversing postings in an open period, never edits.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from unittest import mock

import pytest
from django.db import connection
from django.test import Client

from hub.identity import wiring as identity_wiring
from hub.identity.api.approval_views import ApprovalApproveView
from hub.ledger import wiring
from hub.ledger.application.periods import PeriodReopenCommand
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.ledger.domain.errors import PeriodLocked
from hub.shared.errors import PermissionDenied
from hub.shared.money import Money
from support.principals import SeededPrincipalAuthentication, SeededUser, seed_staff_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
PERIODS = "/api/v1/accounting-periods"


def _period(start: date, end: date, *, status: str = "open") -> uuid.UUID:
    """Insert a month. A locked one needs both lock columns (the schema's CHECK)."""
    period_id = uuid.uuid4()
    locked = status == "locked"
    locker = (
        seed_staff_user("finance_officer", attributes=("finance_lead",)).user_id if locked else None
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.accounting_periods (id, period_start, period_end, status,"
            " locked_by, locked_at) VALUES (%s, %s, %s, %s, %s, %s)",
            [period_id, start, end, status, locker, NOW if locked else None],
        )
    return period_id


def _fee_at(world: Any, when: datetime, *, amount: int = 100) -> Any:
    """A fee posting at an explicit instant: the date rule is about ``effective_at``."""
    with wiring.unit_of_work() as uow:
        return wiring.service().post(
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
                effective_at=when,
                source_top_up_id=world.top_up_id,
            ),
            uow=uow,
        )


@pytest.mark.tc("TC-FR-04-5")
def test_period_lock_boundaries_use_utc_date(world) -> None:  # noqa: ANN001
    """The last instant of September and a +02:00 October morning both belong to September."""
    _period(date(2026, 9, 1), date(2026, 9, 30), status="locked")

    with pytest.raises(PeriodLocked):
        _fee_at(world, datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC))

    with pytest.raises(PeriodLocked):
        # 2026-10-01T00:30+02:00 is 2026-09-30T22:30Z: inside the locked month.
        _fee_at(world, datetime(2026, 10, 1, 0, 30, tzinfo=timezone(timedelta(hours=2))))

    allowed = _fee_at(world, datetime(2026, 10, 1, 0, 0, tzinfo=UTC))
    assert allowed.replayed is False


@pytest.mark.tc("TC-FR-04-5")
def test_locked_period_returns_409_period_locked(
    api: Any, world: Any, finance_lead_maker: SeededUser, finance_lead_checker: SeededUser
) -> None:
    """Over HTTP: the approval route answers 409 ``period_locked`` and the money does not move."""
    today = lead_day()
    _period(today.replace(day=1), _month_end(today), status="locked")

    proposal = api.post(
        "/api/v1/ledger/adjustments",
        data=json.dumps(
            {
                "customer_id": str(world.customer_id),
                "direction": "credit_wallet",
                "amount": {"amount_minor": 500, "currency": "USD"},
                "reason": "an adjustment dated in a closed month",
            }
        ),
        content_type="application/json",
        headers=finance_lead_maker.headers(),
    )
    assert proposal.status_code == 202, proposal.content
    approval_id = proposal.json()["data"]["id"]

    with _approval_client() as client:
        response = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            data=json.dumps({}),
            content_type="application/json",
            headers=finance_lead_checker.headers(),
        )

    assert response.status_code == 409, response.content
    assert response.json()["code"] == "period_locked"
    assert world.balance() == 100_000


def test_correction_is_reversal_in_open_period(
    make_world_factory: Any, fund_wallet: Any, reversal_event: Any
) -> None:
    """A September posting is corrected by a reversal dated in October, and that is allowed."""
    # The credit is dated in September, posted before the month is locked. Re-dating a stored
    # transaction is not an option: `ledger_transactions` is append-only (invariant 4).
    world = make_world_factory()
    fund_wallet(world, 100_000, effective_at=datetime(2026, 9, 20, 9, 0, tzinfo=UTC))
    _period(date(2026, 9, 1), date(2026, 9, 30), status="locked")

    event = reversal_event(world, amount_minor=100_000)
    with wiring.unit_of_work() as uow:
        result = wiring.service().reverse(
            world.top_up_transaction_id,
            posting_key=f"reversal:{uuid.uuid4()}",
            provider_event_id=event,
            uow=uow,
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT effective_at FROM hub.ledger_transactions WHERE id = %s",
            [result.reversal_transaction_id],
        )
        assert cursor.fetchone()[0].date() >= date(2026, 10, 1)


def test_lock_needs_second_finance_lead(
    api: Any, finance_lead_maker: SeededUser, finance_lead_checker: SeededUser
) -> None:
    period_id = _period(date(2026, 9, 1), date(2026, 9, 30))
    response = api.post(
        f"{PERIODS}/{period_id}/lock",
        data=json.dumps({"note": "closing September"}),
        content_type="application/json",
        headers=finance_lead_maker.headers(),
    )
    assert response.status_code == 200, response.content
    assert response.json()["data"]["status"] == "open", "the proposal locks nothing"
    approval_id = uuid.UUID(response["Location"].rsplit("/", 1)[1])

    # An approver without the attribute cannot decide a close, even with the permission.
    plain = seed_staff_user("finance_officer")
    with pytest.raises(PermissionDenied):
        identity_wiring.approvals().approve(plain.principal, approval_id)

    identity_wiring.approvals().approve(finance_lead_checker.principal, approval_id)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, locked_by FROM hub.accounting_periods WHERE id = %s", [period_id]
        )
        status, locked_by = cursor.fetchone()
        assert status == "locked"
        assert locked_by == finance_lead_checker.user_id
        cursor.execute(
            "SELECT count(*) FROM hub.audit_log WHERE action = 'period.lock' AND entity_id = %s",
            [period_id],
        )
        assert cursor.fetchone()[0] == 1


def test_lock_refused_with_open_exceptions_and_conflict(
    api: Any, finance_lead_maker: SeededUser
) -> None:
    """Three open items: 409 with the count, so the maker knows what is left."""
    period_id = _period(date(2026, 9, 1), date(2026, 9, 30))
    with _exceptions(3):
        response = api.post(
            f"{PERIODS}/{period_id}/lock",
            data=json.dumps({}),
            content_type="application/json",
            headers=finance_lead_maker.headers(),
        )
    assert response.status_code == 409, response.content
    # The count rides in the problem body because `AccountingPeriod.open_exceptions` is a
    # response field of its own, not a property the Problem schema enumerates.
    assert response.json()["open_exceptions"] == 3


def test_reopen_needs_approval(
    finance_lead_maker: SeededUser, finance_lead_checker: SeededUser
) -> None:
    period_id = _period(date(2026, 9, 1), date(2026, 9, 30), status="locked")
    receipt = wiring.period_reopens()(
        finance_lead_maker.principal,
        PeriodReopenCommand(period_id=period_id, reason="a supplier invoice arrived late"),
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT status FROM hub.accounting_periods WHERE id = %s", [period_id])
        assert cursor.fetchone()[0] == "locked", "a proposal reopens nothing"

    identity_wiring.approvals().approve(finance_lead_checker.principal, receipt.id)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, locked_by, locked_at FROM hub.accounting_periods WHERE id = %s",
            [period_id],
        )
        assert cursor.fetchone() == ("open", None, None)


def test_list_periods_filter_status(api: Any, finance_lead_maker: SeededUser) -> None:
    open_id = _period(date(2026, 8, 1), date(2026, 8, 31))
    locked_id = _period(date(2026, 7, 1), date(2026, 7, 31), status="locked")

    response = api.get(f"{PERIODS}?status=locked", headers=finance_lead_maker.headers())
    assert response.status_code == 200, response.content
    ids = [row["id"] for row in response.json()["data"]]
    assert ids == [str(locked_id)]
    assert ids != [str(open_id)]


def lead_day() -> date:
    from hub.shared.clock import current  # noqa: PLC0415

    return current().date()


def _month_end(on: date) -> date:
    first = on.replace(day=1)
    next_first = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return next_first - timedelta(days=1)


@contextmanager
def _approval_client() -> Iterator[Client]:
    """A client for `/approvals`, whose caller comes from the test header."""
    with mock.patch.object(
        ApprovalApproveView, "authentication_classes", [SeededPrincipalAuthentication]
    ):
        yield Client()


@contextmanager
def _exceptions(count: int) -> Iterator[None]:
    """Install a counter that reports ``count`` open items, as slice 14 will."""

    class Counter:
        def open_count(self, period_start: Any, period_end: Any) -> int:
            del period_start, period_end
            return count

    wiring.use_open_exception_counter(Counter())
    try:
        yield
    finally:
        wiring.use_open_exception_counter(_NoExceptions())


class _NoExceptions:
    def open_count(self, period_start: Any, period_end: Any) -> int:
        del period_start, period_end
        return 0
