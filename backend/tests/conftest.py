"""Test infrastructure: one PostgreSQL 16 and one Redis container per session (docs/10 6.1).

The Postgres container receives ``db/schema.sql`` once, exactly as production does, and the
schema's roles are given a dev-only LOGIN so the application role can connect. Nothing here
touches a developer's local database: every integration test runs against the throwaway
container, so ``make verify`` is deterministic and never needs a running compose stack.

``django_db_setup`` is overridden because the default pytest-django behaviour (create
``test_<name>`` and run Django migrations) cannot build this schema: it is SQL-first
(ADR-0020), the tables are unmanaged, and the roles the script creates are NOLOGIN.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from django.conf import settings
from django.db import connections
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from hub.settings.base import build_database

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = REPO_ROOT / "db" / "schema.sql"
POSTGRES_IMAGE = "postgres:16"
REDIS_IMAGE = "redis:7-alpine"

# Dev-only password for the application role inside the throwaway container. The schema
# creates every role NOLOGIN on purpose; the deployment is what adds LOGIN and a rotated
# password from the secrets manager (db/schema.sql section 0).
TEST_ROLE_PASSWORD = "dev"


def _libpq_dsn(url: str) -> str:
    """Strip the SQLAlchemy driver marker testcontainers adds: ``postgresql+psycopg://``."""
    scheme, _, rest = url.partition("://")
    return f"{scheme.split('+', 1)[0]}://{rest}"


def _as_role(dsn: str, *, user: str, password: str) -> str:
    """Return the same DSN with a different role."""
    parts = urlsplit(dsn)
    host = parts.hostname or "localhost"
    netloc = f"{user}:{password}@{host}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A PostgreSQL 16 database with ``db/schema.sql`` applied, once per session.

    Yields the URL of the ``hub_api`` role, i.e. the connection the api deployable uses.
    """
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg", dbname="hub_test") as postgres:
        admin_url = _libpq_dsn(postgres.get_connection_url())
        with psycopg.connect(admin_url, autocommit=True) as connection:
            # One statement list: the schema is written to run as a superuser on an empty
            # database (db/schema.sql header), so it is executed in a single call.
            connection.execute(SCHEMA_SQL.read_text(encoding="utf-8"), prepare=False)
            connection.execute(f"ALTER ROLE hub_api LOGIN PASSWORD '{TEST_ROLE_PASSWORD}'")
        yield _as_role(admin_url, user="hub_api", password=TEST_ROLE_PASSWORD)


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """A Redis 7 container for the session (docs/10 6.1: FLUSHALL per test where needed)."""
    with RedisContainer(REDIS_IMAGE) as redis:
        host = redis.get_container_host_ip()
        port = redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def django_db_setup(postgres_url: str) -> Iterator[None]:
    """Point Django's ``default`` alias at the session container (overrides pytest-django).

    The schema is already applied by :func:`postgres_url`, so no ``migrate`` runs: slice 00
    has no Django models at all (every table is SQL-first and unmanaged, ADR-0020).
    """
    os.environ["HUB_TEST_DB_URL"] = postgres_url
    database = build_database("HUB_TEST_DB_URL", app_name="hub-test")
    # The alias dict is mutated in place, never replaced: ``connections`` keeps the dict
    # object it first read (Django lists DATABASES as a complex override, and a replaced
    # dict would leave the handler pointing at the old one).
    settings.DATABASES["default"].update(database)
    connections.close_all()
    yield
