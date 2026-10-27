"""The posting rules, without a database (TC-FR-04-1).

Every claim here is one the schema also makes — and the point of testing the Python half is
that a caller learns about a bad posting *before* a write is attempted, with a 422 that names
the field rather than a 500 that names a constraint.

The resolution callable is a dict lookup: if these tests needed a database, the rules would be
in the wrong module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.ledger.domain.errors import UnbalancedPosting
from hub.ledger.domain.posting import SignedLine, to_signed_lines, total_of
from hub.shared.money import CurrencyMismatch, Money

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

WALLET = AccountRef(kind="customer_wallet", currency="USD", customer_id=uuid.uuid4())
REVENUE = AccountRef(kind="revenue_energy", currency="USD")
SUSPENSE = AccountRef(kind="suspense", currency="USD")
WALLET_CDF = AccountRef(kind="customer_wallet", currency="CDF", customer_id=WALLET.customer_id)


def resolve(account: AccountRef) -> uuid.UUID | None:
    """Every reference in this module is known except the ones a test invents."""
    return ACCOUNT_IDS.get(account.code)


ACCOUNT_IDS: dict[str, uuid.UUID] = {
    WALLET.code: uuid.uuid4(),
    REVENUE.code: uuid.uuid4(),
    SUSPENSE.code: uuid.uuid4(),
    WALLET_CDF.code: uuid.uuid4(),
}


def request(*lines: PostingLine, kind: Any = "fee") -> PostingRequest:
    return PostingRequest(
        posting_key=f"test:{uuid.uuid4()}",
        kind=kind,
        lines=list(lines),
        effective_at=NOW,
    )


@pytest.mark.tc("TC-FR-04-1")
def test_to_signed_lines_debit_positive_credit_negative() -> None:
    """A debit is positive and a credit negative: the sign convention is applied exactly once."""
    req = request(
        PostingLine(account=WALLET, direction="debit", amount=Money(500, "USD")),
        PostingLine(account=REVENUE, direction="credit", amount=Money(500, "USD")),
    )
    lines = to_signed_lines(req, resolve)

    assert [(line.account_id, line.amount_minor) for line in lines] == [
        (ACCOUNT_IDS[WALLET.code], 500),
        (ACCOUNT_IDS[REVENUE.code], -500),
    ]
    assert total_of(lines) == 0
    assert lines[0].document() == {
        "account_id": str(ACCOUNT_IDS[WALLET.code]),
        "currency": "USD",
        "amount_minor": 500,
    }


@pytest.mark.tc("TC-FR-04-1")
def test_unbalanced_raises() -> None:
    """One minor unit of difference is a refusal, not a rounding."""
    req = request(
        PostingLine(account=WALLET, direction="debit", amount=Money(500, "USD")),
        PostingLine(account=REVENUE, direction="credit", amount=Money(499, "USD")),
    )
    with pytest.raises(UnbalancedPosting) as raised:
        to_signed_lines(req, resolve)
    assert raised.value.code == "unbalanced_posting"
    assert raised.value.http_status == 422


def test_mixed_currency_raises() -> None:
    """USD and CDF never share a posting, even when each side would balance."""
    req = request(
        PostingLine(account=WALLET, direction="debit", amount=Money(500, "USD")),
        PostingLine(account=WALLET_CDF, direction="credit", amount=Money(500, "CDF")),
    )
    with pytest.raises(CurrencyMismatch) as raised:
        to_signed_lines(req, resolve)
    assert raised.value.code == "currency_mismatch"
    assert raised.value.http_status == 422


def test_zero_and_float_rejected() -> None:
    """A float is a bug and a zero line is a line that does nothing."""
    with pytest.raises(TypeError):
        Money(amount_minor=1.5, currency="USD")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money(amount_minor=True, currency="USD")

    req = request(
        PostingLine(account=WALLET, direction="debit", amount=Money(0, "USD")),
        PostingLine(account=REVENUE, direction="credit", amount=Money(0, "USD")),
    )
    with pytest.raises(UnbalancedPosting):
        to_signed_lines(req, resolve)


def test_overflow_rejected() -> None:
    """An amount at or above 2**63 cannot exist: the column is a bigint."""
    with pytest.raises(ValueError):
        Money(amount_minor=2**63, currency="USD")
    assert Money(amount_minor=2**63 - 1, currency="USD").amount_minor == 2**63 - 1


def test_single_line_rejected() -> None:
    """One leg is money appearing from nowhere."""
    req = request(PostingLine(account=WALLET, direction="credit", amount=Money(500, "USD")))
    with pytest.raises(UnbalancedPosting) as raised:
        to_signed_lines(req, resolve)
    assert raised.value.details["errors"][0]["code"] == "too_few"


def test_unknown_account_rejected() -> None:
    """A wallet nobody opened resolves to nothing, and that is a 422 about the line."""
    missing = AccountRef(kind="customer_wallet", currency="USD", customer_id=uuid.uuid4())
    req = request(
        PostingLine(account=missing, direction="debit", amount=Money(500, "USD")),
        PostingLine(account=REVENUE, direction="credit", amount=Money(500, "USD")),
    )
    with pytest.raises(UnbalancedPosting) as raised:
        to_signed_lines(req, resolve)
    assert raised.value.details["errors"][0]["code"] == "not_found"


def test_signed_line_document_matches_the_function_contract() -> None:
    """The jsonb element is what `post_ledger_transaction` reads, key for key."""
    line = SignedLine(account_id=uuid.uuid4(), currency="CDF", amount_minor=-42)
    assert line.document() == {
        "account_id": str(line.account_id),
        "currency": "CDF",
        "amount_minor": -42,
    }
