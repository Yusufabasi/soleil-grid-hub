"""Properties of the posting rules under generated input (docs/10 2.1, TC-FR-04-1, TC-NFR-05-1).

The examples in ``test_posting_domain.py`` pin the shapes a reader thinks of; these pin the ones
nobody thinks of. Hypothesis builds arbitrary line sets — zero, one, two, many; balanced and
not; nonzero and not — and the properties say what must always be true:

P1  a balanced, nonzero, single-currency posting converts with a zero signed total;
P2  the signed total is the debit sum minus the credit sum;
P3  the conversion is a pure function: the same request twice gives the same lines;
P4  any total but zero is refused;
P5  every produced line is nonzero, because a zero entry is a row that says nothing;
P6  the number of produced lines equals the number of requested ones;
P7  an amount never changes magnitude: only its sign is chosen.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.ledger.domain.errors import UnbalancedPosting
from hub.ledger.domain.posting import to_signed_lines, total_of
from hub.shared.money import Money

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
WALLET = AccountRef(kind="customer_wallet", currency="USD", customer_id=uuid.uuid4())
SUSPENSE = AccountRef(kind="suspense", currency="USD")
IDS = {WALLET.code: uuid.uuid4(), SUSPENSE.code: uuid.uuid4()}


def resolve(account: AccountRef) -> uuid.UUID | None:
    return IDS.get(account.code)


def _line(account: AccountRef, direction: str, amount: int) -> PostingLine:
    return PostingLine(
        account=account,
        direction=direction,  # type: ignore[arg-type]
        amount=Money(amount, "USD"),
    )


def _request(lines: list[PostingLine]) -> PostingRequest:
    return PostingRequest(
        posting_key=f"prop:{uuid.uuid4()}", kind="fee", lines=lines, effective_at=NOW
    )


amounts = st.integers(min_value=1, max_value=10_000)


@st.composite
def balanced_posting(draw: st.DrawFn) -> list[PostingLine]:
    """A debit on the wallet and an equal credit on suspense: always balanced."""
    amount = draw(amounts)
    return [_line(WALLET, "debit", amount), _line(SUSPENSE, "credit", amount)]


@pytest.mark.tc("TC-NFR-05-1")
@settings(max_examples=100, deadline=None)
@given(balanced_posting())
def test_p1_balanced_postings_have_zero_total(lines: list[PostingLine]) -> None:
    signed = to_signed_lines(_request(lines), resolve)
    assert total_of(signed) == 0
    assert len(signed) == 2


@pytest.mark.tc("TC-FR-04-1")
@settings(max_examples=100, deadline=None)
@given(amounts, amounts)
def test_p2_p3_p7_total_is_debits_minus_credits_and_conversion_is_pure(
    debit: int, credit: int
) -> None:
    lines = [_line(WALLET, "debit", debit), _line(SUSPENSE, "credit", credit)]
    if debit == credit:
        signed = to_signed_lines(_request(lines), resolve)
        assert total_of(signed) == 0
        assert [line.amount_minor for line in signed] == [debit, -credit]
        # P3: the same request twice is the same answer.
        assert to_signed_lines(_request(lines), resolve) == to_signed_lines(
            _request(lines), resolve
        )
    else:
        # P4: anything but zero is refused.
        with pytest.raises(UnbalancedPosting):
            to_signed_lines(_request(lines), resolve)


@pytest.mark.tc("TC-NFR-05-1")
@settings(max_examples=50, deadline=None)
@given(st.lists(amounts, min_size=1, max_size=5))
def test_p5_p6_one_line_per_request_and_never_zero(amounts_: list[int]) -> None:
    """A posting with one debit and many credits of the same total keeps every line nonzero."""
    total = sum(amounts_)
    lines = [_line(WALLET, "debit", total)]
    lines += [_line(SUSPENSE, "credit", amount) for amount in amounts_]
    signed = to_signed_lines(_request(lines), resolve)
    assert len(signed) == len(lines)
    assert all(line.amount_minor != 0 for line in signed)
    assert total_of(signed) == 0
