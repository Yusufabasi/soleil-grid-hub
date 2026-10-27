"""What runs when a second person approves a ledger decision (D-15, Task 03.5 and 03.6).

An ``ApprovalExecutor`` is a callback registered on `identity`'s registry; it executes inside
the approval's own transaction, so the decision, the posting and the audit row commit together
or not at all. Four live here:

* ``ledger.adjustment`` posts the stored lines **unchanged** — the database compares their hash
  with the approved one, so touching them, even by a minor unit, would be refused;
* ``ledger.refund`` reverses the top-up in the payload through ``reverse_ledger_transaction``;
* ``period.lock`` and ``period.reopen`` change one ``accounting_periods`` row.

The payload arrives as the jsonb the approval stored. For an adjustment that is the array of
lines; for the others an object. Reconstructing :class:`PostingLine` values means asking the
reader what each account id is, which is also the check that the accounts still exist.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from hub.identity.contracts import AuditLog, Principal
from hub.ledger.application.periods import open_exception_code
from hub.ledger.contracts import (
    AccountingPeriodsPort,
    AccountRef,
    LedgerReaderPort,
    LedgerService,
    OpenExceptionCounter,
    PostingLine,
    PostingRequest,
)
from hub.ledger.domain.errors import ApprovalRequired, LedgerNotFound
from hub.shared.contracts import UnitOfWork
from hub.shared.errors import ValidationFailed
from hub.shared.money import Money
from hub.shared.ports import Clock

__all__ = [
    "AdjustmentExecutor",
    "PeriodLockExecutor",
    "PeriodReopenExecutor",
    "RefundExecutor",
]

ADJUSTMENT_KIND = "manual_adjustment"
REFUND_KIND = "refund"
LOCK_KIND = "period_lock"
REOPEN_KIND = "period_reopen"

# The permission a finance officer holds; every ledger approval is finance's to decide.
FINANCE_PERMISSION = "ledger.adjust"
# Tariff-style configuration permission, used for the period actions.
CONFIG_PERMISSION = "config.manage"
FINANCE_ATTRIBUTE = "finance_lead"


class AdjustmentExecutor:
    """``manual_adjustment``: post the lines the second finance lead approved."""

    kind = ADJUSTMENT_KIND
    required_permission = FINANCE_PERMISSION
    required_attribute = FINANCE_ATTRIBUTE

    def __init__(
        self, *, clock: Clock, service: LedgerService, reader: LedgerReaderPort, audit: AuditLog
    ) -> None:
        self._clock = clock
        self._service = service
        self._reader = reader
        self._audit = audit

    def execute(
        self,
        payload: Any,
        principal: Principal,
        uow: UnitOfWork,
        *,
        approval_id: UUID,
    ) -> Mapping[str, Any]:
        lines = _lines_from_payload(payload, self._reader)
        result = self._service.post(
            PostingRequest(
                posting_key=f"adjustment:{approval_id}",
                kind="adjustment",
                lines=lines,
                effective_at=self._clock.now(),
                description=f"adjustment {approval_id}",
                approval_id=approval_id,
            ),
            uow=uow,
        )
        self._audit.record(
            stream="finance",
            actor=principal,
            action="ledger.adjustment.posted",
            entity_type="ledger_transaction",
            entity_id=result.transaction_id,
            site_id=None,
            details={"approval_id": str(approval_id), "replayed": result.replayed},
        )
        return {"transaction_id": str(result.transaction_id)}


class RefundExecutor:
    """``refund``: reverse the approved top-up; the shortfall rule is the database's."""

    kind = REFUND_KIND
    required_permission = FINANCE_PERMISSION
    required_attribute = FINANCE_ATTRIBUTE

    def __init__(self, *, service: LedgerService, audit: AuditLog) -> None:
        self._service = service
        self._audit = audit

    def execute(
        self,
        payload: Any,
        principal: Principal,
        uow: UnitOfWork,
        *,
        approval_id: UUID,
    ) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping) or not payload.get("original_transaction_id"):
            raise ApprovalRequired(details={"reason": "the payload names no transaction"})
        original = UUID(str(payload["original_transaction_id"]))
        result = self._service.reverse(
            original,
            posting_key=f"refund:{approval_id}",
            approval_id=approval_id,
            uow=uow,
        )
        self._audit.record(
            stream="finance",
            actor=principal,
            action="ledger.refund.posted",
            entity_type="ledger_transaction",
            entity_id=result.reversal_transaction_id,
            site_id=None,
            details={
                "approval_id": str(approval_id),
                "original_transaction_id": str(original),
                "shortfall_minor": result.shortfall.amount_minor,
                "currency": result.shortfall.currency,
            },
        )
        return {
            "reversal_transaction_id": str(result.reversal_transaction_id),
            "shortfall_minor": result.shortfall.amount_minor,
        }


