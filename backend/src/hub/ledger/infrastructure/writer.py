"""The only Python that writes the ledger (D-22, docs/04 10.7).

Nothing here builds an INSERT. Each method calls one ``SECURITY DEFINER`` function by name
through :func:`hub.shared.db.call_function`, which is what keeps the write path auditable: the
application roles hold EXECUTE on those functions and no INSERT, UPDATE or DELETE on the
ledger tables, so a mistake in this file cannot become an unbalanced entry — the worst it can
do is pass bad arguments to a function that refuses them.

The error mapping is the other half of the job. The functions raise with a message prefix
(``period_locked``, ``approval_required``, ``evidence_required``) and constraint names
(``ck_wallet_balances_no_overdraft``, ``uq_ledger_transactions_posting_key``), because a
``plpgsql`` function cannot raise our Python classes. This module is where those become
:class:`~hub.ledger.domain.errors.PeriodLocked` and friends, so a caller sees a 409 with a
stable code instead of a 500 with a stack trace.

Replay detection: ``post_ledger_transaction`` returns the existing id when the ``posting_key``
is taken, and says nothing about which call won. ``created_xid`` answers it — the row was
written by this transaction (``own``) or by an earlier one (a replay) — which is why the
column exists in the schema (S-9).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
from django.db import DatabaseError, connections
from psycopg.types.json import Jsonb

from hub.ledger.contracts import PostingRequest, PostingResult
from hub.ledger.domain.errors import (
    ApprovalRequired,
    EvidenceRequired,
    InsufficientWalletBalance,
    LedgerInvariant,
    PeriodLocked,
    UnbalancedPosting,
)
from hub.ledger.domain.posting import SignedLine
from hub.shared.db import call_function
from hub.shared.errors import DomainError, ValidationFailed

__all__ = ["LedgerWriter"]

# SQLSTATEs the ledger functions raise with; anything else is an invariant breach, not a rule.
CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
RESTRICT_VIOLATION = "23001"
INVALID_PARAMETER = "22023"
FOREIGN_KEY_VIOLATION = "23503"

NO_OVERDRAFT = "ck_wallet_balances_no_overdraft"
POSTING_KEY_UNIQUE = "uq_ledger_transactions_posting_key"


class LedgerWriter:
    """Calls the definer functions and translates what they say into domain errors."""

    def __init__(self, *, using: str = "default") -> None:
        self._using = using

    # --- writes -------------------------------------------------------------------------
    def post(self, request: PostingRequest, lines: Sequence[SignedLine]) -> PostingResult:
        """Post ``lines`` once per ``posting_key`` and say whether this call was the first."""
        payload = Jsonb([line.document() for line in lines])
        already_there = self._transaction_id_for(request.posting_key)
        try:
            row = call_function(
                "post_ledger_transaction",
                request.posting_key,
                request.kind,
                request.effective_at,
                request.description,
                request.approval_id,
                request.source_top_up_id,
                payload,
                using=self._using,
            )
        except DB_ERRORS as exc:
            raise _translate(exc) from exc
        if row is None:  # pragma: no cover - the function always returns the id
            raise LedgerInvariant(details={"function": "post_ledger_transaction"})
        transaction_id = _uuid(row[0])
        return PostingResult(
            transaction_id=transaction_id,
            replayed=already_there is not None or not self._own_transaction(transaction_id),
        )

    def reverse(
        self,
        original_transaction_id: UUID,
        *,
        posting_key: str,
        provider_event_id: UUID | None = None,
        approval_id: UUID | None = None,
    ) -> tuple[UUID, int, bool]:
        """Reverse a received top-up; returns ``(reversal_id, shortfall_minor, replayed)``."""
        already_there = self._transaction_id_for(posting_key)
        try:
            row = call_function(
                "reverse_ledger_transaction",
                original_transaction_id,
                posting_key,
                provider_event_id,
                approval_id,
                using=self._using,
            )
        except DB_ERRORS as exc:
            raise _translate(exc) from exc
        if row is None:  # pragma: no cover - the function always returns a row
            raise LedgerInvariant(details={"function": "reverse_ledger_transaction"})
        values = _flatten(row)
        reversal_id = _uuid(values[0])
        shortfall = int(values[1] or 0)
        return (
            reversal_id,
            shortfall,
            already_there is not None or not self._own_transaction(reversal_id),
        )

    def post_suspense(
        self,
        *,
        posting_key: str,
        provider_event_id: UUID,
        clearing_account_id: UUID,
        source_top_up_id: UUID | None,
    ) -> tuple[UUID, bool]:
        """Park a received amount in ``suspense``; returns ``(transaction_id, replayed)``.

        ``hub.post_suspense_receipt`` (migration 0004) is a separate function on purpose: the
        general posting refuses a ``topup_received`` whose event disagrees with the top-up, and
        a delivery that *should* disagree is exactly what lands here. The amount posted is the
        event's, because that is the only figure a mismatch can be trusted to state.
        """
        already_there = self._transaction_id_for(posting_key)
        try:
            row = call_function(
                "post_suspense_receipt",
                posting_key,
                provider_event_id,
                clearing_account_id,
                source_top_up_id,
                using=self._using,
                casts=("text", "uuid", "uuid", "uuid"),
            )
        except DB_ERRORS as exc:
            raise _translate(exc) from exc
        if row is None:  # pragma: no cover - the function always returns the id
            raise LedgerInvariant(details={"function": "post_suspense_receipt"})
        transaction_id = _uuid(row[0])
        return (
            transaction_id,
            already_there is not None or not self._own_transaction(transaction_id),
        )

    def ensure_wallet(self, customer_id: UUID, currency: str) -> UUID:
        """Create the customer's wallet for ``currency`` if it is absent; idempotent."""
        try:
            row = call_function(
                "create_wallet_account",
                customer_id,
                currency,
                using=self._using,
                # `bpchar`, not `char`: `%s::char` means `character(1)` and would truncate
                # "USD" to "U", which the currency check refuses.
                casts=("uuid", "bpchar"),
            )
        except DB_ERRORS as exc:
            raise _translate(exc) from exc
        if row is None or row[0] is None:  # pragma: no cover - the function returns the id
            raise LedgerInvariant(details={"function": "create_wallet_account"})
        return _uuid(row[0])

    def snapshot(self, day: date) -> int:
        """Materialise every system account's balance for ``day``; returns rows written."""
        try:
            row = call_function("snapshot_ledger_balances", day, using=self._using)
        except DB_ERRORS as exc:
            raise _translate(exc) from exc
        return int(row[0]) if row else 0

    # --- internals ----------------------------------------------------------------------
    def _transaction_id_for(self, posting_key: str) -> UUID | None:
        """The transaction that already holds ``posting_key``, or ``None``.

        ``created_xid`` answers "did *this* database transaction write the row?", which is the
        precise question, but a caller that posts twice inside one transaction — as a test that
        wraps the whole case in an atomic block does — sees the same xid both times. Asking
        first closes that case; a concurrent winner between the two calls is still caught by
        the xid comparison after the fact.
        """
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                "SELECT id FROM hub.ledger_transactions WHERE posting_key = %s",
                [posting_key],
            )
            row = cursor.fetchone()
        return _uuid(row[0]) if row else None

    def _own_transaction(self, transaction_id: UUID) -> bool:
        """True when this database transaction is the one that inserted the row."""
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                "SELECT created_xid = pg_current_xact_id()"
                "  FROM hub.ledger_transactions WHERE id = %s",
                [transaction_id],
            )
            row = cursor.fetchone()
        return bool(row[0]) if row else False


