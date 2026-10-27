"""A stateful machine over the posting API (TC-FR-04-2, docs/10 2.1).

Examples prove the shapes a person thought of; a state machine proves the ones a sequence of
operations produces. Three rules — spend, fee, replay — interleave in arbitrary order, and two
invariants must hold after every step:

* **the books balance**: the sum of every entry per currency is zero, always;
* **the wallet equals its fold**: the maintained balance is minus the sum of the wallet's
  entries, which is the definition D-03 gives and the trigger maintains.

A replay is included because the at-most-once guard is a rule of the *sequence*, not of a single
call: posting the same key twice in a row must not change anything, including the balance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from django.db import connection
from hypothesis import settings as hypothesis_settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test

from hub.ledger import wiring
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.ledger.domain.errors import InsufficientWalletBalance
from hub.shared.money import Money

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def _sum_by_currency() -> list[tuple[str, int]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT currency, sum(amount_minor) FROM hub.ledger_entries"
            " GROUP BY currency HAVING sum(amount_minor) <> 0"
        )
        return [(str(row[0]), int(row[1])) for row in cursor.fetchall()]


def _fold(account_id: uuid.UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT coalesce(sum(amount_minor), 0) FROM hub.ledger_entries WHERE account_id = %s",
            [account_id],
        )
        return int(cursor.fetchone()[0])


@pytest.mark.tc("TC-FR-04-2")
def test_stateful_ledger_machine(world: Any) -> None:
    class Machine(RuleBasedStateMachine):
        """Spend, charge a fee, replay the last key: the ledger stays a ledger."""

        def __init__(self) -> None:
            super().__init__()
            self.world = world
            self.last_key: str | None = None
            self.last_request: PostingRequest | None = None

        def _post(self, request: PostingRequest) -> bool:
            """Post, remembering the key so a replay can be attempted later."""
            self.last_key, self.last_request = request.posting_key, request
            try:
                with wiring.unit_of_work() as uow:
                    wiring.service().post(request, uow=uow)
            except InsufficientWalletBalance:
                # A spending rule that overdraws is a legal step: the ledger refuses it.
                return False
            return True

        @rule(amount=__import__("hypothesis").strategies.integers(min_value=1, max_value=5_000))
        def spend(self, amount: int) -> None:
            self._post(
                PostingRequest(
                    posting_key=f"token:{uuid.uuid4()}:purchase",
                    kind="energy_purchase",
                    lines=[
                        PostingLine(
                            account=self.world.wallet,
                            direction="debit",
                            amount=Money(amount, "USD"),
                        ),
                        PostingLine(
                            account=AccountRef(kind="revenue_energy", currency="USD"),
                            direction="credit",
                            amount=Money(amount, "USD"),
                        ),
                    ],
                    effective_at=NOW,
                    source_top_up_id=self.world.top_up_id,
                )
            )

        @rule(amount=__import__("hypothesis").strategies.integers(min_value=1, max_value=1_000))
        def fee(self, amount: int) -> None:
            self._post(
                PostingRequest(
                    posting_key=f"fee:{uuid.uuid4()}",
                    kind="fee",
                    lines=[
                        PostingLine(
                            account=self.world.wallet,
                            direction="debit",
                            amount=Money(amount, "USD"),
                        ),
                        PostingLine(
                            account=AccountRef(kind="fees", currency="USD"),
                            direction="credit",
                            amount=Money(amount, "USD"),
                        ),
                    ],
                    effective_at=NOW,
                    source_top_up_id=self.world.top_up_id,
                )
            )

        @rule()
        def replay_same_event(self) -> None:
            if self.last_request is None:
                return
            before = _fold(self.world.wallet_id)
            with wiring.unit_of_work() as uow:
                result = wiring.service().post(self.last_request, uow=uow)
            assert result.replayed is True, "the same key can only be a replay"
            assert _fold(self.world.wallet_id) == before

        @invariant()
        def books_balance(self) -> None:
            assert _sum_by_currency() == []

        @invariant()
        def wallet_matches_its_fold(self) -> None:
            maintained = self.world.balance()
            assert maintained == -_fold(self.world.wallet_id)

    # `run_state_machine_as_test` is a module function since hypothesis 6.x removed the class
    # method that older examples use.
    run_state_machine_as_test(
        Machine,
        settings=hypothesis_settings(
            max_examples=15, stateful_step_count=8, deadline=None, print_blob=False
        ),
    )
