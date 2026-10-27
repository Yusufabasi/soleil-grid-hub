"""The posting function's contract: system accounts, replay, and who gets row-locked.

``post_ledger_transaction`` is the only writer, so its behaviour is the ledger's behaviour:

* the fourteen system accounts exist per currency, seeded by the migration (a posting that
  names one must not fail in an environment where nobody ran a seed script);
* a replay of a ``posting_key`` returns the same transaction and writes nothing (the
  at-most-once guard, ``TC-NFR-05-2``);
* only wallets get a ``wallet_balances`` row. A posting that touches system accounts alone must
  not create one, because those balances are derived, never maintained.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from django.db import connection

from hub.ledger import wiring
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.shared.money import Money
from support.principals import SeededUser

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def _id_of(code: str) -> uuid.UUID:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM hub.ledger_accounts WHERE code = %s", [code])
        row = cursor.fetchone()
    assert row is not None, code
    return uuid.UUID(str(row[0]))


@pytest.mark.tc("TC-FR-04-3")
def test_system_accounts_exist_per_currency() -> None:
    """Every kind has its USD and CDF account, and the providers their clearing account."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT code FROM hub.ledger_accounts WHERE customer_id IS NULL")
        codes = {row[0] for row in cursor.fetchall()}

    for currency in ("USD", "CDF"):
        for kind in ("revenue_energy", "fees", "suspense", "chargeback_receivable"):
            assert f"{kind}.{currency}" in codes, (kind, currency)
        for provider in ("mpesa_cd", "airtel_cd", "orange_cd"):
            assert f"provider_clearing.{provider}.{currency}" in codes, provider


def _wallet_rows() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM hub.wallet_balances")
        return int(cursor.fetchone()[0])


def test_system_accounts_not_row_locked(
    finance_lead_maker: SeededUser, finance_lead_checker: SeededUser, approve: Any
) -> None:
    """A posting between two system accounts creates no wallet row: balances are derived."""
    fees, suspense = _id_of("fees.USD"), _id_of("suspense.USD")
    lines = [
        {"account_id": str(fees), "currency": "USD", "amount_minor": 250},
        {"account_id": str(suspense), "currency": "USD", "amount_minor": -250},
    ]
    approval = approve(
        action="ledger.adjustment",
        payload=lines,
        maker=finance_lead_maker,
        approver=finance_lead_checker,
    )
    before = _wallet_rows()
    with wiring.unit_of_work() as uow:
        result = wiring.service().post(
            PostingRequest(
                posting_key=f"system-only:{uuid.uuid4()}",
                kind="adjustment",
                lines=[
                    PostingLine(
                        account=AccountRef(kind="fees", currency="USD"),
                        direction="debit",
                        amount=Money(250, "USD"),
                    ),
                    PostingLine(
                        account=AccountRef(kind="suspense", currency="USD"),
                        direction="credit",
                        amount=Money(250, "USD"),
                    ),
                ],
                effective_at=NOW,
                approval_id=approval,
            ),
            uow=uow,
        )
    assert result.replayed is False
    assert _wallet_rows() == before


@pytest.mark.tc("TC-NFR-05-2")
def test_replay_returns_same_transaction(world) -> None:  # noqa: ANN001 - fixture
    """Posting the same key twice is one transaction, and the second call says so."""
    key = f"fee:{uuid.uuid4()}"
    request = PostingRequest(
        posting_key=key,
        kind="fee",
        lines=[
            PostingLine(account=world.wallet, direction="debit", amount=Money(100, "USD")),
            PostingLine(account=world.fees, direction="credit", amount=Money(100, "USD")),
        ],
        effective_at=NOW,
        source_top_up_id=world.top_up_id,
    )
    with wiring.unit_of_work() as uow:
        first = wiring.service().post(request, uow=uow)
    with wiring.unit_of_work() as uow:
        second = wiring.service().post(request, uow=uow)

    assert first.replayed is False
    assert second.replayed is True
    assert second.transaction_id == first.transaction_id
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM hub.ledger_transactions WHERE posting_key = %s", [key])
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT count(*) FROM hub.ledger_entries WHERE transaction_id = %s",
            [first.transaction_id],
        )
        assert cursor.fetchone()[0] == 2
