"""Proposing a manual adjustment or a refund: the maker's half of maker-checker (D-15).

Nothing here posts. A proposal stores an approval whose payload is the exact set of lines the
posting function will receive, so the second person approves a specific ledger effect rather
than a sentence describing one. That constraint comes from the schema: ``post_ledger_transaction``
compares ``approvals.payload_hash`` with ``hub.payload_hash(p_lines)``, and therefore the
adjustment's payload *is* the lines array — no customer id, no reason, nothing else. The reason
and the ticket it came from are in the audit record written beside the proposal, which is where
a human looks for them.

The counter-account is ``suspense.<currency>``: an adjustment is money that arrived from
nowhere (or left for no stated reason), and suspense is the account that says so until finance
explains it. Refunds are not postings here at all — they execute
``reverse_ledger_transaction``, which knows how much the wallet still holds (D-06).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from hub.identity.contracts import AuditLog, Principal
from hub.ledger.contracts import (
    AccountRef,
    ApprovalProposer,
    LedgerReaderPort,
    ProposalReceipt,
    TopUpLookup,
)
from hub.ledger.domain.errors import LedgerNotFound, UnknownAccount
from hub.shared.errors import ValidationFailed
from hub.shared.ports import Clock

__all__ = [
    "AdjustmentCreate",
    "ProposeAdjustment",
    "ProposeRefund",
    "RefundCreate",
    "adjustment_lines",
]

AdjustmentDirection = Literal["credit_wallet", "debit_wallet"]

# The OpenAPI kind of each stored approval action; the schema's action names win on the row.
ADJUSTMENT_KIND = "manual_adjustment"
REFUND_KIND = "refund"

MIN_REASON = 10


@dataclass(frozen=True, slots=True)
class AdjustmentCreate:
    """``AdjustmentCreate`` of the contract: never ledger lines, always a direction."""

    customer_id: UUID
    direction: AdjustmentDirection
    amount_minor: int
    currency: str
    reason: str
    ticket_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RefundCreate:
    """``RefundCreate`` of the contract: which top-up, and why it goes back."""

    top_up_id: UUID
    amount_minor: int
    currency: str
    reason: str


def adjustment_lines(
    *,
    wallet_account_id: UUID,
    suspense_account_id: UUID,
    currency: str,
    direction: AdjustmentDirection,
    amount_minor: int,
) -> list[dict[str, Any]]:
    """The exact lines array an adjustment approval hashes and the posting replays.

    Debit is positive. Crediting a customer's wallet therefore debits suspense and credits the
    wallet; debiting it does the opposite. The wallet line is written last, which is also the
    order the entries are inserted in (ascending account id in SQL, but a stable array here
    keeps the proposal's payload_hash reproducible).
    """
    if amount_minor <= 0:
        raise ValidationFailed(
            details={
                "errors": [
                    {
                        "field": "/amount/amount_minor",
                        "code": "out_of_range",
                        "message": "The amount must be positive.",
                    }
                ]
            }
        )
    wallet = amount_minor if direction == "debit_wallet" else -amount_minor
    suspense = -wallet
    return [
        {
            "account_id": str(suspense_account_id),
            "currency": currency,
            "amount_minor": suspense,
        },
        {"account_id": str(wallet_account_id), "currency": currency, "amount_minor": wallet},
    ]


class ProposeAdjustment:
    """``createLedgerAdjustment``: validate, then store a proposal bound to the lines."""

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
        self, principal: Principal, command: AdjustmentCreate, *, ip: str | None = None
    ) -> ProposalReceipt:
        _require_reason(command.reason)
        if command.currency not in ("USD", "CDF"):
            raise _invalid("/amount/currency", "mismatch", "Unsupported currency.")
        wallet_ref = AccountRef(
            kind="customer_wallet",
            currency=command.currency,  # type: ignore[arg-type]
            customer_id=command.customer_id,
        )
        wallet_id = self._reader.account_id(wallet_ref)
        if wallet_id is None:
            raise UnknownAccount(details={"customer_id": str(command.customer_id)})
        suspense_id = self._reader.account_id(
            AccountRef(kind="suspense", currency=command.currency)  # type: ignore[arg-type]
        )
        if suspense_id is None:
            raise UnknownAccount(details={"account": f"suspense.{command.currency}"})

        lines = adjustment_lines(
            wallet_account_id=wallet_id,
            suspense_account_id=suspense_id,
            currency=command.currency,
            direction=command.direction,
            amount_minor=command.amount_minor,
        )
        # The payload is the lines array and nothing else (see the module docstring).
        receipt = self._approvals.propose(
            principal,
            kind=ADJUSTMENT_KIND,
            payload=lines,
            reason=command.reason,
            subject_id=command.customer_id,
        )
        with self._unit_of_work(principal):
            self._audit.record(
                stream="finance",
                actor=principal,
                action="ledger.adjustment.proposed",
                entity_type="approval",
                entity_id=receipt.id,
                site_id=None,
                details={
                    "customer_id": str(command.customer_id),
                    "direction": command.direction,
                    "amount_minor": command.amount_minor,
                    "currency": command.currency,
                    "reason": command.reason,
                    "ticket_id": str(command.ticket_id) if command.ticket_id else None,
                },
                ip=ip,
                subject_customer_id=command.customer_id,
            )
        return receipt


class ProposeRefund:
    """``createRefund``: a received top-up is given back, once, by reversal (D-06)."""

    def __init__(
        self,
        *,
        clock: Clock,
        top_ups: TopUpLookup,
        approvals: ApprovalProposer,
        audit: AuditLog,
        unit_of_work: Any,
    ) -> None:
        self._clock = clock
        self._top_ups = top_ups
        self._approvals = approvals
        self._audit = audit
        self._unit_of_work = unit_of_work

    def __call__(
        self, principal: Principal, command: RefundCreate, *, ip: str | None = None
    ) -> ProposalReceipt:
        _require_reason(command.reason)
        top_up = self._top_ups.get(command.top_up_id)
        if top_up is None:
            raise LedgerNotFound()
        if top_up.status != "confirmed":
            raise _invalid(
                "/top_up_id",
                "state_conflict",
                "Only a confirmed top-up can be refunded.",
            )
        if command.currency != top_up.currency:
            raise _invalid(
                "/amount/currency",
                "mismatch",
                f"The refund must be in the top-up's currency ({top_up.currency}).",
            )
        if command.amount_minor != top_up.amount_minor:
            # `reverse_ledger_transaction` gives back the whole credit; a partial refund would
            # silently be a full one, which is worse than refusing it.
            raise _invalid(
                "/amount/amount_minor",
                "unsupported",
                "A refund returns the whole top-up; partial refunds are not supported.",
            )
        if top_up.ledger_transaction_id is None:
            raise _invalid(
                "/top_up_id",
                "state_conflict",
                "The top-up was never posted, so there is nothing to reverse.",
            )

        payload = {
            "original_transaction_id": str(top_up.ledger_transaction_id),
            "top_up_id": str(top_up.id),
            "amount_minor": command.amount_minor,
            "currency": command.currency,
            "reason": command.reason,
        }
        receipt = self._approvals.propose(
            principal,
            kind=REFUND_KIND,
            payload=payload,
            reason=command.reason,
            subject_id=top_up.customer_id,
        )
        with self._unit_of_work(principal):
            self._audit.record(
                stream="finance",
                actor=principal,
                action="ledger.refund.proposed",
                entity_type="approval",
                entity_id=receipt.id,
                site_id=None,
                details={
                    "top_up_id": str(top_up.id),
                    "customer_id": str(top_up.customer_id),
                    "amount_minor": command.amount_minor,
                    "currency": command.currency,
                    "reason": command.reason,
                },
                ip=ip,
                subject_customer_id=top_up.customer_id,
            )
        return receipt


def _require_reason(reason: str) -> None:
    if len(reason.strip()) < MIN_REASON:
        raise _invalid(
            "/reason",
            "too_short",
            f"The reason must be at least {MIN_REASON} characters: it is the audit trail.",
        )


def _invalid(field: str, code: str, message: str) -> ValidationFailed:
    return ValidationFailed(
        details={"errors": [{"field": field, "code": code, "message": message}]}
    )
