"""Accounting periods closed through maker-checker (D-23, D-40, FR-14).

A lock says "this month's numbers are final". It is therefore a decision, not a button: the
maker proposes, a second ``finance_lead`` approves, and only then does the row change. Two
refusals come before the proposal is even stored:

* **open exceptions.** A month with unresolved reconciliation items is not closable — the count
  is in the 409 so the maker knows how much is left. Until slice 14 registers its counter the
  answer is zero, which is the honest one for a build without that module.
* **the attribute.** ``finance_lead`` is what makes the decision finance's, so the maker must
  hold it too: an administrator must not be able to start a close nobody asked for.

Reopening is the same flow in the other direction (``period.reopen``), and it has no HTTP route:
the contract exposes ``lockAccountingPeriod`` only, and a reopen is an exceptional correction
requested through the approval queue itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from hub.identity.contracts import AuditLog, Principal
from hub.ledger.contracts import (
    AccountingPeriodFacts,
    AccountingPeriodsPort,
    ApprovalProposer,
    LedgerReaderPort,
    OpenExceptionCounter,
    ProposalReceipt,
)
from hub.ledger.domain.errors import LedgerNotFound
from hub.shared.errors import Conflict, PermissionDenied, ValidationFailed
from hub.shared.ports import Clock

__all__ = [
    "ListPeriods",
    "PeriodLockCommand",
    "PeriodReopenCommand",
    "ProposePeriodLock",
    "ProposePeriodReopen",
    "open_exception_code",
]

LOCK_KIND = "period_lock"
REOPEN_KIND = "period_reopen"

# The attribute that makes a lock decision finance's rather than an administrator's.
FINANCE_ATTRIBUTE = "finance_lead"


@dataclass(frozen=True, slots=True)
class PeriodLockCommand:
    """``PeriodLock`` of the contract: the month, and the note recorded with the decision."""

    period_id: UUID
    note: str = ""


@dataclass(frozen=True, slots=True)
class PeriodReopenCommand:
    """Reopening: the month, and why history is being re-opened."""

    period_id: UUID
    reason: str


@dataclass(frozen=True, slots=True)
class ListPeriods:
    """One page of the months, filtered by status."""

    statuses: tuple[str, ...] = ()
    after: UUID | None = None
    limit: int = 25


def open_exception_code(count: int) -> Conflict:
    """The 409 a lock gets while items are open, with the count the contract promises."""
    return Conflict(
        code="conflict",
        message_key="open_exceptions_remain",
        details={"open_exceptions": count, "reason": "open_exceptions_remain"},
    )


class ProposePeriodLock:
    """``lockAccountingPeriod``: check the two rules, then store the proposal."""

    def __init__(
        self,
        *,
        clock: Clock,
        reader: LedgerReaderPort,
        exceptions: OpenExceptionCounter,
        approvals: ApprovalProposer,
        audit: AuditLog,
        unit_of_work: Any,
    ) -> None:
        self._clock = clock
        self._reader = reader
        self._exceptions = exceptions
        self._approvals = approvals
        self._audit = audit
        self._unit_of_work = unit_of_work

    def __call__(
        self, principal: Principal, command: PeriodLockCommand, *, ip: str | None = None
    ) -> ProposalReceipt:
        _require_finance(principal)
        period = self._period(command.period_id)
        if period.status == "locked":
            raise Conflict(code="conflict", message_key="period_already_locked")
        count = self._exceptions.open_count(period.period_start, period.period_end)
        if count > 0:
            raise open_exception_code(count)

        receipt = self._approvals.propose(
            principal,
            kind=LOCK_KIND,
            payload={
                "period_id": str(period.id),
                "period_start": period.period_start.isoformat(),
                "period_end": period.period_end.isoformat(),
                "note": command.note,
                "reason": command.note or "close the accounting period",
            },
            reason=command.note or "close the accounting period",
        )
        with self._unit_of_work(principal):
            self._audit.record(
                stream="finance",
                actor=principal,
                action="period.lock.proposed",
                entity_type="accounting_period",
                entity_id=period.id,
                site_id=None,
                details={
                    "period_start": period.period_start.isoformat(),
                    "period_end": period.period_end.isoformat(),
                    "note": command.note,
                },
                ip=ip,
            )
        return receipt

    def _period(self, period_id: UUID) -> AccountingPeriodFacts:
        period = self._reader.period(period_id)
        if period is None:
            raise LedgerNotFound()
        return period


class ProposePeriodReopen:
    """``period.reopen``: exceptional, approved, and audited like the lock itself."""

    def __init__(
        self,
        *,
        clock: Clock,
        reader: LedgerReaderPort,
        approvals: ApprovalProposer,
        audit: AuditLog,
        unit_of_work: Any,
    ) -> None:
        self._clock = clock
        self._reader = reader
        self._approvals = approvals
        self._audit = audit
        self._unit_of_work = unit_of_work

    def __call__(
        self, principal: Principal, command: PeriodReopenCommand, *, ip: str | None = None
    ) -> ProposalReceipt:
        _require_finance(principal)
        if len(command.reason.strip()) < 10:
            raise ValidationFailed(
                details={
                    "errors": [
                        {
                            "field": "/reason",
                            "code": "too_short",
                            "message": "Reopening a locked period needs a written reason.",
                        }
                    ]
                }
            )
        period = self._reader.period(command.period_id)
        if period is None:
            raise LedgerNotFound()
        if period.status != "locked":
            raise Conflict(code="conflict", message_key="period_not_locked")

        receipt = self._approvals.propose(
            principal,
            kind=REOPEN_KIND,
            payload={
                "period_id": str(period.id),
                "reason": command.reason,
            },
            reason=command.reason,
        )
        with self._unit_of_work(principal):
            self._audit.record(
                stream="finance",
                actor=principal,
                action="period.reopen.proposed",
                entity_type="accounting_period",
                entity_id=period.id,
                site_id=None,
                details={"reason": command.reason},
                ip=ip,
            )
        return receipt


def _require_finance(principal: Principal) -> None:
    if FINANCE_ATTRIBUTE not in principal.attributes:
        raise PermissionDenied(code="forbidden")


def period_document(period: AccountingPeriodFacts, *, open_exceptions: int = 0) -> dict[str, Any]:
    """``AccountingPeriod`` of the contract."""
    return {
        "id": str(period.id),
        "period_start": period.period_start.isoformat(),
        "period_end": period.period_end.isoformat(),
        "status": period.status,
        "open_exceptions": open_exceptions,
        "locked_by": str(period.locked_by) if period.locked_by is not None else None,
        "locked_at": period.locked_at.isoformat().replace("+00:00", "Z")
        if period.locked_at is not None
        else None,
    }


def ensure_month(
    periods: AccountingPeriodsPort, on: date, *, new_id: UUID, reader: LedgerReaderPort
) -> AccountingPeriodFacts:
    """The period containing ``on``, created if this is the first time anyone asked."""
    existing = reader.find_period_for(on)
    if existing is not None:
        return existing
    return periods.ensure_for(on, new_id=new_id)
