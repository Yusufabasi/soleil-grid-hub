"""Row-level security: the claims the unit of work sets, and what the tables do without them.

`db/schema.sql` enables and *forces* row-level security on the customer-scoped tables, and the
policies read two session settings — `app.user_id` for staff, `app.customer_id` for the portal.
Those are set with `set_config(..., true)`, which is transaction-local, and these tests prove
the three consequences the build cares about (TC-NFR-06-4, TC-NFR-06-5):

1. a request's claim lives exactly as long as its transaction;
2. a reused connection never shows one request's claim to the next;
3. a transaction without a claim sees nothing, rather than everything.

The assertions run as `hub_api`, the role the API connects as. The test database's own alias is
a superuser, and a superuser bypasses RLS — testing the policies through it would prove nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
import pytest
from django.db import connection

from hub.shared.contracts import PrincipalContext
from hub.shared.uow import DjangoUnitOfWork

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

HUB_API_PASSWORD = "rls-test-password"


@dataclass(frozen=True)
class Tenant:
    """Two sites, one customer each, and a staff user who may see only the first."""

    site_a: UUID
    site_b: UUID
    customer_a: UUID
    customer_b: UUID
    site_bound_user: UUID
    all_sites_user: UUID


@pytest.fixture(scope="module")
def hub_api_dsn(postgres_server: Any) -> str:
    """Give the API role a login for the tests; the deployment does the same from secrets."""
    admin = postgres_server.dsn("hub_test")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"ALTER ROLE hub_api LOGIN PASSWORD '{HUB_API_PASSWORD}'")
    return str(postgres_server.dsn("hub_test", user="hub_api", password=HUB_API_PASSWORD))


@pytest.fixture
def tenant(postgres_url: str) -> Iterator[Tenant]:
    """Committed rows: a second connection cannot see the test transaction's uncommitted work."""
    ids = Tenant(
        site_a=uuid.uuid4(),
        site_b=uuid.uuid4(),
        customer_a=uuid.uuid4(),
        customer_b=uuid.uuid4(),
        site_bound_user=uuid.uuid4(),
        all_sites_user=uuid.uuid4(),
    )
    plan_id = uuid.uuid4()
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, 'USD')",
            [ids.site_a, f"RLS-A-{ids.site_a.hex[:6]}", "RLS A"],
        )
        conn.execute(
            "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, 'USD')",
            [ids.site_b, f"RLS-B-{ids.site_b.hex[:6]}", "RLS B"],
        )
        conn.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor) "
            "VALUES (%s, %s, %s, 'USD', 100, 100000)",
            [plan_id, f"RLS-P-{plan_id.hex[:6]}", "RLS Plan"],
        )
        for customer_id, site_id, name in (
            (ids.customer_a, ids.site_a, "Customer A"),
            (ids.customer_b, ids.site_b, "Customer B"),
        ):
            conn.execute(
                "INSERT INTO hub.customers (id, site_id, plan_id, full_name) VALUES (%s, %s, %s, %s)",
                [customer_id, site_id, plan_id, name],
            )
        conn.execute(
            "INSERT INTO hub.users (id, email, display_name, status, all_sites) "
            "VALUES (%s, %s, %s, 'active', false)",
            [ids.site_bound_user, f"{ids.site_bound_user}@example.test", "Site bound"],
        )
        conn.execute(
            "INSERT INTO hub.users (id, email, display_name, status, all_sites) "
            "VALUES (%s, %s, %s, 'active', true)",
            [ids.all_sites_user, f"{ids.all_sites_user}@example.test", "All sites"],
        )
        for user_id in (ids.site_bound_user, ids.all_sites_user):
            conn.execute(
                "INSERT INTO hub.user_roles (user_id, role_code) VALUES (%s, 'support_agent')",
                [user_id],
            )
        conn.execute(
            "INSERT INTO hub.user_sites (user_id, site_id) VALUES (%s, %s)",
            [ids.site_bound_user, ids.site_a],
        )
    try:
        yield ids
    finally:
        with psycopg.connect(postgres_url, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM hub.user_sites WHERE user_id = ANY(%s)", [[ids.site_bound_user]]
            )
            conn.execute(
                "DELETE FROM hub.user_roles WHERE user_id = ANY(%s)",
                [[ids.site_bound_user, ids.all_sites_user]],
            )
            conn.execute(
                "DELETE FROM hub.users WHERE id = ANY(%s)",
                [[ids.site_bound_user, ids.all_sites_user]],
            )
            conn.execute(
                "DELETE FROM hub.customers WHERE id = ANY(%s)", [[ids.customer_a, ids.customer_b]]
            )
            conn.execute("DELETE FROM hub.plans WHERE id = %s", [plan_id])
            conn.execute("DELETE FROM hub.sites WHERE id = ANY(%s)", [[ids.site_a, ids.site_b]])


