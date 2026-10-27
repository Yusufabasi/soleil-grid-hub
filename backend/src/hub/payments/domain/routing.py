"""Which way a confirmation goes: credit the wallet, or park the money in suspense (D-05, D-09).

The route is a pure decision, so it is a pure function. Everything it needs is gathered by the
use case — the stored row, the verified event, the payer comparison, the registry's verdict and
(rarely) what the provider's status API said — and the result is a value the use case acts on.
Keeping it here means the priority order below is testable without a database, and it means the
order is written *once*: a mismatch is a mismatch whether it was noticed at delivery time or by
a replay hours later.

**Priority matters.** A delivery can be wrong in several ways at once — an erased customer who
also paid a different amount — and only one reason is stored. The order runs from "the hub
cannot hold this money" to "the money arrived for a top-up that is already settled", so the
reason recorded is the first one a human has to deal with.

**`late` is not a route.** A confirmation after the window is still a credit (D-05): the customer
paid, and refusing would keep cash the ledger cannot explain. The row records `late`; the route
does not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

__all__ = [
    "CREDIT",
    "PULL_AGREES",
    "PullOutcome",
    "Route",
    "SUSPENSE_REASONS",
    "classify_confirmation",
    "exception_kind",
    "suspense",
]

RouteKind = Literal["credit", "suspense"]

# The reasons in the order they are evaluated (slice 04, Task 04.8). The first that applies is
# the one stored, because a person can only resolve one at a time.
SUSPENSE_REASONS: Final = (
    "erased_customer",
    "meter_decommissioned",
    "meter_not_owned",
    "currency_mismatch",
    "amount_mismatch",
    "payer_mismatch",
    "pull_disagrees",
    "already_confirmed",
    "top_up_failed_state",
)

# The reasons a reconciliation item is categorised as. Everything else is `statement_only`: the
# money is parked and a human checks a statement rather than a difference.
_AMOUNT_REASONS = frozenset({"amount_mismatch"})
_CURRENCY_REASONS = frozenset({"currency_mismatch"})


@dataclass(frozen=True, slots=True)
class Route:
    """Where a confirmation goes, and why when it is not a credit."""

    kind: RouteKind
    reason: str | None = None

    @property
    def is_credit(self) -> bool:
        return self.kind == "credit"


CREDIT: Final = Route(kind="credit")
PULL_AGREES: Final = "successful"


def suspense(reason: str) -> Route:
    """A route to ``suspense``; an unknown reason is a programming error, not a silent credit."""
    if reason not in SUSPENSE_REASONS:
        raise ValueError(f"not a suspense reason: {reason!r}")
    return Route(kind="suspense", reason=reason)


@dataclass(frozen=True, slots=True)
class PullOutcome:
    """What the provider's status API said, flattened to the three facts that decide a route.

    ``amount_minor`` and ``currency`` are ``None`` when the provider answered with a status only
    — a ``not_found`` has no amount to compare, and treating that as agreement would credit a
    payment nobody can find.
    """

    status: str
    amount_minor: int | None = None
    currency: str | None = None

    def agrees_with(self, *, amount_minor: int, currency: str) -> bool:
        """True only for an explicit success of the same amount in the same currency."""
        return (
            self.status == PULL_AGREES
            and self.amount_minor == amount_minor
            and self.currency == currency
        )


def classify_confirmation(
    *,
    status: str,
    top_up_amount_minor: int,
    top_up_currency: str,
    event_amount_minor: int,
    event_currency: str,
    payer_ok: bool,
    eligibility: str | None = None,
    pull: PullOutcome | None = None,
) -> Route:
    """The route and reason for one confirmation, in the documented priority order."""
    if eligibility in SUSPENSE_REASONS:
        return suspense(eligibility)
    if event_currency != top_up_currency:
        return suspense("currency_mismatch")
    if event_amount_minor != top_up_amount_minor:
        return suspense("amount_mismatch")
    if not payer_ok:
        return suspense("payer_mismatch")
    if pull is not None and not pull.agrees_with(
        amount_minor=event_amount_minor, currency=event_currency
    ):
        return suspense("pull_disagrees")
    if status == "confirmed":
        # A second, distinct charge against a top-up that already has its money. The first
        # credit is not undone; the second payment is parked until somebody decides whose it is.
        return suspense("already_confirmed")
    if status in ("failed", "reversed"):
        # `top_ups_guard` has no edge to `confirmed` from either status, and rightly: the hub
        # told the customer this money did not move (`failed`) or gave it back (`reversed`).
        return suspense("top_up_failed_state")
    return CREDIT


def exception_kind(reason: str) -> str:
    """The ``reconciliation.exception`` kind for a suspense reason."""
    if reason in _AMOUNT_REASONS:
        return "amount_mismatch"
    if reason in _CURRENCY_REASONS:
        return "currency_mismatch"
    return "statement_only"
