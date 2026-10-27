"""Turning a :class:`PostingRequest` into the entries the database will write.

Pure functions, no I/O: the caller supplies ``resolve``, which maps an :class:`AccountRef` to
the ``ledger_accounts.id`` it names. Keeping the resolution out of this module is what makes the
rules testable without a database, and it is also the shape ``post_ledger_transaction`` wants —
a jsonb array of ``{account_id, currency, amount_minor}``.

Two rules live here, and both are also enforced by the schema (S-9: never trust the caller):

* a posting has at least two lines, because a one-legged posting is a balance that appeared
  from nowhere;
* per currency, debits equal credits. A mixed-currency request is refused outright rather than
  balanced twice: v1 moves one currency per transaction, which is what every posting in the
  system is, and allowing more would invite a ``USD``/``CDF`` line that nets out across
  currencies while each wallet is wrong.

Signs: the caller writes positive amounts and a direction; this module is the only place the
sign convention (debit positive, credit negative, D-03) is applied.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from hub.ledger.contracts import AccountRef, PostingRequest
from hub.ledger.domain.errors import UnbalancedPosting
from hub.shared.money import MAX_AMOUNT_MINOR, CurrencyMismatch

__all__ = ["SignedLine", "to_signed_lines", "total_of"]

# Debit is positive, credit negative: the wallet balance is minus the sum of its entries.
SIGN: dict[str, int] = {"debit": 1, "credit": -1}

MINIMUM_LINES = 2


@dataclass(frozen=True, slots=True)
class SignedLine:
    """One entry as the function receives it: an account id, a currency and a signed amount."""

    account_id: UUID
    currency: str
    amount_minor: int

    def document(self) -> dict[str, Any]:
        """The element of the jsonb array ``post_ledger_transaction`` reads."""
        return {
            "account_id": str(self.account_id),
            "currency": self.currency,
            "amount_minor": self.amount_minor,
        }


def to_signed_lines(
    request: PostingRequest, resolve: Callable[[AccountRef], UUID | None]
) -> list[SignedLine]:
    """Validate ``request`` and return its entries, or raise before anything is written."""
    lines = list(request.lines)
    if len(lines) < MINIMUM_LINES:
        raise UnbalancedPosting(
            details={
                "errors": [
                    {
                        "field": "/lines",
                        "code": "too_few",
                        "message": f"A posting needs at least {MINIMUM_LINES} lines.",
                    }
                ]
            }
        )

    currencies = {line.account.currency for line in lines}
    if len(currencies) > 1:
        raise CurrencyMismatch(details={"currencies": sorted(currencies)})
    currency = currencies.pop()

    signed: list[SignedLine] = []
    total = 0
    for index, line in enumerate(lines):
        amount = line.amount.amount_minor
        if amount <= 0:
            # `Money` already refuses a negative; zero would be a line that does nothing.
            raise UnbalancedPosting(details={"errors": [_zero(index)]})
        account_id = resolve(line.account)
        if account_id is None:
            raise UnbalancedPosting(details={"errors": [_unknown(index, line.account.code)]})
        value = SIGN[line.direction] * amount
        total += value
        if abs(total) >= MAX_AMOUNT_MINOR:  # pragma: no cover - needs absurd inputs
            raise ValueError("posting amount overflows bigint")
        signed.append(SignedLine(account_id=account_id, currency=currency, amount_minor=value))

    if total != 0:
        raise UnbalancedPosting(
            details={
                "currency": currency,
                "difference_minor": total,
                "errors": [
                    {
                        "field": "/lines",
                        "code": "unbalanced",
                        "message": f"Debits and credits differ by {total} minor units.",
                    }
                ],
            }
        )
    return signed


def _zero(index: int) -> dict[str, str]:
    return {
        "field": f"/lines/{index}/amount/amount_minor",
        "code": "out_of_range",
        "message": "A ledger line must be nonzero.",
    }


def _unknown(index: int, code: str) -> dict[str, str]:
    return {
        "field": f"/lines/{index}/account",
        "code": "not_found",
        "message": f"No ledger account is named {code}.",
    }


def total_of(lines: Sequence[SignedLine]) -> int:
    """The signed sum of a set of lines; zero for anything the database accepted."""
    return sum(line.amount_minor for line in lines)
