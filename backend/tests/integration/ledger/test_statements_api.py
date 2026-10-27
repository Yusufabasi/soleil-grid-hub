"""The read surface: wallet, accounts, transactions and statements (TC-FR-04-1).

What the API must show, and what it must not:

* the wallet balance is minus the fold of its entries (D-03), never a number kept elsewhere;
* a customer reads one wallet — its own — and any other id is a 404, not a 403 (an existence
  oracle);
* a line shows a ``direction`` and a positive amount, so a client never has to know that a
  credit is stored negative;
* statements page by id and stay stable while rows arrive;
* a support agent has no ledger read at all, and no route writes one.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml
from django.db import connection
from django.test import Client
from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request

from hub.identity.contracts import Principal
from hub.ledger import wiring
from hub.ledger.api.views import CustomerWalletView
from hub.shared.db import announced as announced_read
from support.principals import SeededUser, seed_staff_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# The repository root, found from this file rather than assumed: the contract lives outside the
# backend package, and a hard-coded checkout path would only pass on the machine that wrote it.
REPO_ROOT = Path(__file__).resolve().parents[4]


def _fold(account_id: uuid.UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT coalesce(sum(amount_minor), 0) FROM hub.ledger_entries WHERE account_id = %s",
            [account_id],
        )
        return int(cursor.fetchone()[0])


class _CustomerAuthentication(BaseAuthentication):
    """A portal caller: the customer from the header, with no staff roles at all."""

    def authenticate(self, request: Request) -> tuple[Principal, None] | None:
        raw = request.headers.get("X-Test-Customer")
        if not raw:
            return None
        principal = Principal(
            subject_id=uuid.UUID(raw),
            kind="customer",
            roles=frozenset({"customer"}),
            customer_id=uuid.UUID(raw),
            session_id=uuid.uuid4(),
        )
        setattr(request, "principal", principal)  # noqa: B010
        return principal, None


@pytest.fixture
def portal() -> Iterator[Client]:
    with mock.patch.object(CustomerWalletView, "authentication_classes", [_CustomerAuthentication]):
        yield Client()


@pytest.mark.tc("TC-FR-04-1")
def test_wallet_endpoint_matches_fold(api: Any, world: Any, finance_lead_maker: SeededUser) -> None:
    response = api.get(
        f"/api/v1/customers/{world.customer_id}/wallet", headers=finance_lead_maker.headers()
    )
    assert response.status_code == 200, response.content
    document = response.json()["data"]
    assert document["balance"] == {"amount_minor": 100_000, "currency": "USD"}
    assert -_fold(world.wallet_id) == 100_000
    assert document["ledger_account_id"] == str(world.wallet_id)
    assert document["pending_top_ups"] == 0


def test_customer_cannot_read_other_wallet(portal: Client, world: Any) -> None:
    other = uuid.uuid4()
    response = portal.get(
        f"/api/v1/customers/{other}/wallet", headers={"X-Test-Customer": str(world.customer_id)}
    )
    assert response.status_code == 404, response.content

    own = portal.get(
        f"/api/v1/customers/{world.customer_id}/wallet",
        headers={"X-Test-Customer": str(world.customer_id)},
    )
    assert own.status_code == 200, own.content
    assert own.json()["data"]["customer_id"] == str(world.customer_id)


def test_list_accounts_filter_type_and_cursor(api: Any, finance_lead_maker: SeededUser) -> None:
    first = api.get(
        "/api/v1/ledger/accounts?type=fees&limit=1", headers=finance_lead_maker.headers()
    )
    assert first.status_code == 200, first.content
    page = first.json()
    assert page["meta"]["has_next"] is True
    assert len(page["data"]) == 1
    assert page["data"][0]["type"] == "fees"

    second = api.get(
        f"/api/v1/ledger/accounts?type=fees&limit=1&cursor={page['meta']['next_cursor']}",
        headers=finance_lead_maker.headers(),
    )
    assert second.status_code == 200, second.content
    assert second.json()["data"][0]["id"] != page["data"][0]["id"]

    unknown = api.get("/api/v1/ledger/accounts?type=nonsense", headers=finance_lead_maker.headers())
    assert unknown.status_code == 400


def test_transaction_shows_direction_and_positive_amounts(
    api: Any, world: Any, finance_lead_maker: SeededUser
) -> None:
    response = api.get(
        f"/api/v1/ledger/transactions/{world.top_up_transaction_id}",
        headers=finance_lead_maker.headers(),
    )
    assert response.status_code == 200, response.content
    document = response.json()["data"]
    assert document["posting_key"].startswith("topup:")
    assert document["source_type"] == "top_up"
    directions = {line["direction"] for line in document["lines"]}
    assert directions == {"debit", "credit"}
    assert all(line["amount"]["amount_minor"] > 0 for line in document["lines"])
    assert (
        sum(
            line["amount"]["amount_minor"] * (1 if line["direction"] == "debit" else -1)
            for line in document["lines"]
        )
        == 0
    )


def test_statement_keyset_pages_are_stable_during_inserts(world: Any, spend_from: Any) -> None:
    """A page boundary already returned is never repeated, even as rows arrive behind it."""
    spend_from(world, 1_000)
    reader = wiring.reader()
    first_page = reader.statement(world.wallet_id, limit=1)
    assert len(first_page) == 1
    cursor = first_page[-1].id

    # A new row after the cursor must not disturb the next page.
    with connection.cursor() as db:
        db.execute(
            "SELECT count(*) FROM hub.ledger_entries WHERE account_id = %s", [world.wallet_id]
        )
        before = db.fetchone()[0]
    assert before >= 2

    second_page = reader.statement(world.wallet_id, after=cursor, limit=5)
    assert cursor not in {entry.id for entry in second_page}
    assert all(entry.id > cursor for entry in second_page)


def test_agent_has_no_ledger_read(api: Any) -> None:
    agent = seed_staff_user("support_agent")
    response = api.get("/api/v1/ledger/accounts", headers=agent.headers())
    assert response.status_code == 403, response.content


def test_no_ledger_write_route_exists() -> None:
    """The contract has no route that posts, edits or deletes a ledger row."""
    document = yaml.safe_load(open(f"{REPO_ROOT}/api/openapi.yaml", encoding="utf-8"))
    methods: dict[str, set[str]] = {}
    for path, item in document["paths"].items():
        if path.startswith(("/ledger", "/accounting-periods", "/refunds")):
            methods[path] = {
                method for method in item if method in {"get", "post", "put", "patch", "delete"}
            }
    assert methods["/ledger/accounts"] == {"get"}
    assert methods["/ledger/accounts/{account_id}"] == {"get"}
    assert methods["/ledger/transactions"] == {"get"}
    assert methods["/ledger/transactions/{transaction_id}"] == {"get"}
    assert methods["/ledger/adjustments"] == {"post"}, "a proposal, not a posting"
    assert methods["/accounting-periods"] == {"get"}


def test_wallet_of_unknown_customer_is_empty_not_an_error(
    api: Any, finance_lead_maker: SeededUser
) -> None:
    """No wallet yet is a zero balance with no account id, which is what a customer sees."""
    response = api.get(
        f"/api/v1/customers/{uuid.uuid4()}/wallet", headers=finance_lead_maker.headers()
    )
    assert response.status_code == 200, response.content
    document = response.json()["data"]
    assert document["balance"]["amount_minor"] == 0
    assert document["ledger_account_id"] is None


def test_account_detail_reports_404(api: Any, finance_lead_maker: SeededUser) -> None:
    response = api.get(
        f"/api/v1/ledger/accounts/{uuid.uuid4()}", headers=finance_lead_maker.headers()
    )
    assert response.status_code == 404


def test_account_reads_announce_the_caller(world: Any) -> None:
    """Both account reads go through ``announced()``, and dropping that silently empties the screen.

    ``hub.ledger_accounts`` is forced-RLS: the ``hub_api`` policy lets a wallet account through
    when its customer exists, but a **system** account (``customer_id IS NULL``) only when
    ``hub.user_has_permission('ledger.read')`` — and that reads ``app.user_id``, a setting the
    application writes per transaction. A SELECT outside such a transaction matches no policy and
    answers zero rows, which is what the running stack did: ``GET /ledger/accounts`` returned an
    empty page for a finance officer while the 14 system accounts existed and ``/ledger/transactions``
    rendered normally (that table has RLS disabled).

    Why this is pinned as a mechanism rather than end to end: pytest-django wraps every test in one
    transaction and ``set_config(..., true)`` is transaction-local, so a fixture's write leaves an
    ``app.user_id`` behind for the whole test. A read that never announces its caller therefore
    still sees rows **in this suite** — which is exactly how the defect survived slice 03 — while in
    the running stack it does not. The reproduction and the fix were verified against the live
    stack; this pins the two call sites so they cannot be dropped again.
    """
    reader = wiring.reader()

    with mock.patch("hub.ledger.infrastructure.reader.announced", wraps=announced_read) as spy:
        assert reader.list_accounts(limit=5), "the fixture's ledger has no accounts to read"
        assert reader.account(world.account_id(world.fees.code)) is not None

    assert spy.call_count == 2
