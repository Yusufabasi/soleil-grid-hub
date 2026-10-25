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
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
import redis
from django.conf import settings
from django.db import connections
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from hub.identity import wiring
from hub.settings.base import build_database

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = REPO_ROOT / "db" / "schema.sql"

# `tests/support` is importable as `support.*` from every test module: the helpers there are
# shared, and a settings string (an authentication class) has to be importable by dotted path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
POSTGRES_IMAGE = "postgres:16"
REDIS_IMAGE = "redis:7-alpine"


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


_DATABASE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


@dataclass(frozen=True)
class PostgresServer:
    """The session container, addressable per database (docs/10 6.1).

    ``admin_user`` is the container's own superuser (``test`` for the official image): the
    baseline migration creates roles and grants, so it has to run as that role.
    """

    admin_dsn: str  # superuser, on the container's default database
    admin_user: str = "postgres"

    def dsn(self, database: str, *, user: str | None = None, password: str | None = None) -> str:
        """A DSN for ``database`` as ``user``; the superuser password is reused by default."""
        if not _DATABASE_NAME_RE.fullmatch(database):
            raise ValueError(f"not a database name: {database!r}")
        parts = urlsplit(self.admin_dsn)
        secret = password if password is not None else (parts.password or "")
        netloc = f"{user or self.admin_user}:{secret}@{parts.hostname}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, f"/{database}", "", ""))

    def create_database(self, name: str) -> str:
        """Drop and recreate ``name`` and return its superuser DSN."""
        if not _DATABASE_NAME_RE.fullmatch(name):
            raise ValueError(f"not a database name: {name!r}")
        with psycopg.connect(self.dsn("postgres"), autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                [name],
            )
            connection.execute(f'DROP DATABASE IF EXISTS "{name}"')
            connection.execute(f'CREATE DATABASE "{name}"')
        return self.dsn(name)


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[PostgresServer]:
    """PostgreSQL 16 with ``db/schema.sql`` applied to ``hub_test``, once per session."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg", dbname="hub_test") as postgres:
        admin_dsn = _libpq_dsn(postgres.get_connection_url())
        server = PostgresServer(admin_dsn, admin_user=urlsplit(admin_dsn).username or "postgres")
        with psycopg.connect(server.admin_dsn, autocommit=True) as connection:
            # One statement list: the schema is written to run as a superuser on an empty
            # database (db/schema.sql header), so it is executed in a single call.
            connection.execute(SCHEMA_SQL.read_text(encoding="utf-8"), prepare=False)
        yield server


@pytest.fixture(scope="session")
def postgres_url(postgres_server: PostgresServer) -> str:
    """The application database, as the container's superuser.

    The schema creates every application role NOLOGIN on purpose, and an invariant test
    pins that (``db/invariant_tests.sql``), so the harness must not add LOGIN to one. Tests
    that exercise a specific role create their own login and say so explicitly (docs/10 2.9).
    """
    return postgres_server.dsn("hub_test")


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """A Redis 7 container for the session (docs/10 6.1: FLUSHALL per test where needed)."""
    with RedisContainer(REDIS_IMAGE) as redis:
        host = redis.get_container_host_ip()
        port = redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture
def redis_store(settings: Any, redis_url: str) -> Iterator[redis.Redis]:
    """Point the identity wiring at the session Redis and start from an empty one.

    The wiring caches its adapters per process, so the settings override has to be followed by
    :func:`hub.identity.wiring.reset`; a test that changes ``HUB_REDIS_URL`` would otherwise keep
    talking to the previous client.
    """
    settings.HUB_REDIS_URL = redis_url
    wiring.reset()
    client: redis.Redis = redis.Redis.from_url(redis_url, decode_responses=True)
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()
        wiring.reset()


@pytest.fixture(scope="session")
def django_db_setup(postgres_url: str) -> Iterator[None]:
    """Point Django's ``default`` alias at the session container (overrides pytest-django).

    The schema is already applied by :func:`postgres_url`, so no ``migrate`` runs: slice 00
    has no Django models at all (every table is SQL-first and unmanaged, ADR-0020).
    """
    os.environ["HUB_TEST_DB_URL"] = postgres_url
    database = build_database("HUB_TEST_DB_URL", app_name="hub-test")
    # `db/schema.sql` gives every application role `search_path = hub, pg_catalog` (D-43) and
    # the models name bare tables, so the test alias emulates the role setting instead of
    # qualifying names in the models. Production reads it from the role, never from code.
    database["OPTIONS"] = {**database["OPTIONS"], "options": "-c search_path=hub,pg_catalog"}
    # The alias dict is mutated in place, never replaced: ``connections`` keeps the dict
    # object it first read (Django lists DATABASES as a complex override, and a replaced
    # dict would leave the handler pointing at the old one).
    settings.DATABASES["default"].update(database)
    connections.close_all()
    yield
