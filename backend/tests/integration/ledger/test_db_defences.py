"""The database's own defences, proved rather than assumed (TC-FR-04-3, TC-FR-04-6).

Three claims, each of which a deployment would otherwise discover the hard way:

1. **No application role can write a ledger table.** ``hub_api`` holds EXECUTE on the definer
   functions and no INSERT, UPDATE or DELETE on ``ledger_*`` or ``wallet_balances``; the test
   connects *as* ``hub_api``, because a superuser would silently pass.
2. **The definer functions are safe to expose.** Owned by the no-login ``ledger_owner`` with a
   pinned ``search_path``, so a temporary table cannot hijack an unqualified name.
3. **History is immutable even for the owner.** The triggers are ``ENABLE ALWAYS``, so an
   UPDATE, a DELETE or a TRUNCATE is refused by statement, not by row, and
   ``session_replication_role = replica`` does not skip it (S-9).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

HUB_API_PASSWORD = "hub-api-tests-only"  # noqa: S105 - a throwaway role in a throwaway database

LEDGER_FUNCTIONS = (
    "assert_transaction_balanced",
    "guard_entry_insert",
    "guard_period",
    "apply_wallet_balance",
    "create_wallet_account",
    "post_ledger_transaction",
    "reverse_ledger_transaction",
    "snapshot_ledger_balances",
)


def _dsn_as(role: str, password: str | None = None) -> str:
    """The test database's DSN, as ``role``; the superuser's own password by default."""
    settings = connection.settings_dict
    host = settings["HOST"] or "localhost"
    port = settings["PORT"] or "5432"
    secret = settings["PASSWORD"] or "" if password is None else password
    return f"postgresql://{role}:{secret}@{host}:{port}/{settings['NAME']}"


def _as_superuser(statement: str) -> None:
    """Run one role change on a connection of its own, so it commits immediately.

    ``django_db`` holds the test in a transaction, and an ``ALTER ROLE`` made inside it is
    invisible to the ``hub_api`` connection this fixture opens next: the role would still be
    NOLOGIN, and the test would be refused for the wrong reason.
    """
    with psycopg.connect(_dsn_as(str(connection.settings_dict["USER"])), autocommit=True) as admin:
        admin.execute(statement)


@pytest.fixture
def hub_api() -> Iterator[psycopg.Connection[Any]]:
    """A real connection as ``hub_api``: the role every API request runs as.

    The role logs in for the duration and is put back to NOLOGIN afterwards, which is the
    state ``db/invariant_tests.sql`` pins for every application role.
    """
    _as_superuser(f"ALTER ROLE hub_api LOGIN PASSWORD '{HUB_API_PASSWORD}'")
    conn = psycopg.connect(_dsn_as("hub_api", HUB_API_PASSWORD))
    try:
        yield conn
    finally:
        conn.close()
        _as_superuser("ALTER ROLE hub_api NOLOGIN")


def _refused(conn: psycopg.Connection[Any], statement: str) -> None:
    """Run one statement as ``hub_api`` and require an insufficient-privilege refusal."""
    conn.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.cursor() as cursor:
            cursor.execute(statement)
    conn.rollback()


@pytest.mark.tc("TC-FR-04-3")
def test_app_role_cannot_write_ledger_tables(hub_api: psycopg.Connection[Any]) -> None:
    """Every write an ORM mistake could attempt is refused by the grant, not by review."""
    statements = [
        "INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)"
        " VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'USD', 1)",
        "UPDATE hub.ledger_entries SET amount_minor = amount_minor",
        "DELETE FROM hub.ledger_entries",
        "UPDATE hub.ledger_transactions SET description = description",
        "DELETE FROM hub.ledger_transactions",
        "INSERT INTO hub.ledger_transactions (id, posting_key, kind)"
        " VALUES (gen_random_uuid(), 'x', 'fee')",
        "INSERT INTO hub.ledger_accounts (id, code, kind, currency)"
        " VALUES (gen_random_uuid(), 'x', 'fees', 'USD')",
        "UPDATE hub.ledger_accounts SET code = code",
        "UPDATE hub.wallet_balances SET balance_minor = balance_minor",
        "DELETE FROM hub.wallet_balances",
    ]
    for statement in statements:
        _refused(hub_api, statement)


@pytest.mark.tc("TC-FR-04-3")
def test_app_role_may_call_the_functions(hub_api: psycopg.Connection[Any]) -> None:
    """The other half of the same grant: the one write path is reachable."""
    hub_api.rollback()
    with hub_api.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('hub_api', 'create_wallet_account(uuid, char)', 'EXECUTE')"
        )
        assert cursor.fetchone() == (True,)
    hub_api.rollback()


@pytest.mark.tc("TC-FR-04-6")
def test_definer_functions_are_owned_and_pinned() -> None:
    """Owned by ``ledger_owner`` (no login) with a search_path that cannot be hijacked."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.proname, pg_get_userbyid(p.proowner), p.proconfig"
            "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'hub' AND p.proname = ANY(%s)",
            [list(LEDGER_FUNCTIONS)],
        )
        rows = cursor.fetchall()
    assert len(rows) == len(LEDGER_FUNCTIONS)
    for name, owner, config in rows:
        assert owner == "ledger_owner", name
        settings_text = " ".join(config or [])
        assert "search_path=" in settings_text, name
        for schema in ("pg_catalog", "hub", "pg_temp"):
            assert schema in settings_text, (name, settings_text)


