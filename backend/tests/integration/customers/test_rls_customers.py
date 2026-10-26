"""Row-level security on ``hub.customers`` (TC-FR-15-6, D-22): the database's own answer.

The application applies site scope in SQL, which is the first layer; these tests prove the
second one, because a future handler that forgets the filter must still not see another site's
customer. They run as ``hub_api`` — the role the API connects as — because the test database's
own alias is a superuser, and a superuser bypasses RLS. The rows are committed: a second
connection, which is what RLS is about, cannot see an uncommitted test transaction.

The claim is the transaction-local ``app.user_id`` that ``DjangoUnitOfWork`` sets, so a request
that forgot to announce its caller sees nothing rather than everything.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

HUB_API_PASSWORD = "rls-customers-password"


@dataclass(frozen=True)
class Registry:
    """Two sites with one customer each, and a support agent bound to the first."""

    site_a: UUID
    site_b: UUID
    plan_id: UUID
    customer_a: UUID
    customer_b: UUID
    site_bound_user: UUID


@pytest.fixture(scope="module")
def hub_api_dsn(postgres_server: Any) -> str:
    """Give the API role a login for these tests; the deployment does the same from secrets."""
    with psycopg.connect(postgres_server.dsn("hub_test"), autocommit=True) as conn:
        conn.execute(f"ALTER ROLE hub_api LOGIN PASSWORD '{HUB_API_PASSWORD}'")
    return str(postgres_server.dsn("hub_test", user="hub_api", password=HUB_API_PASSWORD))


@pytest.fixture
def registry(postgres_url: str) -> Iterator[Registry]:
    ids = Registry(
        site_a=uuid.uuid4(),
        site_b=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        customer_a=uuid.uuid4(),
        customer_b=uuid.uuid4(),
        site_bound_user=uuid.uuid4(),
    )
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        for site_id in (ids.site_a, ids.site_b):
            conn.execute(
                "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, 'USD')",
                [site_id, f"RLSC-{site_id.hex[:6]}", "RLS customers"],
            )
        conn.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor) "
            "VALUES (%s, %s, 'RLS customers plan', 'USD', 100, 100000)",
            [ids.plan_id, f"RLSC-P-{ids.plan_id.hex[:6]}"],
        )
        for customer_id, site_id in ((ids.customer_a, ids.site_a), (ids.customer_b, ids.site_b)):
            conn.execute(
                "INSERT INTO hub.customers (id, site_id, plan_id, full_name) "
                "VALUES (%s, %s, %s, %s)",
                [customer_id, site_id, ids.plan_id, "RLS customer"],
            )
        conn.execute(
            "INSERT INTO hub.users (id, email, display_name, status, all_sites) "
            "VALUES (%s, %s, 'Site bound', 'active', false)",
            [ids.site_bound_user, f"{ids.site_bound_user}@example.test"],
        )
        conn.execute(
            "INSERT INTO hub.user_roles (user_id, role_code) VALUES (%s, 'support_agent')",
            [ids.site_bound_user],
        )
        conn.execute(
            "INSERT INTO hub.user_sites (user_id, site_id) VALUES (%s, %s)",
            [ids.site_bound_user, ids.site_a],
        )
    try:
        yield ids
    finally:
        with psycopg.connect(postgres_url, autocommit=True) as conn:
            conn.execute("DELETE FROM hub.user_sites WHERE user_id = %s", [ids.site_bound_user])
            conn.execute("DELETE FROM hub.user_roles WHERE user_id = %s", [ids.site_bound_user])
            conn.execute("DELETE FROM hub.users WHERE id = %s", [ids.site_bound_user])
            conn.execute(
                "DELETE FROM hub.customers WHERE id = ANY(%s)",
                [[ids.customer_a, ids.customer_b]],
            )
            conn.execute("DELETE FROM hub.plans WHERE id = %s", [ids.plan_id])
            conn.execute("DELETE FROM hub.sites WHERE id = ANY(%s)", [[ids.site_a, ids.site_b]])


def claim(conn: psycopg.Connection[Any], name: str, value: str | None) -> None:
    """Set one transaction-local claim, exactly as `DjangoUnitOfWork` does."""
    conn.execute("SELECT set_config(%s, %s, true)", [name, value or ""])


def visible_customers(conn: psycopg.Connection[Any]) -> set[UUID]:
    rows = conn.execute("SELECT id FROM hub.customers").fetchall()
    return {row[0] for row in rows}


def test_rls_foreign_site_rows_invisible_and_immutable(
    hub_api_dsn: str, registry: Registry
) -> None:
    """A site-bound caller reads its own row only, and cannot write the other one."""
    with psycopg.connect(hub_api_dsn) as conn:
        with conn.transaction():
            claim(conn, "app.user_id", str(registry.site_bound_user))
            assert visible_customers(conn) == {registry.customer_a}

            updated = conn.execute(
                "UPDATE hub.customers SET full_name = 'taken over' WHERE id = %s",
                [registry.customer_b],
            )
            assert updated.rowcount == 0

        with conn.transaction():
            claim(conn, "app.user_id", str(registry.site_bound_user))
            row = conn.execute(
                "SELECT full_name FROM hub.customers WHERE id = %s", [registry.customer_b]
            ).fetchone()
            assert row is None


def test_rls_missing_claim_zero_rows(hub_api_dsn: str, registry: Registry) -> None:
    """Fail closed: a transaction that announced nobody may not touch a customer."""
    with psycopg.connect(hub_api_dsn) as conn:
        assert visible_customers(conn) == set()
        updated = conn.execute(
            "UPDATE hub.customers SET full_name = 'anonymous' WHERE id = %s",
            [registry.customer_a],
        )
        assert updated.rowcount == 0