class PeriodLockExecutor:
    """``period_lock``: close the month, re-checking the open-exception count first."""

    kind = LOCK_KIND
    required_permission = CONFIG_PERMISSION
    required_attribute = FINANCE_ATTRIBUTE

    def __init__(
        self,
        *,
        clock: Clock,
        periods: AccountingPeriodsPort,
        reader: LedgerReaderPort,
        exceptions: OpenExceptionCounter,
        audit: AuditLog,
    ) -> None:
        self._clock = clock
        self._periods = periods
        self._reader = reader
        self._exceptions = exceptions
        self._audit = audit

    def execute(
        self,
        payload: Any,
        principal: Principal,
        uow: UnitOfWork,
        *,
        approval_id: UUID,
    ) -> Mapping[str, Any]:
        period_id = _period_id(payload)
        period = self._reader.period(period_id)
        if period is None:
            raise LedgerNotFound()
        # Re-checked here, not only at proposal time: an exception may have opened while the
        # approval waited, and a month is not closed over one.
        count = self._exceptions.open_count(period.period_start, period.period_end)
        if count > 0:
            raise open_exception_code(count)
        changed = self._periods.lock(
            period_id, approver_id=principal.subject_id, at=self._clock.now()
        )
        self._audit.record(
            stream="finance",
            actor=principal,
            action="period.lock",
            entity_type="accounting_period",
            entity_id=period_id,
            site_id=None,
            details={
                "approval_id": str(approval_id),
                "period_start": period.period_start.isoformat(),
                "period_end": period.period_end.isoformat(),
                "already_locked": not changed,
            },
        )
        return {"period_id": str(period_id), "status": "locked"}


class PeriodReopenExecutor:
    """``period_reopen``: reopen a locked month (no HTTP route; the queue is the route)."""

    kind = REOPEN_KIND
    required_permission = CONFIG_PERMISSION
    required_attribute = FINANCE_ATTRIBUTE

    def __init__(
        self,
        *,
        periods: AccountingPeriodsPort,
        reader: LedgerReaderPort,
        audit: AuditLog,
    ) -> None:
        self._periods = periods
        self._reader = reader
        self._audit = audit

    def execute(
        self,
        payload: Any,
        principal: Principal,
        uow: UnitOfWork,
        *,
        approval_id: UUID,
    ) -> Mapping[str, Any]:
        period_id = _period_id(payload)
        if self._reader.period(period_id) is None:
            raise LedgerNotFound()
        changed = self._periods.reopen(period_id)
        self._audit.record(
            stream="finance",
            actor=principal,
            action="period.reopen",
            entity_type="accounting_period",
            entity_id=period_id,
            site_id=None,
            details={"approval_id": str(approval_id), "already_open": not changed},
        )
        return {"period_id": str(period_id), "status": "open"}


def _period_id(payload: Any) -> UUID:
    if not isinstance(payload, Mapping) or not payload.get("period_id"):
        raise ValidationFailed(
            details={"errors": [{"field": "/payload/period_id", "code": "required", "message": ""}]}
        )
    return UUID(str(payload["period_id"]))


def _lines_from_payload(payload: Any, reader: LedgerReaderPort) -> list[PostingLine]:
    """Rebuild the approved lines, resolving each account id back to the ref it names.

    The array order is preserved: ``hub.payload_hash`` is computed over the jsonb array, so the
    lines must arrive in the order the approval stored.
    """
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, Mapping)):
        raise ApprovalRequired(details={"reason": "an adjustment payload is an array of lines"})
    lines: list[PostingLine] = []
    for entry in payload:
        if not isinstance(entry, Mapping):
            raise ApprovalRequired(details={"reason": "a line is an object"})
        account_id = UUID(str(entry["account_id"]))
        account = reader.account(account_id)
        if account is None:
            raise ApprovalRequired(details={"reason": "an account of the payload is gone"})
        amount_minor = int(entry["amount_minor"])
        if amount_minor == 0:
            raise ApprovalRequired(details={"reason": "a line may not be zero"})
        lines.append(
            PostingLine(
                account=_ref_of(account.kind, account.currency, account.customer_id, account.code),
                direction="debit" if amount_minor > 0 else "credit",
                amount=Money(amount_minor=abs(amount_minor), currency=account.currency),  # type: ignore[arg-type]
            )
        )
    return lines


def _ref_of(kind: str, currency: str, customer_id: UUID | None, code: str) -> AccountRef:
    """The ``AccountRef`` an account row names; ``provider_clearing`` carries its provider."""
    if kind == "customer_wallet":
        return AccountRef(
            kind="customer_wallet",
            currency=currency,  # type: ignore[arg-type]
            customer_id=customer_id,
        )
    if kind == "provider_clearing":
        return AccountRef(
            kind="provider_clearing",
            currency=currency,  # type: ignore[arg-type]
            provider=code.split(".")[1] if code.count(".") >= 2 else None,
        )
    return AccountRef(kind=kind, currency=currency)  # type: ignore[arg-type]
