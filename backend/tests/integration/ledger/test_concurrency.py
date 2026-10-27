"""Concurrency: the wallet row is the serialisation point, and it holds (TC-FR-04-4).

Twenty or a hundred threads debit one wallet at once. Two things must be true at the end:

* the balance is exactly what the arithmetic says — no lost update, because the balance trigger
  applies a *relative* delta to a row it locks, not a value read elsewhere;
* the overdraft is refused by the constraint, not by a race that got lucky.

The same posting key from twenty threads is the other half of the at-most-once guard: exactly
one call writes, nineteen find the row. These tests need real commits (``transaction=True``),
because a rollback would hide the very serialisation they are about.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
from django.db import connection

from hub.ledger import wiring
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest
from hub.ledger.domain.errors import InsufficientWalletBalance
from hub.shared.money import Money

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def _debit(world: Any, amount: int, *, posting_key: str) -> str:
    """One fee debit on a fresh thread: its own connection, its own unit of work."""
    try:
        with wiring.unit_of_work() as uow:
            wiring.service().post(
                PostingRequest(
                    posting_key=posting_key,
                    kind="fee",
                    lines=[
                        PostingLine(
                            account=world.wallet,
                            direction="debit",
                            amount=Money(amount, "USD"),
                        ),
                        PostingLine(
                            account=world.fees,
                            direction="credit",
                            amount=Money(amount, "USD"),
                        ),
                    ],
                    effective_at=NOW,
                    source_top_up_id=world.top_up_id,
                ),
                uow=uow,
            )
        return "posted"
    except InsufficientWalletBalance:
        return "overdraft"
    finally:
        connection.close()


@pytest.mark.tc("TC-FR-04-4")
def test_100_concurrent_debits_keep_balance(make_world_factory: Any) -> None:
    """A wallet of 10,000 survives 100 debits of 100 and refuses the 101st."""
    world = make_world_factory(funded_minor=10_000)
    keys = [f"race:{uuid.uuid4()}" for _ in range(100)]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda key: _debit(world, 100, posting_key=key), keys))

    assert results.count("posted") == 100, results
    assert world.balance() == 0
    assert _debit(world, 100, posting_key=f"race:{uuid.uuid4()}") == "overdraft"


@pytest.mark.tc("TC-NFR-05-2")
def test_20_concurrent_same_posting_key_one_winner(make_world_factory: Any) -> None:
    """Twenty threads, one key: one transaction, one effect, nineteen replays."""
    world = make_world_factory(funded_minor=5_000)
    key = f"same:{uuid.uuid4()}"

    def attempt(_: int) -> bool:
        """Return whether this call was the one that wrote."""
        try:
            with wiring.unit_of_work() as uow:
                result = wiring.service().post(
                    PostingRequest(
                        posting_key=key,
                        kind="fee",
                        lines=[
                            PostingLine(
                                account=world.wallet,
                                direction="debit",
                                amount=Money(100, "USD"),
                            ),
                            PostingLine(
                                account=world.fees,
                                direction="credit",
                                amount=Money(100, "USD"),
                            ),
                        ],
                        effective_at=NOW,
                        source_top_up_id=world.top_up_id,
                    ),
                    uow=uow,
                )
            return not result.replayed
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = list(pool.map(attempt, range(20)))

    assert winners.count(True) == 1, winners
    assert world.balance() == 4_900, "one credit of 100, not twenty"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM hub.ledger_entries e"
            "  JOIN hub.ledger_transactions t ON t.id = e.transaction_id"
            " WHERE t.posting_key = %s",
            [key],
        )
        assert cursor.fetchone()[0] == 2


def test_two_wallet_postings_do_not_deadlock(approve: Any, make_world_factory: Any) -> None:
    """Two postings touching the same two wallets in opposite order still both commit.

    The function inserts entries ``ORDER BY account_id``, so the lock order is the schema's,
    not the caller's array order — which is what makes this a test of the database rather than
    of luck.
    """
    first = make_world_factory(funded_minor=10_000)
    second = make_world_factory(funded_minor=10_000)
    prize, fee = 250, 5

    def move(source: Any, target: Any, name: str) -> str:
        # The approval's payload must be the posted lines in order: the database compares the
        # hash of exactly this array with the one the approval stored. The "opposite order"
        # this test is about is the direction of the transfer, not the array: `a` moves
        # first -> second and `b` moves second -> first, so the two transactions lock the same
        # two wallet rows from different ends.
        lines = [
            {
                "account_id": str(target.wallet_id),
                "currency": "USD",
                "amount_minor": -(prize - fee),
            },
            {"account_id": str(source.wallet_id), "currency": "USD", "amount_minor": prize},
            {
                "account_id": str(source.account_id("fees.USD")),
                "currency": "USD",
                "amount_minor": -fee,
            },
        ]
        approval = approve(
            action="ledger.adjustment",
            payload=lines,
            maker=first.maker,
            approver=first.checker,
            customer_id=target.customer_id,
        )
        try:
            with wiring.unit_of_work() as uow:
                wiring.service().post(
                    PostingRequest(
                        posting_key=f"pair:{name}:{uuid.uuid4()}",
                        kind="adjustment",
                        lines=[
                            PostingLine(
                                account=AccountRef(
                                    kind="customer_wallet",
                                    currency="USD",
                                    customer_id=target.customer_id,
                                ),
                                direction="credit",
                                amount=Money(prize - fee, "USD"),
                            ),
                            PostingLine(
                                account=AccountRef(
                                    kind="customer_wallet",
                                    currency="USD",
                                    customer_id=source.customer_id,
                                ),
                                direction="debit",
                                amount=Money(prize, "USD"),
                            ),
                            PostingLine(
                                account=AccountRef(kind="fees", currency="USD"),
                                direction="credit",
                                amount=Money(fee, "USD"),
                            ),
                        ],
                        effective_at=NOW,
                        approval_id=approval,
                    ),
                    uow=uow,
                )
            return "posted"
        except Exception as error:  # noqa: BLE001 - the assertion names what happened
            return f"{type(error).__name__}: {error}"
        finally:
            connection.close()

    results: list[str] = []
    lock = threading.Lock()

    def run(source: Any, target: Any, name: str) -> None:
        outcome = move(source, target, name)
        with lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=run, args=(first, second, "a")),
        threading.Thread(target=run, args=(second, first, "b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert results == ["posted", "posted"], results
    # Each move: the source pays 250 and the target receives 245, so each wallet is the source
    # of one move and the target of the other: 20,000 - 250 + 245 twice = 20,000 - 2 * 5.
    assert first.balance() + second.balance() == 20_000 - 2 * fee
