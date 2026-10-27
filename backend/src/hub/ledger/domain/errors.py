"""Ledger failures and the codes clients branch on (docs/05 section 5, docs/03 7.1).

The catalogue is closed, so four of these are new *codes* only where the contract already
names one; the overdraft keeps the documented 409 ``conflict`` and a localised message,
because a wallet that cannot pay is a state conflict, not a category of its own. Every class
carries the same ``message_key`` so a French client sees a French sentence (the catalogue lives
in ``ledger/locale``).

``CurrencyMismatch`` is re-exported rather than redefined: the value object that raises it is
``hub.shared.money.Money``, and two classes with one code would be one too many.
"""

from __future__ import annotations

from hub.shared.errors import Conflict, InvariantViolation, NotFound, ValidationFailed
from hub.shared.money import CurrencyMismatch

__all__ = [
    "ApprovalRequired",
    "CurrencyMismatch",
    "EvidenceRequired",
    "InsufficientWalletBalance",
    "LedgerNotFound",
    "PeriodLocked",
    "UnbalancedPosting",
    "UnknownAccount",
]


class UnbalancedPosting(ValidationFailed):
    """Lines that do not sum to zero per currency (or too few of them): 422.

    The database refuses it too — with a deferred constraint trigger at commit — but arriving
    there would mean a write was attempted with a posting the caller can be told about sooner.
    """

    code = "unbalanced_posting"
    message_key = "unbalanced_posting"


class PeriodLocked(Conflict):
    """A posting dated inside a locked accounting period: 409 ``period_locked`` (D-23).

    The correction is a reversing posting dated in an open period, never an edit of history.
    """

    code = "period_locked"
    message_key = "period_locked"


class ApprovalRequired(Conflict):
    """A manual kind without a valid, approved, unconsumed approval: 409 ``approval_required``.

    The approval is bound to the exact lines by its payload hash, so this also covers an
    approval that was reused or whose lines differ by one minor unit from what is posted.
    """

    code = "approval_required"
    message_key = "approval_required"


class EvidenceRequired(ValidationFailed):
    """A reversal with neither a stored provider event nor an approval: 422 (D-06)."""

    code = "evidence_required"
    message_key = "evidence_required"


class InsufficientWalletBalance(Conflict):
    """A debit larger than the wallet holds: 409 (no overdraft on wallets, D-03).

    The code is the documented ``conflict``; the message key narrows it to the wallet, so the
    client can say \"top up first\" instead of \"try again\".
    """

    code = "conflict"
    message_key = "insufficient_wallet_balance"


class LedgerNotFound(NotFound):
    """A ledger row that does not exist or is outside the caller's scope: 404."""

    message_key = "not_found"


class UnknownAccount(ValidationFailed):
    """An ``AccountRef`` that resolves to no account: 422.

    A wallet that was never opened is the ordinary case: the customer exists but has not been
    funded, so the reference names an account the ledger has not created yet.
    """

    code = "unbalanced_posting"
    message_key = "unknown_ledger_account"


class LedgerInvariant(InvariantViolation):
    """A breach the database did not explain in words we map: 500, page on-call."""
