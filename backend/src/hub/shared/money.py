"""Money: an integer count of minor units plus a currency (docs/03 2.1, D-03).

One value object for the whole system, because the alternative — every module with its own
amount type — is how a float reaches a ledger. Construction validates, so an invalid amount
cannot exist: a ``bool`` is not an amount (``True`` is an ``int`` in Python), a ``float`` is
refused instead of silently truncated, the currency is one of the two the schema allows, and
the magnitude stays inside ``bigint``.

The sign convention is the ledger's, not this class's: ``Money`` is a positive amount, and a
``direction`` carries the sign (``docs/00`` invariant 3). ``__add__`` exists for tests and
reports and refuses a mixed-currency sum with :class:`CurrencyMismatch`, which is the 422
``currency_mismatch`` of the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from hub.shared.errors import ValidationFailed

__all__ = ["CURRENCIES", "MAX_AMOUNT_MINOR", "Currency", "CurrencyMismatch", "Money"]

Currency = Literal["USD", "CDF"]
CURRENCIES: Final = ("USD", "CDF")

# bigint, and a posting of 2**63 minor units would overflow the sum the balance trigger takes.
MAX_AMOUNT_MINOR: Final = 2**63


class CurrencyMismatch(ValidationFailed):
    """Two amounts in different currencies: 422 ``currency_mismatch`` (docs/05 section 5)."""

    code = "currency_mismatch"
    message_key = "currency_mismatch"


@dataclass(frozen=True, slots=True)
class Money:
    """A positive amount in one supported currency, stored as integer minor units."""

    amount_minor: int
    currency: Currency

    def __post_init__(self) -> None:
        amount = self.amount_minor
        # bool is an int subclass in Python: `Money(True, "USD")` must not become one cent.
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise TypeError(f"amount_minor must be an int, got {type(amount).__name__}")
        if amount < 0:
            raise ValueError("Money is a positive amount; direction carries the sign")
        if amount >= MAX_AMOUNT_MINOR:
            raise ValueError(f"amount_minor must be below 2**63, got {amount}")
        if self.currency not in CURRENCIES:
            raise ValueError(f"unsupported currency {self.currency!r}")

    # --- arithmetic ---------------------------------------------------------------------
    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(amount_minor=self.amount_minor + other.amount_minor, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(amount_minor=self.amount_minor - other.amount_minor, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(amount_minor=-self.amount_minor, currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount_minor <= other.amount_minor

    def __gt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount_minor > other.amount_minor

    def __ge__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount_minor >= other.amount_minor

    # --- helpers ------------------------------------------------------------------------
    @property
    def is_zero(self) -> bool:
        return self.amount_minor == 0

    def document(self) -> dict[str, object]:
        """``Money`` of the contract: the two fields, in that order."""
        return {"amount_minor": self.amount_minor, "currency": self.currency}

    def _same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(
                details={"left": self.currency, "right": other.currency},
            )
