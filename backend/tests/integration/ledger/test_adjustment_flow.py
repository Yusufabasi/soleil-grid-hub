"""Manual adjustments and refunds through maker-checker (D-15, TC-FR-04-1, TC-FR-04-4).

The flow the contract promises: a finance officer proposes, a *different* finance lead approves,
and only then does money move. Every refusal in between is a code a client can branch on:

* the maker cannot approve their own proposal (by user, and by session);
* the approval is consumed by the posting, so it cannot authorise a second one, and its payload
  hash binds the decision to the exact lines — one minor unit of drift is refused;
* an approver linked to the customer is a conflict of interest;
* a stale session cannot even propose (401 ``step_up_required``);
* a debit larger than the wallet fails and leaves the approval pending, because the execution's
  transaction (decision, posting, audit) rolls back as one.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from django.db import connection

from hub.identity import wiring as identity_wiring
from hub.ledger import wiring
from hub.ledger.contracts import AccountRef, PostingLine, PostingRequest, TopUpFacts
from hub.ledger.domain.errors import ApprovalRequired
from hub.shared.errors import Conflict, PermissionDenied
from hub.shared.money import Money
from support.principals import SeededUser, seed_staff_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
ADJUSTMENTS = "/api/v1/ledger/adjustments"
REFUNDS = "/api/v1/refunds"


def _propose(client: Any, maker: SeededUser, world: Any, **overrides: Any) -> Any:
    body = {
        "customer_id": str(world.customer_id),
        "direction": "credit_wallet",
        "amount": {"amount_minor": 500, "currency": "USD"},
        "reason": "goodwill for the outage of 3 October",
    }
    body.update(overrides)
    return client.post(
        ADJUSTMENTS,
        data=json.dumps(body),
        content_type="application/json",
        headers=maker.headers(),
    )


def _transactions_for(approval_id: uuid.UUID) -> list[tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, kind FROM hub.ledger_transactions WHERE approval_id = %s",
            [approval_id],
        )
        return [(str(row[0]), str(row[1])) for row in cursor.fetchall()]


@pytest.mark.tc("TC-FR-04-1")
def test_adjustment_proposed_then_posted_after_second_approver(
    api: Any, world: Any, finance_lead_maker: SeededUser, finance_lead_checker: SeededUser
) -> None:
    response = _propose(api, finance_lead_maker, world)
    assert response.status_code == 202, response.content
    document = response.json()["data"]
    assert document["status"] == "pending"
    assert document["kind"] == "manual_adjustment"
    assert response["Location"] == f"/api/v1/approvals/{document['id']}"
    approval_id = uuid.UUID(document["id"])
    assert _transactions_for(approval_id) == [], "a proposal posts nothing"

    decided = identity_wiring.approvals().approve(finance_lead_checker.principal, approval_id)

    assert decided.status == "executed"
    assert decided.approver_id == finance_lead_checker.user_id
    rows = _transactions_for(approval_id)
    assert len(rows) == 1
    assert rows[0][1] == "adjustment"
    assert world.balance() == 100_500
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT currency, sum(amount_minor) FROM hub.ledger_entries"
            " WHERE transaction_id = %s GROUP BY currency",
            [rows[0][0]],
        )
        assert cursor.fetchall() == [("USD", 0)], "balanced in the database"


def test_self_approval_forbidden(api: Any, world: Any, finance_lead_maker: SeededUser) -> None:
    approval_id = uuid.UUID(_propose(api, finance_lead_maker, world).json()["data"]["id"])
    with pytest.raises(PermissionDenied) as raised:
        identity_wiring.approvals().approve(finance_lead_maker.principal, approval_id)
    assert raised.value.code == "self_approval_forbidden"

    # The same person through a second session is still the same person (D-16): the schema
    # checks both, and the service checks both before it writes.
    other_session = replace(finance_lead_maker.principal, session_id=uuid.uuid4())
    with pytest.raises(PermissionDenied) as raised_again:
        identity_wiring.approvals().approve(other_session, approval_id)
    assert raised_again.value.code == "self_approval_forbidden"
    assert _transactions_for(approval_id) == []


def test_approval_cannot_be_reused_or_altered(
    api: Any, world: Any, finance_lead_maker: SeededUser, finance_lead_checker: SeededUser
) -> None:
    approval_id = uuid.UUID(_propose(api, finance_lead_maker, world).json()["data"]["id"])
    identity_wiring.approvals().approve(finance_lead_checker.principal, approval_id)

    # Reuse: the same approval, the same lines, a second posting.
    with pytest.raises(ApprovalRequired):
        _post_with_approval(world, approval_id, 500)

    # Alteration: a fresh approval, then lines one minor unit larger than the approved ones.
    second = uuid.UUID(
        _propose(
            api, finance_lead_maker, world, amount={"amount_minor": 900, "currency": "USD"}
        ).json()["data"]["id"]
    )
    with pytest.raises(ApprovalRequired):
        _post_with_approval(world, second, 901)


def _post_with_approval(world: Any, approval_id: uuid.UUID, amount_minor: int) -> Any:
    with wiring.unit_of_work() as uow:
        return wiring.service().post(
            PostingRequest(
                posting_key=f"adjustment:{approval_id}",
                kind="adjustment",
                lines=[
                    PostingLine(
                        account=AccountRef(kind="suspense", currency="USD"),
                        direction="debit",
                        amount=Money(amount_minor, "USD"),
                    ),
                    PostingLine(
                        account=world.wallet,
                        direction="credit",
                        amount=Money(amount_minor, "USD"),
                    ),
                ],
                effective_at=NOW,
                approval_id=approval_id,
            ),
            uow=uow,
        )


def test_conflict_of_interest_blocks_approver(
    api: Any, world: Any, finance_lead_maker: SeededUser, finance_lead_checker: SeededUser
) -> None:
    approval_id = uuid.UUID(_propose(api, finance_lead_maker, world).json()["data"]["id"])
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.staff_customer_link (user_id, customer_id) VALUES (%s, %s)",
            [finance_lead_checker.user_id, world.customer_id],
        )
    with pytest.raises(PermissionDenied) as raised:
        identity_wiring.approvals().approve(finance_lead_checker.principal, approval_id)
    assert raised.value.code == "conflict_of_interest"
    assert _transactions_for(approval_id) == []


def test_adjustment_without_step_up_is_401(api: Any, world: Any) -> None:
    """A session that proved identity more than five minutes ago cannot move money."""
    maker = seed_staff_user("finance_officer", attributes=("finance_lead",))
    response = api.post(
        ADJUSTMENTS,
        data=json.dumps(
            {
                "customer_id": str(world.customer_id),
                "direction": "credit_wallet",
                "amount": {"amount_minor": 500, "currency": "USD"},
                "reason": "goodwill for the outage of 3 October",
            }
        ),
        content_type="application/json",
        headers=maker.headers(auth_age_seconds=600),
    )
    assert response.status_code == 401, response.content
    assert response.json()["code"] == "step_up_required"


@pytest.mark.tc("TC-FR-04-4")
def test_adjustment_overdraft_debit_rejected(
    api: Any, world: Any, finance_lead_maker: SeededUser, finance_lead_checker: SeededUser
) -> None:
    """A debit larger than the wallet is refused at execution, and the approval stays pending."""
    response = _propose(
        api,
        finance_lead_maker,
        world,
        direction="debit_wallet",
        amount={"amount_minor": 1_000_000, "currency": "USD"},
    )
    assert response.status_code == 202, response.content
    approval_id = uuid.UUID(response.json()["data"]["id"])

    with pytest.raises(Conflict):
        identity_wiring.approvals().approve(finance_lead_checker.principal, approval_id)

    with connection.cursor() as cursor:
        cursor.execute("SELECT status FROM hub.approvals WHERE id = %s", [approval_id])
        assert cursor.fetchone()[0] == "pending"
    assert world.balance() == 100_000


class _Lookup:
    """A minimal `TopUpLookup` over the world's confirmed top-up (slice 04 binds the real one)."""

    def __init__(self, world: Any) -> None:
        self._world = world

    def get(self, top_up_id: uuid.UUID) -> TopUpFacts | None:
        if top_up_id != self._world.top_up_id:
            return None
        return TopUpFacts(
            id=self._world.top_up_id,
            customer_id=self._world.customer_id,
            amount_minor=100_000,
            currency="USD",
            status="confirmed",
            ledger_transaction_id=self._world.top_up_transaction_id,
        )


