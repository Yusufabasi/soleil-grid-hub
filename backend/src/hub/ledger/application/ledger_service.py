"""``LedgerService``: the one door a posting goes through (docs/03 2.5).

The service owns three things the writer and the reader do not:

* **resolution.** A caller names an account by ``AccountRef``; this is where that becomes an
  id, so no other module stores one.
* **the event.** ``ledger.posted`` is staged in the caller's unit of work, next to the posting,
  so the outbox row and the transaction commit together or not at all.
* **the warning.** After a posting that touches a wallet, a balance below the configured
  threshold stages ``wallet.low_balance``. It is not an error: the debit went through, and the
  customer should hear about it before the meter refuses the next one.

Reversals are the database's: ``reverse_ledger_transaction`` decides how much the wallet still
holds and posts any shortfall to ``chargeback_receivable``. Building those lines in Python
would mean two implementations of D-06 that could disagree, and the one that runs in a
``SECURITY DEFINER`` function is the one the invariant tests check.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from hub.ledger.contracts import (
    AccountRef,
    LedgerReaderPort,
    LedgerWriterPort,
    Page,
    PageRequest,
    PostingRequest,
    PostingResult,
    ReversalResult,
    TransactionView,
)
from hub.ledger.domain.errors import LedgerNotFound
from hub.ledger.domain.posting import SignedLine, to_signed_lines
from hub.ledger.events import ledger_posted, wallet_low_balance
from hub.shared.contracts import UnitOfWork
from hub.shared.money import Money
from hub.shared.ports import Clock, IdGenerator

__all__ = ["LedgerServiceImpl"]


class LedgerServiceImpl:
    """Post, reverse, resolve and read, with the container's clock and ids."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        writer: LedgerWriterPort,
        reader: LedgerReaderPort,
        low_balance_minor: int,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._writer = writer
        self._reader = reader
        self._low_balance_minor = low_balance_minor

    # --- writes -------------------------------------------------------------------------
    def post(self, req: PostingRequest, *, uow: UnitOfWork) -> PostingResult:
        """Post ``req`` once per ``posting_key`` and announce it."""
        signed = to_signed_lines(req, self._reader.account_id)
        result = self._writer.post(req, signed)
        if result.replayed:
            # A replay changed nothing, so it is not news: emitting again would double the
            # downstream effect the key exists to prevent.
            return result
        now = self._clock.now()
        uow.outbox.add(
            ledger_posted(
                ids=self._ids,
                occurred_at=now,
                transaction_id=result.transaction_id,
                kind=req.kind,
                posting_key=req.posting_key,
                effective_at=req.effective_at,
            )
        )
        self._warn_low_balance(uow, req, signed, now)
        return result

    def post_suspense(
        self,
        *,
        posting_key: str,
        provider_event_id: UUID,
        clearing: AccountRef,
        source_top_up_id: UUID | None,
        uow: UnitOfWork,
    ) -> PostingResult:
        """Park a received amount the automatic path refused to allocate (D-05).

        The entries are ``(debit provider_clearing, credit suspense)`` for the event's amount:
        the money arrived and the ledger has to say where it is, even when nothing decided
        whose it is. No wallet is touched, so the customer's balance is untouched until a human
        resolves the exception.
        """
        account_id = self._reader.account_id(clearing)
        if account_id is None:
            raise LedgerNotFound(details={"account": clearing.code})
        transaction_id, replayed = self._writer.post_suspense(
            posting_key=posting_key,
            provider_event_id=provider_event_id,
            clearing_account_id=account_id,
            source_top_up_id=source_top_up_id,
        )
        if not replayed:
            uow.outbox.add(
                ledger_posted(
                    ids=self._ids,
                    occurred_at=self._clock.now(),
                    transaction_id=transaction_id,
                    kind="topup_received",
                    posting_key=posting_key,
                    effective_at=self._clock.now(),
                )
            )
        return PostingResult(transaction_id=transaction_id, replayed=replayed)

    def reverse(
        self,
        original_transaction_id: UUID,
        *,
        posting_key: str,
        provider_event_id: UUID | None = None,
        approval_id: UUID | None = None,
        uow: UnitOfWork,
    ) -> ReversalResult:
        """Reverse a received top-up, at most once, with the shortfall rule of D-06."""
        original = self._reader.transaction(original_transaction_id)
        if original is None:
            raise LedgerNotFound()
        currency = _currency_of(original)
        reversal_id, shortfall, replayed = self._writer.reverse(
            original_transaction_id,
            posting_key=posting_key,
            provider_event_id=provider_event_id,
            approval_id=approval_id,
        )
        if not replayed:
            uow.outbox.add(
                ledger_posted(
                    ids=self._ids,
                    occurred_at=self._clock.now(),
                    transaction_id=reversal_id,
                    kind="reversal",
                    posting_key=posting_key,
                    effective_at=self._clock.now(),
                )
            )
        return ReversalResult(
            reversal_transaction_id=reversal_id,
            shortfall=Money(amount_minor=shortfall, currency=currency),  # type: ignore[arg-type]
            replayed=replayed,
        )

    def ensure_wallet(self, customer_id: UUID, currency: str, *, uow: UnitOfWork) -> UUID:
        """Create the customer's wallet for ``currency`` if absent; safe to call repeatedly."""
        del uow  # the function is idempotent on its own; no event is news here
        return self._writer.ensure_wallet(customer_id, currency)

    # --- reads --------------------------------------------------------------------------
    def balance(self, account: AccountRef) -> Money:
        account_id = self._reader.account_id(account)
        if account_id is None:
            return Money(amount_minor=0, currency=account.currency)
        return Money(
            amount_minor=abs(self._reader.balance_minor(account_id)),
            currency=account.currency,
        )

    def statement(self, account: AccountRef, page: PageRequest) -> Page:
        account_id = self._reader.account_id(account)
        if account_id is None:
            return Page(items=(), next_cursor=None)
        rows = self._reader.statement(account_id, after=page.after, limit=page.limit + 1)
        return Page(
            items=rows[: page.limit],
            next_cursor=rows[page.limit].id if len(rows) > page.limit else None,
        )

    def transaction(self, transaction_id: UUID) -> TransactionView:
        found = self._reader.transaction(transaction_id)
        if found is None:
            raise LedgerNotFound()
        return found

    def period_is_locked(self, on: date) -> bool:
        return self._reader.period_is_locked(on)

    # --- internals ----------------------------------------------------------------------
    def _warn_low_balance(
        self,
        uow: UnitOfWork,
        req: PostingRequest,
        signed: list[SignedLine],
        now: datetime,
    ) -> None:
        """Stage one warning per wallet that ended below the threshold."""
        # ``SignedLine`` keeps the request's order, so the zip pairs each line with the account
        # id its ``AccountRef`` resolved to — the caller never knew that id.
        wallets = {
            line.account.customer_id: resolved.account_id
            for line, resolved in zip(req.lines, signed, strict=True)
            if line.account.kind == "customer_wallet" and line.account.customer_id is not None
        }
        for customer_id, account_id in wallets.items():
            balance = self._reader.balance_minor(account_id)
            if balance >= self._low_balance_minor:
                continue
            uow.outbox.add(
                wallet_low_balance(
                    ids=self._ids,
                    occurred_at=now,
                    customer_id=customer_id,
                    account_id=account_id,
                    currency=_currency_of_lines(req),
                    balance_minor=balance,
                )
            )


def _currency_of(transaction: TransactionView) -> str:
    return transaction.lines[0].currency if transaction.lines else "USD"


def _currency_of_lines(req: PostingRequest) -> str:
    return req.lines[0].account.currency if req.lines else "USD"
