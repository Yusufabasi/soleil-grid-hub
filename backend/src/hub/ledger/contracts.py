"""Public surface of `ledger`: the posting vocabulary every money-moving module speaks.

The ledger is a leaf (docs/03 section 0): it imports ``shared`` only, and this file is the
whole of what its neighbours may import from it. Nothing here names a top-up, a token or a
meter — a posting is a key, a kind, an instant and a list of signed lines, and the module that
knows what those mean owns them.

The value objects are frozen and validate nothing the database also validates; :func:`to_signed_lines`
in ``ledger.domain.posting`` is the one place a request becomes entries, and it checks the rules
that can be checked before a round trip.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from hub.shared.contracts import UnitOfWork
from hub.shared.money import Currency, CurrencyMismatch, Money

__all__ = [
    "AccountKind",
    "AccountRef",
    "AccountView",
    "AccountingPeriodFacts",
    "AccountingPeriodsPort",
    "ApprovalProposer",
    "ProposalReceipt",
    "Currency",
    "CurrencyMismatch",
    "Direction",
    "EntryView",
    "LedgerReaderPort",
    "LedgerService",
    "LedgerWriterPort",
    "Money",
    "OpenExceptionCounter",
    "Page",
    "PageRequest",
    "PostingLine",
    "PostingRequest",
    "PostingResult",
    "ReversalResult",
    "TopUpFacts",
    "TopUpLookup",
    "TransactionView",
    "WalletView",
]

AccountKind = Literal[
    "customer_wallet",
    "provider_clearing",
    "revenue_energy",
    "fees",
    "suspense",
    "chargeback_receivable",
]

Direction = Literal["debit", "credit"]

# The kinds a caller may post. `reversal` is not here on purpose: it is not something a caller
# asks for, it is what `reverse` produces.
PostingKind = Literal[
    "topup_received",
    "energy_purchase",
    "fee",
    "refund",
    "adjustment",
    "reconciliation_fix",
]

MANUAL_KINDS: frozenset[str] = frozenset({"adjustment", "refund", "reconciliation_fix"})


@dataclass(frozen=True, slots=True)
class AccountRef:
    """A ledger account as a caller names it, without knowing its id.

    A wallet is ``(customer_id, currency)``; a system account is ``(kind, currency)`` and, for
    ``provider_clearing``, the provider whose money sits there. Resolving the id is the ledger's
    job, so no other module ever stores or passes a ``ledger_accounts.id``.
    """

    kind: AccountKind
    currency: Currency
    customer_id: UUID | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        wallet = self.kind == "customer_wallet"
        if wallet and self.customer_id is None:
            raise ValueError("a customer_wallet reference needs a customer_id")
        if not wallet and self.customer_id is not None:
            raise ValueError(f"a {self.kind} account has no customer")
        if self.kind == "provider_clearing" and not self.provider:
            raise ValueError("a provider_clearing reference needs a provider")

    @property
    def code(self) -> str:
        """The ``ledger_accounts.code`` this reference names (the wallet code includes the id)."""
        if self.kind == "customer_wallet":
            return f"wallet.{self.customer_id}.{self.currency}"
        if self.kind == "provider_clearing":
            return f"provider_clearing.{self.provider}.{self.currency}"
        return f"{self.kind}.{self.currency}"


@dataclass(frozen=True, slots=True)
class PostingLine:
    """One side of a posting: a positive amount and the direction that gives it a sign."""

    account: AccountRef
    direction: Direction
    amount: Money

    def __post_init__(self) -> None:
        if self.direction not in ("debit", "credit"):
            raise ValueError(f"not a direction: {self.direction!r}")
        if self.amount.currency != self.account.currency:
            raise CurrencyMismatch(details={"line": self.account.code})


@dataclass(frozen=True, slots=True)
class PostingRequest:
    """A balanced set of lines the caller wants written, at most once per ``posting_key``."""

    posting_key: str
    kind: PostingKind
    lines: Sequence[PostingLine]
    effective_at: datetime
    description: str | None = None
    source_top_up_id: UUID | None = None
    approval_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.posting_key:
            raise ValueError("a posting needs a posting_key: it is the at-most-once guard")
        if self.effective_at.tzinfo is None:
            raise ValueError("effective_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class PostingResult:
    """``replayed`` is True when the key already existed and nothing new was written."""

    transaction_id: UUID
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ReversalResult:
    """A reversal, and the part of the credit the wallet no longer held (D-06)."""

    reversal_transaction_id: UUID
    shortfall: Money
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class EntryView:
    """One ``ledger_entries`` row, signed the way the API shows it: direction plus an amount."""

    id: UUID
    transaction_id: UUID
    account_id: UUID
    currency: str
    direction: Direction
    amount_minor: int
    created_at: datetime

    def amount(self) -> Money:
        return Money(amount_minor=self.amount_minor, currency=self.currency)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class TransactionView:
    """A transaction with its lines (``LedgerTransaction`` of the contract)."""

    id: UUID
    posting_key: str
    kind: str
    description: str | None
    effective_at: datetime
    source_type: str
    source_id: UUID | None
    reverses_transaction_id: UUID | None
    lines: Sequence[EntryView] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AccountView:
    """An account with the balance a reader may show (``LedgerAccount`` of the contract)."""

    id: UUID
    code: str
    kind: str
    currency: str
    customer_id: UUID | None
    balance_minor: int
    balance_as_of: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WalletView:
    """``Wallet`` of the contract: the balance, plus the top-ups still waiting for money."""

    customer_id: UUID
    ledger_account_id: UUID | None
    balance_minor: int
    currency: str
    as_of: datetime
    pending_top_ups: int = 0


@dataclass(frozen=True, slots=True)
class PageRequest:
    """Keyset pagination: ``after`` is the id of the last row the caller saw."""

    after: UUID | None = None
    limit: int = 25


@dataclass(frozen=True, slots=True)
class Page:
    """One page of a keyset walk; ``next_cursor`` is the id to pass back as ``after``."""

    items: Sequence[Any]
    next_cursor: UUID | None = None


@dataclass(frozen=True, slots=True)
class TopUpFacts:
    """What a refund needs to know about a top-up (D-06), read through :class:`TopUpLookup`."""

    id: UUID
    customer_id: UUID
    amount_minor: int
    currency: str
    status: str
    ledger_transaction_id: UUID | None


@dataclass(frozen=True, slots=True)
class ProposalReceipt:
    """A stored approval: its id, and the document the propose route answers with."""

    id: UUID
    document: Mapping[str, Any]


class ApprovalProposer(Protocol):
    """The propose half of `identity`'s maker-checker, as this module needs it.

    Only ``propose`` crosses the boundary: the decision happens on `/approvals`, where the
    executors registered by ``ledger.wiring`` run, so the ledger never holds an approval row.
    ``subject_id`` is the customer the decision concerns, which is what makes the schema's
    conflict-of-interest check possible.
    """

    def propose(
        self,
        principal: Any,
        *,
        kind: str,
        payload: Any,
        reason: str,
        subject_id: UUID | None = None,
    ) -> ProposalReceipt: ...


class TopUpLookup(Protocol):
    """The top-up facts a refund validates against; `payments` implements this in slice 04.

    Until then the wiring binds a lookup that answers ``None``, and a refund proposal is
    refused as not found. That is deliberately the honest answer for a build without payments
    rather than a fake row: an approval that cannot be executed must not be storable.
    """

    def get(self, top_up_id: UUID) -> TopUpFacts | None: ...


class OpenExceptionCounter(Protocol):
    """How many reconciliation exceptions are open in a period (slice 14 provides this).

    A period is not locked over open exceptions, and the count is also what the 409 shows the
    caller. Until `reconciliation` registers its own, the default answer is zero, so a build
    without that module can still lock a period.
    """

    def open_count(self, period_start: date, period_end: date) -> int: ...


class AccountingPeriodFacts(Protocol):
    """The columns of one ``accounting_periods`` row the use cases read.

    The repository returns a row object; the use cases look at these fields and nothing else,
    so this is the shape they name and `ledger.infrastructure` satisfies it.
    """

    id: UUID
    period_start: date
    period_end: date
    status: str
    locked_by: UUID | None
    locked_at: datetime | None


class LedgerReaderPort(Protocol):
    """The reads the use cases and the jobs make, as `ledger.infrastructure.reader` offers them.

    `ledger.application` may not name an infrastructure class (docs/03 section 1.2): the
    application talks to the shape below, and the reader is one implementation of it.
    """

    def account_id(self, ref: AccountRef) -> UUID | None: ...

    def account(self, account_id: UUID) -> AccountView | None: ...

    def balance_minor(self, account_id: UUID) -> int: ...

    def statement(
        self, account_id: UUID, *, after: UUID | None = None, limit: int = 25
    ) -> Sequence[EntryView]: ...

    def transaction(self, transaction_id: UUID) -> TransactionView | None: ...

    def period(self, period_id: UUID) -> AccountingPeriodFacts | None: ...

    def find_period_for(self, on: date) -> AccountingPeriodFacts | None: ...

    def period_is_locked(self, on: date) -> bool: ...

    def drift(self) -> Sequence[Mapping[str, Any]]: ...

    def imbalances(self) -> Sequence[Mapping[str, Any]]: ...


class LedgerWriterPort(Protocol):
    """The writes the use cases and the jobs make, as `ledger.infrastructure.writer` offers them.

    ``lines`` is a ``SignedLine`` sequence from ``ledger.domain.posting``: by the time anything
    reaches the writer the signs are decided.
    """

    def post(self, request: PostingRequest, lines: Sequence[Any]) -> PostingResult: ...

    def post_suspense(
        self,
        *,
        posting_key: str,
        provider_event_id: UUID,
        clearing_account_id: UUID,
        source_top_up_id: UUID | None,
    ) -> tuple[UUID, bool]: ...

    def reverse(
        self,
        original_transaction_id: UUID,
        *,
        posting_key: str,
        provider_event_id: UUID | None = None,
        approval_id: UUID | None = None,
    ) -> tuple[UUID, int, bool]: ...

    def ensure_wallet(self, customer_id: UUID, currency: str) -> UUID: ...

    def snapshot(self, day: date) -> int: ...


class AccountingPeriodsPort(Protocol):
    """The three period writes the lock and reopen executors make."""

    def ensure_for(self, on: date, *, new_id: UUID) -> AccountingPeriodFacts: ...

    def lock(self, period_id: UUID, *, approver_id: UUID, at: datetime) -> bool: ...

    def reopen(self, period_id: UUID) -> bool: ...


class LedgerService(Protocol):
    """What a money-moving module may ask the ledger to do (docs/03 2.5)."""

    def post(self, req: PostingRequest, *, uow: UnitOfWork) -> PostingResult: ...

    def post_suspense(
        self,
        *,
        posting_key: str,
        provider_event_id: UUID,
        clearing: AccountRef,
        source_top_up_id: UUID | None,
        uow: UnitOfWork,
    ) -> PostingResult:
        """Park a received amount the automatic path refused to allocate (D-05)."""
        ...

    def reverse(
        self,
        original_transaction_id: UUID,
        *,
        posting_key: str,
        provider_event_id: UUID | None = None,
        approval_id: UUID | None = None,
        uow: UnitOfWork,
    ) -> ReversalResult: ...

    def ensure_wallet(self, customer_id: UUID, currency: str, *, uow: UnitOfWork) -> UUID: ...

    def balance(self, account: AccountRef) -> Money: ...

    def statement(self, account: AccountRef, page: PageRequest) -> Page: ...

    def transaction(self, transaction_id: UUID) -> TransactionView: ...

    def period_is_locked(self, on: date) -> bool: ...