@pytest.mark.tc("TC-FR-04-6")
def test_triggers_are_enable_always() -> None:
    """A replica session, and pg_restore --disable-triggers, cannot edit the ledger."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tgname, tgenabled FROM pg_trigger"
            " WHERE NOT tgisinternal AND tgname LIKE 'trg_ledger%%'"
        )
        triggers = dict(cursor.fetchall())
    assert len(triggers) == 9, sorted(triggers)
    assert {name for name, mode in triggers.items() if mode != "A"} == set()


@pytest.mark.django_db(transaction=True)
def test_unbalanced_commit_rejected() -> None:
    """The deferred trigger refuses a transaction whose entries do not sum to zero.

    ``transaction=True`` because the check is deferred: it fires at COMMIT, and pytest-django's
    default savepoint never commits, so the rule would go unproven.
    """
    account_a, account_b = (uuid4() for _ in range(2))
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.ledger_accounts (id, code, kind, currency)"
            " VALUES (%s, %s, 'fees', 'USD'), (%s, %s, 'revenue_energy', 'USD')",
            [
                account_a,
                f"test.unbalanced.a.{account_a}",
                account_b,
                f"test.unbalanced.b.{account_b}",
            ],
        )

    with pytest.raises(DatabaseError) as raised:
        with transaction.atomic():
            # An unbalanced pair through the function is refused before the entries are written,
            # so the deferred check is exercised with a direct third line (as the owner would).
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO hub.ledger_transactions (id, posting_key, kind)"
                    " VALUES (%s, %s, 'fee')",
                    [uuid4(), f"test.unbalanced.{uuid4()}"],
                )
                cursor.execute("SELECT id FROM hub.ledger_transactions ORDER BY id DESC LIMIT 1")
                transaction_id = cursor.fetchone()[0]
                cursor.execute(
                    "INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency,"
                    " amount_minor) VALUES (%s, %s, %s, 'USD', 500), (%s, %s, %s, 'USD', -499)",
                    [
                        uuid4(),
                        transaction_id,
                        account_a,
                        uuid4(),
                        transaction_id,
                        account_b,
                    ],
                )
    assert "unbalanced" in str(raised.value) or "check" in str(raised.value).lower()


@pytest.mark.django_db(transaction=True)
def test_entry_into_committed_transaction_rejected() -> None:
    """An entry added to a transaction from an earlier commit would rewrite its effect (S-9).

    ``transaction=True`` so the earlier transaction really is committed and gets its own xid.
    """
    transaction_id = uuid4()
    debit_account, credit_account = uuid4(), uuid4()
    # One commit for the transaction and its two entries: the balance check is deferred to the
    # commit, so a transaction cannot be left alone in the table even for a moment.
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.ledger_accounts (id, code, kind, currency)"
                " VALUES (%s, %s, 'fees', 'USD'), (%s, %s, 'revenue_energy', 'USD')",
                [
                    debit_account,
                    f"test.committed.a.{debit_account}",
                    credit_account,
                    f"test.committed.b.{credit_account}",
                ],
            )
            cursor.execute(
                "INSERT INTO hub.ledger_transactions (id, posting_key, kind)"
                " VALUES (%s, %s, 'fee')",
                [transaction_id, f"test.committed.{transaction_id}"],
            )
            cursor.execute(
                "INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency,"
                " amount_minor) VALUES (%s, %s, %s, 'USD', 1), (%s, %s, %s, 'USD', -1)",
                [
                    uuid4(),
                    transaction_id,
                    debit_account,
                    uuid4(),
                    transaction_id,
                    credit_account,
                ],
            )

    # A different database transaction: the guard compares created_xid with the current one.
    with pytest.raises(DatabaseError) as raised:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency,"
                    " amount_minor) VALUES (%s, %s, %s, 'USD', 1)",
                    [uuid4(), transaction_id, debit_account],
                )
    assert "same database transaction" in str(raised.value)


def test_update_delete_truncate_refused_as_owner_session() -> None:
    """Statement-level triggers: even a zero-row UPDATE and a TRUNCATE are refused."""
    statements = [
        "UPDATE hub.ledger_entries SET amount_minor = amount_minor WHERE false",
        "DELETE FROM hub.ledger_entries WHERE false",
        "TRUNCATE hub.ledger_entries CASCADE",
        "TRUNCATE hub.ledger_transactions CASCADE",
    ]
    for statement in statements:
        with pytest.raises(DatabaseError) as raised:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement)
        assert "append-only" in str(raised.value), statement


def test_restore_session_cannot_bypass_immutability() -> None:
    """``session_replication_role = replica`` is what a restore uses; it changes nothing here."""
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = replica")
                cursor.execute("DELETE FROM hub.ledger_entries WHERE false")
