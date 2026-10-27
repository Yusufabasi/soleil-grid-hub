"""The top-up state machine, mirroring ``hub.top_ups_guard`` (D-05, docs/00 7.2).

The database is the authority: the trigger refuses a transition this module would allow, and
this module refuses one the trigger would allow only by accident. Keeping both is not
duplication for its own sake — a use case has to know *before* it writes whether a confirmation
is late, and a client has to be told 409 rather than see a check violation.

Two rules are worth stating because they look like gaps:

* ``initiated`` cannot become ``confirmed`` in one step. A payment path that has not marked the
  top-up ``pending`` has not told the provider it accepted the collection, and the ledger
  function requires the stored event — so the confirmation passes through ``pending``.
* ``expired`` and ``cancelled`` still become ``confirmed``: money that arrived is money the
  customer has, and refusing it would keep cash the ledger cannot explain (D-05). The outcome
  carries ``late`` so the transition is recorded as what it is.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from hub.payments.domain.errors import InvalidTopUpState

__all__ = [
    "ALLOWED_TRANSITIONS",
    "CancelOutcome",
    "ConfirmOutcome",
    "TopUp",
    "TopUpStatus",
    "can_transition",
]

TopUpStatus = Literal[
    "initiated",
    "pending",
    "pending_unknown",
    "confirmed",
    "failed",
    "expired",
    "cancelled",
    "reversed",
]

INITIATED: Final[TopUpStatus] = "initiated"
PENDING: Final[TopUpStatus] = "pending"
PENDING_UNKNOWN: Final[TopUpStatus] = "pending_unknown"
CONFIRMED: Final[TopUpStatus] = "confirmed"
FAILED: Final[TopUpStatus] = "failed"
EXPIRED: Final[TopUpStatus] = "expired"
CANCELLED: Final[TopUpStatus] = "cancelled"
REVERSED: Final[TopUpStatus] = "reversed"

# Copied from `hub.top_ups_guard`: a pair missing here would be a state the use cases cannot
# reach, and a pair present here that the trigger refuses would be a write that always fails.
ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    INITIATED: frozenset({PENDING, FAILED, EXPIRED, CANCELLED}),
    PENDING: frozenset({PENDING_UNKNOWN, CONFIRMED, FAILED, EXPIRED, CANCELLED}),
    PENDING_UNKNOWN: frozenset({PENDING, CONFIRMED, FAILED, EXPIRED, CANCELLED}),
    EXPIRED: frozenset({CONFIRMED}),
    CANCELLED: frozenset({CONFIRMED}),
    CONFIRMED: frozenset({REVERSED}),
    FAILED: frozenset(),
    REVERSED: frozenset(),
}


def can_transition(source: str, target: str) -> bool:
    """True when the database would accept ``source -> target``."""
    return target in ALLOWED_TRANSITIONS.get(source, frozenset())


@dataclass(frozen=True, slots=True)
class ConfirmOutcome:
    """What a confirmation does to a top-up: the status chain, and whether it arrived late."""

    steps: tuple[str, ...]
    late: bool

    @property
    def status(self) -> str:
        return self.steps[-1]


@dataclass(frozen=True, slots=True)
class CancelOutcome:
    """What a cancellation does: the new status, or a no-op when it was already cancelled."""

    status: str
    changed: bool


@dataclass(frozen=True, slots=True)
class TopUp:
    """The fields the state rules read; the repository holds the full row."""

    id: UUID
    status: str
    expires_at: datetime
    late: bool = False

    def confirm(self, now: datetime) -> ConfirmOutcome:
        """The chain a confirmation walks from this status, and whether it is late.

        A top-up whose window closed is still credited — the customer paid — but the outcome
        says ``late`` so the event and the row record why.
        """
        if self.status == INITIATED:
            if not can_transition(INITIATED, PENDING):
                raise InvalidTopUpState(details={"from": self.status, "to": PENDING})
            return ConfirmOutcome(steps=(PENDING, CONFIRMED), late=now > self.expires_at)
        if self.status in (PENDING, PENDING_UNKNOWN):
            if not can_transition(self.status, CONFIRMED):
                raise InvalidTopUpState(details={"from": self.status, "to": CONFIRMED})
            # The sweeper may not have expired the row yet: the delivery still arrived after
            # the window the customer was quoted, so it is late by the clock, not by status.
            return ConfirmOutcome(steps=(CONFIRMED,), late=now > self.expires_at)
        if self.status in (EXPIRED, CANCELLED):
            if not can_transition(self.status, CONFIRMED):
                raise InvalidTopUpState(details={"from": self.status, "to": CONFIRMED})
            # `top_ups_guard` requires `late` for exactly these two, whatever the clock says.
            return ConfirmOutcome(steps=(CONFIRMED,), late=True)
        raise InvalidTopUpState(details={"from": self.status, "to": CONFIRMED})

    def cancel(self) -> CancelOutcome:
        """Cancel a top-up that is still waiting for the provider; a repeat is a no-op."""
        if self.status == CANCELLED:
            return CancelOutcome(status=CANCELLED, changed=False)
        if not can_transition(self.status, CANCELLED):
            raise InvalidTopUpState(details={"from": self.status, "to": CANCELLED})
        return CancelOutcome(status=CANCELLED, changed=True)

    def path_to(self, target: str) -> tuple[str, ...]:
        """The statuses a write must pass through to reach ``target``.

        Most transitions are one step. ``initiated`` has no edge to ``pending_unknown`` in
        ``hub.top_ups_guard``, so an initiation whose provider answer never arrived walks
        ``pending -> pending_unknown``: the same two-statement path a confirmation takes, and
        the only one the trigger accepts. The search uses :data:`ALLOWED_TRANSITIONS`, so it
        can never invent an edge the database refuses.
        """
        if can_transition(self.status, target):
            return (target,)
        for hop in ALLOWED_TRANSITIONS.get(self.status, frozenset()):
            if can_transition(hop, target):
                return (hop, target)
        raise InvalidTopUpState(details={"from": self.status, "to": target})

    def with_status(self, status: str, *, late: bool | None = None) -> TopUp:
        """This top-up after a transition; the caller never mutates a frozen value."""
        return replace(self, status=status, late=self.late if late is None else late)
