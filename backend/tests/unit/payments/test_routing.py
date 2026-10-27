"""Which way a confirmation goes (Task 04.8).

The routing is a pure function, so these tests are about the *order* rather than about HTTP: a
delivery can be wrong in several ways at once, and the reason stored must be the first one an
operator has to deal with. Each reason is pinned on its own, then the priority is pinned as a
sequence, because a reordering that still routes every single fault correctly would still be a
real change to what a person sees at 03:00.

`late` is deliberately absent: it is a property of the *row* after a credit, not a route (D-05).
The expired and cancelled statuses therefore assert that they credit, which is the half of the
rule this function owns.
"""

from __future__ import annotations

from typing import Any

import pytest

from hub.payments.domain.routing import (
    CREDIT,
    SUSPENSE_REASONS,
    PullOutcome,
    classify_confirmation,
    exception_kind,
    suspense,
)

# A delivery that agrees with the top-up in every respect; each test changes one fact. The
# keywords are one flat mapping so a test overrides exactly the fact it is about, and the
# mapping is typed loosely because `classify_confirmation`'s keywords are not one type.
MATCHING: dict[str, Any] = {
    "status": "pending",
    "top_up_amount_minor": 500,
    "top_up_currency": "USD",
    "event_amount_minor": 500,
    "event_currency": "USD",
    "payer_ok": True,
}

AGREEING_PULL = PullOutcome(status="successful", amount_minor=500, currency="USD")


def route(**changes: Any) -> Any:
    facts: dict[str, Any] = {**MATCHING, **changes}
    return classify_confirmation(**facts)


# --- the credit -----------------------------------------------------------------------------


def test_credit_when_everything_matches() -> None:
    assert route() is CREDIT


def test_late_is_credit_not_suspense() -> None:
    """An expired or cancelled top-up is credited; the row records `late` (D-05)."""
    assert route(status="expired") is CREDIT
    assert route(status="cancelled") is CREDIT
    assert route(status="initiated") is CREDIT
    assert route(status="pending_unknown") is CREDIT


def test_an_agreeing_pull_does_not_change_the_route() -> None:
    assert route(pull=AGREEING_PULL) is CREDIT


# --- one test per reason, in priority order --------------------------------------------------


def test_erased_customer_is_suspense() -> None:
    assert route(eligibility="erased_customer").reason == "erased_customer"


def test_decommissioned_meter_is_suspense() -> None:
    assert route(eligibility="meter_decommissioned").reason == "meter_decommissioned"


def test_meter_not_owned_is_suspense() -> None:
    assert route(eligibility="meter_not_owned").reason == "meter_not_owned"


def test_currency_mismatch_is_suspense() -> None:
    assert route(event_currency="CDF").reason == "currency_mismatch"


def test_amount_mismatch_is_suspense() -> None:
    assert route(event_amount_minor=600).reason == "amount_mismatch"


def test_payer_mismatch_is_suspense() -> None:
    assert route(payer_ok=False).reason == "payer_mismatch"


def test_pull_disagrees_is_suspense() -> None:
    disagreeing = PullOutcome(status="successful", amount_minor=600, currency="USD")
    assert route(pull=disagreeing).reason == "pull_disagrees"


def test_a_pull_without_an_amount_disagrees() -> None:
    """`not_found` has no amount to compare, and crediting it would credit a payment nobody sees."""
    assert route(pull=PullOutcome(status="not_found")).reason == "pull_disagrees"


def test_already_confirmed_is_suspense() -> None:
    """A second, distinct charge against settled money is parked, never credited twice."""
    assert route(status="confirmed").reason == "already_confirmed"


def test_top_up_failed_state_is_suspense() -> None:
    assert route(status="failed").reason == "top_up_failed_state"
    assert route(status="reversed").reason == "top_up_failed_state"


# --- priority --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        # A customer who was erased *and* paid the wrong amount: erasure is the blocker.
        ({"eligibility": "erased_customer", "event_amount_minor": 600}, "erased_customer"),
        # Decommissioned beats every money disagreement: there is nowhere to credit it.
        (
            {"eligibility": "meter_decommissioned", "event_currency": "CDF"},
            "meter_decommissioned",
        ),
        ({"event_currency": "CDF", "event_amount_minor": 600}, "currency_mismatch"),
        # The payer check precedes the pull: a pull was made *because* the payer disagreed,
        # so reporting the pull would hide the reason the call happened.
        (
            {
                "payer_ok": False,
                "pull": PullOutcome(status="successful", amount_minor=500, currency="USD"),
            },
            "payer_mismatch",
        ),
        # The pull precedes the status: a settled row whose pull disagrees is re-examined.
        (
            {
                "status": "confirmed",
                "pull": PullOutcome(status="successful", amount_minor=600, currency="USD"),
            },
            "pull_disagrees",
        ),
    ],
)
def test_reason_priority_is_the_documented_order(changes: dict[str, object], expected: str) -> None:
    assert route(**changes).reason == expected


def test_every_reason_is_reachable() -> None:
    """A reason in the catalogue no route can produce is a reason nobody can ever resolve."""
    produced = {
        route(eligibility="erased_customer").reason,
        route(eligibility="meter_decommissioned").reason,
        route(eligibility="meter_not_owned").reason,
        route(event_currency="CDF").reason,
        route(event_amount_minor=600).reason,
        route(payer_ok=False).reason,
        route(pull=PullOutcome(status="not_found")).reason,
        route(status="confirmed").reason,
        route(status="failed").reason,
    }
    assert produced == set(SUSPENSE_REASONS)


# --- the small pieces ------------------------------------------------------------------------


def test_suspense_refuses_a_reason_outside_the_catalogue() -> None:
    with pytest.raises(ValueError):
        suspense("something_else")


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("amount_mismatch", "amount_mismatch"),
        ("currency_mismatch", "currency_mismatch"),
        ("payer_mismatch", "statement_only"),
        ("pull_disagrees", "statement_only"),
        ("already_confirmed", "statement_only"),
        ("erased_customer", "statement_only"),
    ],
)
def test_exception_kind_groups_the_money_differences(reason: str, kind: str) -> None:
    assert exception_kind(reason) == kind


def test_pull_outcome_requires_an_explicit_success() -> None:
    assert AGREEING_PULL.agrees_with(amount_minor=500, currency="USD")
    assert not PullOutcome(status="pending", amount_minor=500, currency="USD").agrees_with(
        amount_minor=500, currency="USD"
    )
    assert not PullOutcome(status="successful", amount_minor=None).agrees_with(
        amount_minor=500, currency="USD"
    )
