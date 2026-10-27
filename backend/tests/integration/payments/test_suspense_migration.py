"""Migration 0004: the ingest role's account reads, and `hub.post_suspense_receipt` (Task 04.7).

The frozen schema has two gaps that would make D-05 unimplementable as written, and this file
pins the migration that closes them without editing the baseline:

* **`hub_ingest` may resolve account ids but not read balances.** The posting functions take an
  id, so turning an `AccountRef` into one is a `SELECT` on `hub.ledger_accounts`. Before the
  migration the webhook pool gets ``permission denied``; after it, the ids and codes are readable
  and ``hub.wallet_balances`` — the one table that would show a customer's money — is not.
* **A mismatched delivery needs somewhere to land.** `hub.post_ledger_transaction` refuses a
  `topup_received` whose event disagrees with the top-up, and every manual kind wants an approval.
  `hub.post_suspense_receipt` posts the pair (debit clearing, credit suspense) from the *event*,
  which is the only amount a disagreement can be trusted to state.

The function tests run as the harness superuser, so they prove the function; the role tests run as
a fresh login that is a member of `hub_ingest`, so they prove the grant.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from django.db import connection, transaction

from support.payments import NOW

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

SUSPENSE_FUNCTION = "post_suspense_receipt"


def account_id(code: str) -> uuid.UUID:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM hub.ledger_accounts WHERE code = %s", [code])
        row = cursor.fetchone()
    assert row is not None, code
    return uuid.UUID(str(row[0]))


def seed_provider_event(
    *,
    event_type: str = "payment.succeeded",
    amount_minor: int = 500,
    currency: str = "USD",
    provider: str = "mpesa_cd",
) -> uuid.UUID:
    """One stored delivery; the receipt function reads it and nothing else."""
    event_id = uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id,"
            " event_type, amount_minor, currency, payload, status, received_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, '{}'::jsonb, 'received', %s)",
            [
                event_id,
                provider,
                f"evt_{event_id.hex[:8]}",
                f"txn-{event_id.hex[:8]}",
                event_type,
                amount_minor,
                currency,
                NOW,
            ],
        )
    return event_id


def call_receipt(
    *,
    posting_key: str,
    provider_event_id: uuid.UUID,
    clearing_code: str = "provider_clearing.mpesa_cd.USD",
    source_top_up_id: uuid.UUID | None = None,
) -> uuid.UUID:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT hub.post_suspense_receipt(%s, %s, %s, %s)",
            [
                posting_key,
                provider_event_id,
                account_id(clearing_code),
                # Nullable on purpose: a receipt whose source top-up is unknown is still a
                # receipt, and the foreign key accepts ``NULL`` where it would refuse a
                # fabricated id.
                source_top_up_id,
            ],
        )
        created = cursor.fetchone()
    assert created is not None
    return uuid.UUID(str(created[0]))


def entries_of(posting_key: str) -> list[tuple[str, int]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.code, e.amount_minor FROM hub.ledger_entries e"
            "  JOIN hub.ledger_transactions t ON t.id = e.transaction_id"
            "  JOIN hub.ledger_accounts a ON a.id = e.account_id"
            " WHERE t.posting_key = %s ORDER BY a.code",
            [posting_key],
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]


# --- the receipt ----------------------------------------------------------------------------


def test_suspense_receipt_posts_clearing_to_suspense() -> None:
    event_id = seed_provider_event()
    key = f"topup:{uuid.uuid4()}:suspense:evt"

    transaction_id = call_receipt(posting_key=key, provider_event_id=event_id)

    assert transaction_id is not None
    entries = entries_of(key)
    # Debit clearing (the money is ours to hold), credit suspense (it belongs to nobody yet).
    assert entries == [
        ("provider_clearing.mpesa_cd.USD", 500),
        ("suspense.USD", -500),
    ]
    assert sum(amount for _, amount in entries) == 0


def test_suspense_receipt_touches_no_wallet() -> None:
    """The customer's balance is untouched until a human resolves the exception (D-05)."""
    event_id = seed_provider_event()
    key = f"topup:{uuid.uuid4()}:suspense:evt"

    call_receipt(posting_key=key, provider_event_id=event_id)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM hub.ledger_entries e"
            "  JOIN hub.ledger_accounts a ON a.id = e.account_id"
            "  JOIN hub.ledger_transactions t ON t.id = e.transaction_id"
            " WHERE t.posting_key = %s AND a.kind = 'customer_wallet'",
            [key],
        )
        assert cursor.fetchone()[0] == 0