def test_refund_executes_reverse_ledger_transaction(
    api: Any, world: Any, finance_lead_maker: SeededUser, finance_lead_checker: SeededUser
) -> None:
    wiring.use_top_up_lookup(_Lookup(world))
    try:
        response = api.post(
            REFUNDS,
            data=json.dumps(
                {
                    "top_up_id": str(world.top_up_id),
                    "amount": {"amount_minor": 100_000, "currency": "USD"},
                    "reason": "customer was double charged by the provider",
                }
            ),
            content_type="application/json",
            headers=finance_lead_maker.headers(),
        )
        assert response.status_code == 202, response.content
        document = response.json()["data"]
        assert document["kind"] == "refund"
        assert document["payload"]["original_transaction_id"] == str(world.top_up_transaction_id)

        approval_id = uuid.UUID(document["id"])
        identity_wiring.approvals().approve(finance_lead_checker.principal, approval_id)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM hub.ledger_transactions"
                " WHERE reverses_transaction_id = %s AND approval_id = %s",
                [world.top_up_transaction_id, approval_id],
            )
            assert cursor.fetchone()[0] == 1, "one reversal, authorised by the refund approval"
        assert world.balance() == 0
    finally:
        wiring.use_top_up_lookup(_Null())


class _Null:
    def get(self, top_up_id: uuid.UUID) -> TopUpFacts | None:
        del top_up_id
        return None