def claim(conn: psycopg.Connection[Any], name: str, value: str | None) -> None:
    """Set one transaction-local claim, exactly as `DjangoUnitOfWork` does."""
    conn.execute("SELECT set_config(%s, %s, true)", [name, value or ""])


def visible_customers(conn: psycopg.Connection[Any]) -> set[UUID]:
    rows = conn.execute("SELECT id FROM hub.customers").fetchall()
    return {row[0] for row in rows}


def test_unit_of_work_sets_claims_for_exactly_its_transaction(tenant: Tenant) -> None:
    """Two units of work in a row: each sees its own claim, and neither leaks to the next."""
    with DjangoUnitOfWork(principal=PrincipalContext(user_id=tenant.site_bound_user)) as _uow:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.user_id', true)")
            assert cursor.fetchone()[0] == str(tenant.site_bound_user)

    with DjangoUnitOfWork(principal=PrincipalContext(customer_id=tenant.customer_a)) as _uow:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('app.user_id', true), "
                "current_setting('app.customer_id', true)"
            )
            user_id, customer_id = cursor.fetchone()
        assert user_id == "", "the previous request's staff claim did not survive its transaction"
        assert customer_id == str(tenant.customer_a)


def test_pooled_connection_never_shows_another_requests_claim(
    hub_api_dsn: str, tenant: Tenant
) -> None:
    """Two connections, two claims, interleaved: each answers about its own caller only."""
    with psycopg.connect(hub_api_dsn) as first, psycopg.connect(hub_api_dsn) as second:
        with first.transaction():
            claim(first, "app.customer_id", str(tenant.customer_a))
            with second.transaction():
                claim(second, "app.customer_id", str(tenant.customer_b))
                assert visible_customers(second) == {tenant.customer_b}
            # The first connection's claim is still its own while the second came and went.
            assert visible_customers(first) == {tenant.customer_a}

        # Reusing the same server connection without a claim shows nothing at all.
        assert visible_customers(first) == set()


def test_missing_claim_returns_zero_rows(hub_api_dsn: str, tenant: Tenant) -> None:
    """Fail closed: a transaction that announced nobody is not allowed to see everybody."""
    with psycopg.connect(hub_api_dsn) as conn:
        assert visible_customers(conn) == set()
        row = conn.execute("SELECT count(*) FROM hub.customers").fetchone()
        assert row is not None and int(row[0]) == 0


def test_staff_claim_is_scoped_to_their_sites(hub_api_dsn: str, tenant: Tenant) -> None:
    """A support agent sees the sites in `user_sites`; an `all_sites` user sees every site."""
    with psycopg.connect(hub_api_dsn) as conn:
        with conn.transaction():
            claim(conn, "app.user_id", str(tenant.site_bound_user))
            assert visible_customers(conn) == {tenant.customer_a}

    with psycopg.connect(hub_api_dsn) as conn:
        with conn.transaction():
            claim(conn, "app.user_id", str(tenant.all_sites_user))
            assert visible_customers(conn) == {tenant.customer_a, tenant.customer_b}


def test_customer_claim_cannot_read_or_write_another_customer(
    hub_api_dsn: str, tenant: Tenant
) -> None:
    """The portal's claim names one row: another customer's row is invisible and immutable."""
    with psycopg.connect(hub_api_dsn) as conn:
        with conn.transaction():
            claim(conn, "app.customer_id", str(tenant.customer_a))
            assert visible_customers(conn) == {tenant.customer_a}
            updated = conn.execute(
                "UPDATE hub.customers SET full_name = 'taken over' WHERE id = %s",
                [tenant.customer_b],
            )
            assert updated.rowcount == 0
        with conn.transaction():
            claim(conn, "app.customer_id", str(tenant.customer_a))
            row = conn.execute(
                "SELECT count(*) FROM hub.customers WHERE id = %s", [tenant.customer_b]
            ).fetchone()
            assert row is not None and int(row[0]) == 0