def test_suspense_receipt_is_idempotent_on_posting_key() -> None:
    event_id = seed_provider_event()
    key = f"topup:{uuid.uuid4()}:suspense:evt"

    first = call_receipt(posting_key=key, provider_event_id=event_id)
    second = call_receipt(posting_key=key, provider_event_id=event_id)

    assert first == second
    assert len(entries_of(key)) == 2


def test_suspense_receipt_requires_stored_success_event() -> None:
    with pytest.raises(Exception) as raised:
        with transaction.atomic():
            call_receipt(posting_key="topup:x:suspense:missing", provider_event_id=uuid.uuid4())
    assert "evidence_required" in str(raised.value)


def test_suspense_receipt_rejects_non_success_event_type() -> None:
    event_id = seed_provider_event(event_type="payment.failed")

    with pytest.raises(Exception) as raised:
        with transaction.atomic():
            call_receipt(posting_key="topup:x:suspense:failed", provider_event_id=event_id)

    assert "success event" in str(raised.value)


def test_suspense_receipt_rejects_the_wrong_currency_clearing_account() -> None:
    """A CDF charge cannot be parked against the USD clearing account: the ids would not add up."""
    event_id = seed_provider_event(currency="CDF")

    with pytest.raises(Exception) as raised:
        with transaction.atomic():
            call_receipt(
                posting_key="topup:x:suspense:cdf",
                provider_event_id=event_id,
                clearing_code="provider_clearing.mpesa_cd.USD",
            )

    assert "clearing account" in str(raised.value)


# --- the function's shape -------------------------------------------------------------------


def test_function_owner_and_search_path() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.prosecdef, pg_get_userbyid(p.proowner), p.proconfig"
            "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'hub' AND p.proname = %s",
            [SUSPENSE_FUNCTION],
        )
        row = cursor.fetchone()
    assert row is not None, "the migration did not create hub.post_suspense_receipt"
    security_definer, owner, config = row
    assert security_definer is True
    assert owner == "ledger_owner"
    assert config == ["search_path=pg_catalog, hub, pg_temp"]


def test_only_the_deployed_roles_may_execute_the_receipt() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('hub_ingest', 'hub.post_suspense_receipt"
            "(text, uuid, uuid, uuid)', 'EXECUTE'),"
            " has_function_privilege('hub_worker', 'hub.post_suspense_receipt"
            "(text, uuid, uuid, uuid)', 'EXECUTE'),"
            " has_function_privilege('hub_api', 'hub.post_suspense_receipt"
            "(text, uuid, uuid, uuid)', 'EXECUTE')"
        )
        ingest, worker, api = cursor.fetchone()
    assert (ingest, worker, api) == (True, True, False)


# --- the grant ------------------------------------------------------------------------------


@pytest.fixture
def ingest_login(postgres_server: Any) -> Iterator[psycopg.Connection[Any]]:
    """A login that is a member of `hub_ingest`, and nothing else (docs/10 2.9)."""
    with psycopg.connect(postgres_server.admin_dsn, autocommit=True) as admin:
        admin.execute("DROP ROLE IF EXISTS hub_ingest_login")
        admin.execute("CREATE ROLE hub_ingest_login LOGIN PASSWORD 'dev-only'")
        admin.execute("GRANT hub_ingest TO hub_ingest_login")
    dsn = postgres_server.dsn("hub_test", user="hub_ingest_login", password="dev-only")
    with psycopg.connect(dsn, autocommit=True) as user:
        user.execute("SET search_path = hub, pg_catalog")
        yield user
    with psycopg.connect(postgres_server.admin_dsn, autocommit=True) as admin:
        admin.execute("DROP ROLE IF EXISTS hub_ingest_login")


def test_hub_ingest_can_select_ledger_accounts_but_not_write(
    ingest_login: psycopg.Connection[Any],
) -> None:
    rows = ingest_login.execute(
        "SELECT id, code FROM hub.ledger_accounts WHERE code = 'suspense.USD'"
    ).fetchall()
    assert len(rows) == 1

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ingest_login.execute(
            "INSERT INTO hub.ledger_accounts (id, code, kind, currency)"
            " VALUES ('00000000-0000-7000-8000-000000000000', 'suspense.XTS', 'suspense', 'USD')"
        )


def test_hub_ingest_cannot_read_wallet_balances(
    ingest_login: psycopg.Connection[Any],
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ingest_login.execute("SELECT * FROM hub.wallet_balances LIMIT 1")
