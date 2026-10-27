"""The top-up state machine, pair by pair against ``hub.top_ups_guard`` (D-05).

The table below is transcribed from the trigger, not from ``ALLOWED_TRANSITIONS``: a test that
read the same dict it was checking would pass whatever the dict said. Every one of the 64 pairs
is asserted, because the pairs that are *absent* are the interesting ones — a state the use
cases cannot reach, or a transition the database would refuse after Python allowed it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from hub.payments.domain.errors import InvalidTopUpState
from hub.payments.domain.top_up import TopUp, can_transition

STATUSES = (
    "initiated",
    "pending",
    "pending_unknown",
    "confirmed",
    "failed",
    "expired",
    "cancelled",
    "reversed",
)

# `hub.top_ups_guard`, verbatim:
#   initiated       -> pending, failed, expired, cancelled
#   pending         -> pending_unknown, confirmed, failed, expired, cancelled
#   pending_unknown -> pending, confirmed, failed, expired, cancelled
#   expired         -> confirmed
#   cancelled       -> confirmed
#   confirmed       -> reversed
GUARD = {
    "initiated": {"pending", "failed", "expired", "cancelled"},
    "pending": {"pending_unknown", "confirmed", "failed", "expired", "cancelled"},
    "pending_unknown": {"pending", "confirmed", "failed", "expired", "cancelled"},
    "expired": {"confirmed"},
    "cancelled": {"confirmed"},
    "confirmed": {"reversed"},
    "failed": set(),
    "reversed": set(),
}

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def test_allowed_transitions_match_database_guard() -> None:
    """All 64 pairs agree with the trigger, both ways."""
    disagreements = [
        (source, target)
        for source in STATUSES
        for target in STATUSES
        if can_transition(source, target) != (target in GUARD[source])
    ]
    assert disagreements == []


def _top_up(status: str, *, expires_at: datetime = NOW + timedelta(minutes=15)) -> TopUp:
    return TopUp(id=uuid.uuid4(), status=status, expires_at=expires_at)


def test_confirm_after_expiry_sets_late() -> None:
    """A confirmation past the window is still credited, and says so."""
    expired = _top_up("expired")
    assert expired.confirm(NOW).late is True

    late_arrival = _top_up("pending", expires_at=NOW - timedelta(seconds=1))
    outcome = late_arrival.confirm(NOW)
    assert outcome.steps == ("confirmed",)
    assert outcome.late is True


def test_confirm_from_initiated_passes_through_pending() -> None:
    """No jump from initiated to confirmed: the provider has not been told yet."""
    outcome = _top_up("initiated").confirm(NOW)
    assert outcome.steps == ("pending", "confirmed")
    assert outcome.status == "confirmed"
    assert outcome.late is False


def test_confirmed_after_cancel_is_late() -> None:
    """Money that arrived after a cancellation is the customer's; the row records why."""
    outcome = _top_up("cancelled").confirm(NOW)
    assert outcome.steps == ("confirmed",)
    assert outcome.late is True


def test_failed_cannot_confirm() -> None:
    with pytest.raises(InvalidTopUpState) as raised:
        _top_up("failed").confirm(NOW)
    assert raised.value.http_status == 409
    assert raised.value.code == "invalid_state_transition"


def test_confirmed_or_reversed_cannot_confirm_again() -> None:
    for status in ("confirmed", "reversed"):
        with pytest.raises(InvalidTopUpState):
            _top_up(status).confirm(NOW)


def test_cancel_from_waiting_states_and_repeat_is_noop() -> None:
    for status in ("initiated", "pending", "pending_unknown"):
        outcome = _top_up(status).cancel()
        assert outcome.changed is True
        assert outcome.status == "cancelled"

    again = _top_up("cancelled").cancel()
    assert again.changed is False
    assert again.status == "cancelled"


def test_cancel_confirmed_is_invalid() -> None:
    for status in ("confirmed", "reversed", "failed", "expired"):
        with pytest.raises(InvalidTopUpState):
            _top_up(status).cancel()