def _flatten(row: Sequence[Any]) -> Sequence[Any]:
    """A ``RETURNS TABLE`` function arrives as one record column, not as its fields."""
    if len(row) == 1 and isinstance(row[0], tuple):
        return row[0]
    return row


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


# A statement that fails through Django's cursor arrives as `django.db.utils.IntegrityError`
# (and friends), which is not a `psycopg.Error`; the driver's error is its `__cause__`.
DB_ERRORS = (psycopg.Error, DatabaseError)


def _driver_error(exc: BaseException) -> psycopg.Error | None:
    """The psycopg error behind a Django wrapper, or ``None`` when there is not one."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, psycopg.Error):
            return seen
        seen = seen.__cause__ if seen.__cause__ is not seen else None
    return None


def _translate(exc: BaseException) -> DomainError:
    """Map a SQLSTATE and its message prefix to the domain error the client should see."""
    original = _driver_error(exc)
    if original is None:  # pragma: no cover - a Django error with no driver error behind it
        return LedgerInvariant(details={"reason": type(exc).__name__})
    diag = getattr(original, "diag", None)
    message = (getattr(diag, "message_primary", None) or "") if diag else ""
    constraint = (getattr(diag, "constraint_name", None) or "") if diag else ""
    sqlstate = getattr(original, "sqlstate", None)

    if message.startswith("period_locked"):
        return PeriodLocked(details={"effective_at": message.partition("effective date ")[2]})
    if message.startswith("approval_required"):
        return ApprovalRequired()
    if message.startswith("evidence_required") or "no stored provider success event" in message:
        return EvidenceRequired()
    if constraint == NO_OVERDRAFT:
        return InsufficientWalletBalance()
    if message.startswith("only a received top-up can be reversed"):
        return ValidationFailed(code="validation_error", details={"reason": message})
    if message.startswith("a posting needs at least two lines"):
        return UnbalancedPosting(details={"reason": message})
    if message.startswith("source_required") or "needs a source top-up" in message:
        return ValidationFailed(code="validation_error", details={"reason": message})
    if sqlstate == CHECK_VIOLATION:
        # `assert_transaction_balanced` and the `amount_minor <> 0` check both land here.
        return UnbalancedPosting(details={"reason": message or "check_violation"})
    if sqlstate == UNIQUE_VIOLATION and constraint == POSTING_KEY_UNIQUE:
        # Unreachable through `post` (the function is ON CONFLICT DO NOTHING); kept because a
        # future caller of the bare function would otherwise see a 500 for a replay.
        return LedgerInvariant(details={"constraint": constraint, "replay": True})
    if sqlstate in (RESTRICT_VIOLATION, INVALID_PARAMETER, FOREIGN_KEY_VIOLATION):
        return ValidationFailed(
            code="validation_error", details={"reason": message or sqlstate or "rejected"}
        )
    return LedgerInvariant(
        details={"sqlstate": sqlstate, "constraint": constraint, "reason": message}
    )
