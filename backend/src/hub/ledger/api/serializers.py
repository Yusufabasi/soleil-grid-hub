"""Request validators and documents of the ledger operations (docs/03 7.7, D-03).

Two rules shape this module:

* **Money crosses the wire as ``{amount_minor, currency}``** with a positive amount, and the
  *direction* carries the sign. The database stores the signed amount; converting is this
  module's job, so a client never has to know that a credit is negative in storage.
* **A cursor is opaque.** Accounts and statements page by id (UUIDv7, so time ordered);
  transactions page by ``(effective_at, id)`` because finance reads them by accounting date.
  Both are base64url over those fields, and a cursor that does not decode is a 400 rather than
  a silently ignored page boundary.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Any
from uuid import UUID

from rest_framework import serializers

from hub.ledger.contracts import AccountView, EntryView, TransactionView, WalletView
from hub.shared.errors import InvalidCursor

__all__ = [
    "AdjustmentCreateInput",
    "PeriodLockInput",
    "RefundCreateInput",
    "account_document",
    "decode_transaction_cursor",
    "decode_uuid_cursor",
    "encode_transaction_cursor",
    "encode_uuid_cursor",
    "entry_document",
    "money_document",
    "transaction_document",
    "wallet_document",
]

CURRENCIES = ("USD", "CDF")


class MoneyInput(serializers.Serializer[dict[str, Any]]):
    """``Money`` of the contract: integer minor units plus a currency, always positive."""

    amount_minor = serializers.IntegerField(min_value=1, max_value=2**53 - 1)
    currency = serializers.ChoiceField(choices=CURRENCIES)


class AdjustmentCreateInput(serializers.Serializer[dict[str, Any]]):
    """``AdjustmentCreate``: a direction and an amount; ledger lines are built server-side."""

    customer_id = serializers.UUIDField()
    direction = serializers.ChoiceField(choices=("credit_wallet", "debit_wallet"))
    amount = MoneyInput()
    reason = serializers.CharField(min_length=10, max_length=1000)
    ticket_id = serializers.UUIDField(required=False, allow_null=True)


class RefundCreateInput(serializers.Serializer[dict[str, Any]]):
    """``RefundCreate``: which top-up, how much (all of it), and why."""

    top_up_id = serializers.UUIDField()
    amount = MoneyInput()
    reason = serializers.CharField(min_length=10, max_length=1000)


class PeriodLockInput(serializers.Serializer[dict[str, Any]]):
    """``PeriodLock``: a note that lands in the audit record and the approval."""

    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


# --- documents ------------------------------------------------------------------------------


def money_document(amount_minor: int, currency: str) -> dict[str, Any]:
    """``Money`` with a positive amount; the direction, where there is one, is a sibling."""
    return {"amount_minor": int(amount_minor), "currency": currency}


def entry_document(entry: EntryView) -> dict[str, Any]:
    """``LedgerLine``: the stored sign becomes a direction plus a positive amount."""
    return {
        "account_id": str(entry.account_id),
        "direction": entry.direction,
        "amount": money_document(entry.amount_minor, entry.currency),
    }


def transaction_document(transaction: TransactionView) -> dict[str, Any]:
    """``LedgerTransaction``, with every line and the source it came from."""
    return {
        "id": str(transaction.id),
        "posting_key": transaction.posting_key,
        "kind": transaction.kind,
        "lines": [entry_document(line) for line in transaction.lines],
        "source_type": transaction.source_type,
        "source_id": str(transaction.source_id) if transaction.source_id is not None else None,
        "description": transaction.description,
        "reverses_transaction_id": (
            str(transaction.reverses_transaction_id)
            if transaction.reverses_transaction_id is not None
            else None
        ),
        "effective_at": _instant(transaction.effective_at),
    }


def account_document(account: AccountView) -> dict[str, Any]:
    """``LedgerAccount``: the signed balance becomes a positive one with a direction."""

    return {
        "id": str(account.id),
        "code": account.code,
        "type": account.kind,
        "currency": account.currency,
        "owner_customer_id": (
            str(account.customer_id) if account.customer_id is not None else None
        ),
        "balance": money_document(abs(account.balance_minor), account.currency),
        "balance_as_of": _instant(account.balance_as_of),
        "created_at": _instant(account.created_at),
    }


def wallet_document(wallet: WalletView) -> dict[str, Any]:
    """``Wallet`` of the contract: the customer's credit balance, in minor units."""
    return {
        "customer_id": str(wallet.customer_id),
        "balance": money_document(wallet.balance_minor, wallet.currency),
        "as_of": _instant(wallet.as_of),
        "pending_top_ups": wallet.pending_top_ups,
        "ledger_account_id": (
            str(wallet.ledger_account_id) if wallet.ledger_account_id is not None else None
        ),
    }


def _instant(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


# --- cursors --------------------------------------------------------------------------------


def encode_uuid_cursor(value: UUID | None) -> str | None:
    """A plain id cursor: one UUID, opaque to the client."""
    if value is None:
        return None
    return base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")


def decode_uuid_cursor(cursor: str | None) -> UUID | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        return UUID(raw)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise InvalidCursor() from exc


def encode_transaction_cursor(effective_at: datetime, transaction_id: UUID) -> str:
    """``effective_at|id``: the keyset the transactions list orders by."""
    raw = f"{effective_at.isoformat()}|{transaction_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_transaction_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        instant, _, raw_id = raw.partition("|")
        return datetime.fromisoformat(instant), UUID(raw_id)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise InvalidCursor() from exc
