"""Reversals: what the wallet still holds, and who carries the shortfall (D-06, TC-FR-04-1).

The rules the schema implements and this file proves:

* a reversal gives back the credit the wallet still holds and posts the rest — energy already
  issued, which cannot come back — to ``chargeback_receivable``;
* it happens at most once per transaction, and a second call is a replay, not a second posting;
* it needs evidence (a stored ``payment.reversed``) or an approved refund, never just a caller
  who says so;
* only a received top-up is reversible: an energy purchase is not money that can go back.

None of these lines are built in Python. ``reverse_ledger_transaction`` decides them, which is
why the assertions are about rows and balances rather than about a list the test also produced.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from django.db import connection

from hub.ledger import wiring
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.ledger.domain.errors import EvidenceRequired
from hub.shared.errors import ValidationFailed
from hub.shared.money import Money

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def _reverse(world: Any, *, provider_event_id: Any = None, key: str | None = None) -> Any:
    with wiring.unit_of_work() as uow:
        return wiring.service().reverse(
            world.top_up_transaction_id,
            posting_key=key or f"reversal:{uuid.uuid4()}",
            provider_event_id=provider_event_id,
            uow=uow,
        )


def _spend(world: Any, amount_minor: int) -> Any:
    with wiring.unit_of_work() as uow:
        return wiring.service().post(
            PostingRequest(
                posting_key=f"token:{uuid.uuid4()}:purchase",
                kind="energy_purchase",
                lines=[
                    PostingLine(
                        account=world.wallet, direction="debit", amount=Money(amount_minor, "USD")
                    ),
                    PostingLine(
                        account=AccountRef(kind="revenue_energy", currency="USD"),
                        direction="credit",
                        amount=Money(amount_minor, "USD"),
                    ),
                ],
                effective_at=NOW,
                source_top_up_id=world.top_up_id,
            ),
            uow=uow,
        )


def _entries(account_code: str) -> int:
    """The signed sum of one account's entries: what a statement would fold to."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT coalesce(sum(e.amount_minor), 0) FROM hub.ledger_entries e"
            "  JOIN hub.ledger_accounts a ON a.id = e.account_id WHERE a.code = %s",
            [account_code],
        )
        return int(cursor.fetchone()[0])


@pytest.mark.tc("TC-FR-04-1")
def test_reverse_full_when_wallet_holds_credit(world, reversal_event: Any) -> None:  # noqa: ANN001
    """Nothing was spent: the wallet goes back to zero and both transactions remain."""
    event = reversal_event(world, amount_minor=100_000)
    result = _reverse(world, provider_event_id=event)

    assert result.shortfall.amount_minor == 0
    assert world.balance() == 0
    assert _entries("provider_clearing.mpesa_cd.USD") == 0
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM hub.ledger_transactions"
            " WHERE id = %s OR reverses_transaction_id = %s",
            [world.top_up_transaction_id, world.top_up_transaction_id],
        )
        assert cursor.fetchone()[0] == 2, "the original and its reversal, both immutable"


@pytest.mark.tc("TC-FR-04-1")
def test_reverse_with_shortfall_posts_chargeback(world, reversal_event: Any) -> None:  # noqa: ANN001
    """300 of the 1,000 was already spent: the wallet returns 700, 300 becomes receivable."""
    _spend(world, 30_000)
    assert world.balance() == 70_000

    event = reversal_event(world, amount_minor=100_000)
    result = _reverse(world, provider_event_id=event)

    assert result.shortfall.amount_minor == 30_000
    assert world.balance() == 0
    assert _entries("chargeback_receivable.USD") == 30_000
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT payload FROM hub.outbox WHERE event_type = 'ledger.chargeback_opened'"
        )
        row = cursor.fetchone()
    assert row is not None, "the shortfall opens an exception for reconciliation"
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    assert payload["shortfall_minor"] == 30_000
    assert payload["original_transaction_id"] == str(world.top_up_transaction_id)


@pytest.mark.tc("TC-NFR-05-2")
def test_reverse_is_at_most_once(world, reversal_event: Any) -> None:  # noqa: ANN001
    """The second call is a replay: no new transaction, no second credit."""
    event = reversal_event(world, amount_minor=100_000)
    key = f"reversal:{uuid.uuid4()}"
    first = _reverse(world, provider_event_id=event, key=key)
    second = _reverse(world, provider_event_id=event, key=key)

    assert first.replayed is False
    assert second.replayed is True
    assert second.reversal_transaction_id == first.reversal_transaction_id
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM hub.ledger_transactions WHERE reverses_transaction_id = %s",
            [world.top_up_transaction_id],
        )
        assert cursor.fetchone()[0] == 1


def test_reverse_requires_evidence_or_approval(world) -> None:  # noqa: ANN001
    """Neither a provider event nor an approval: refused with 422 evidence_required."""
    with pytest.raises(EvidenceRequired) as raised:
        _reverse(world)
    assert raised.value.http_status == 422


def test_only_received_topups_reversible(world) -> None:  # noqa: ANN001
    """An energy purchase is not a credit that can go back."""
    purchase = _spend(world, 10_000)
    with pytest.raises(ValidationFailed):
        with wiring.unit_of_work() as uow:
            wiring.service().reverse(
                purchase.transaction_id,
                posting_key=f"reversal:{uuid.uuid4()}",
                provider_event_id=None,
                uow=uow,
            )
