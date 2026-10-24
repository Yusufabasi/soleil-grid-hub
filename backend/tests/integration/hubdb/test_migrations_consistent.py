"""The SQL-first migration chain must reproduce ``db/schema.sql`` exactly (ADR-0020).

Two databases are built from empty: one by ``manage.py migrate hubdb 0000`` (what production
does) and one by loading ``db/schema.sql`` directly (what the design document says). Their
catalogs must be identical, and the database invariants must pass on the migrated one too —
otherwise the migration chain has already drifted from the schema and every later slice
would ship a database nobody designed.

The pairing rules are checked against temporary trees as well, so the checker itself is
proven to fail on a gap, a missing Django wrapper and a bad phase line.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from django.db.migrations import RunSQL
from fingerprint import catalog_fingerprint

from hub.hubdb.sqlfiles import sql_text

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "backend"
SCHEMA = REPO_ROOT / "db" / "schema.sql"
BASELINE = REPO_ROOT / "db" / "baseline" / "0000_baseline.sql"
SQL_MIGRATIONS = REPO_ROOT / "db" / "migrations"
WRAPPERS = BACKEND_DIR / "src" / "hub" / "hubdb" / "migrations"
INVARIANTS = REPO_ROOT / "db" / "invariant_tests.sql"

sys.path.insert(0, str(REPO_ROOT / "tests"))
from test_schema_in_postgres import chunks  # noqa: E402

pytestmark = pytest.mark.integration

# A migrate subprocess needs a complete environment of its own: the test suite must not
# depend on a developer's .env file (make env always writes one).
MIGRATE_ENV = {
    "DJANGO_SETTINGS_MODULE": "hub.settings.migrate",
    "HUB_SECRET_KEY": "test-only-not-a-secret-0123456789-abcdefghijkl",
    "HUB_ALLOWED_HOSTS": "localhost",
    "SITE_ORIGIN": "http://localhost:8080",
    "HUB_REDIS_URL": "redis://localhost:6379/0",
    "HUB_ENV": "test",
}


def migrate(dsn: str) -> subprocess.CompletedProcess[str]:
    """Run the baseline migration against ``dsn`` exactly as the deploy job does."""
    return subprocess.run(
        [sys.executable, "manage.py", "migrate", "hubdb", "0000", "--noinput"],
        cwd=BACKEND_DIR,
        env={**os.environ, **MIGRATE_ENV, "HUB_MIGRATE_URL": dsn},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


@pytest.fixture(scope="session")
def migrated_dsn(postgres_server, django_db_setup: None) -> str:
    """A database built only by the migration chain (the fingerprint target)."""
    dsn: str = postgres_server.create_database("hub_migrated")
    done = migrate(dsn)
    assert done.returncode == 0, done.stderr
    return dsn


@pytest.fixture(scope="session")
def schema_dsn(postgres_server, django_db_setup: None) -> str:
    """A database built only by loading ``db/schema.sql`` (the design target)."""
    dsn: str = postgres_server.create_database("hub_from_schema")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
    return dsn


def pairing_violations(sql_dir: Path, wrapper_dir: Path) -> list[str]:
    """The pairing rules of docs/04 10.5, as a list of human-readable violations."""
    violations: list[str] = []
    expected = 1
    for path in sorted(sql_dir.glob("*.sql")):
        stem = path.stem
        number = stem.split("_", 1)[0]
        if not number.isdigit() or int(number) != expected:
            violations.append(f"{path.name}: expected number {expected:04d}, got {number!r}")
        source = path.read_text(encoding="utf-8")
        head = source.splitlines()[:1]
        phase_line = head[0].strip() if head else ""
        if phase_line not in ("-- phase: expand", "-- phase: contract"):
            violations.append(f"{path.name}: first line must be a phase marker")
        wrapper = wrapper_dir / f"{stem}.py"
        if not wrapper.exists():
            violations.append(f"{path.name}: no Django wrapper {wrapper.name}")
        else:
            wrapper_source = wrapper.read_text(encoding="utf-8")
            non_transactional = "-- transaction: none" in source
            if non_transactional and "atomic = False" not in wrapper_source:
                violations.append(f"{wrapper.name}: transaction: none needs atomic = False")
            if not non_transactional and "atomic = False" in wrapper_source:
                violations.append(f"{wrapper.name}: atomic = False without transaction: none")
        expected += 1
    return violations


def test_baseline_is_byte_copy_of_schema_sql() -> None:
    assert BASELINE.read_bytes() == SCHEMA.read_bytes()


def test_pairing_rules() -> None:
    assert pairing_violations(SQL_MIGRATIONS, WRAPPERS) == []


def test_pairing_rules_reject_a_gap_a_missing_wrapper_and_a_bad_phase(tmp_path: Path) -> None:
    sql_dir = tmp_path / "migrations"
    wrapper_dir = tmp_path / "wrappers"
    sql_dir.mkdir()
    wrapper_dir.mkdir()

    (sql_dir / "0001_add_column.sql").write_text(
        "-- phase: expand\nALTER TABLE hub.users ADD COLUMN IF NOT EXISTS x int;\n"
    )
    (wrapper_dir / "0001_add_column.py").write_text(
        "from django.db import migrations\n\nclass Migration(migrations.Migration):\n"
        "    atomic = True\n"
    )
    assert pairing_violations(sql_dir, wrapper_dir) == []

    # A gap: 0002 is missing, so 0003 is out of sequence.
    (sql_dir / "0003_drop_column.sql").write_text("-- phase: contract\nSELECT 1;\n")
    (wrapper_dir / "0003_drop_column.py").write_text("atomic = False\n")
    violations = pairing_violations(sql_dir, wrapper_dir)
    assert any("0002" in violation for violation in violations), violations

    # A missing wrapper for an otherwise valid number.
    (sql_dir / "0002_added.sql").write_text("-- phase: expand\nSELECT 1;\n")
    violations = pairing_violations(sql_dir, wrapper_dir)
    assert any("no Django wrapper" in violation for violation in violations), violations

    # A phase line that is not a phase marker.
    (sql_dir / "0002_added.sql").write_text("ALTER TABLE hub.users ADD COLUMN y int;\n")
    violations = pairing_violations(sql_dir, wrapper_dir)
    assert any("phase marker" in violation for violation in violations), violations


def test_migrate_from_empty_applies_baseline(migrated_dsn: str) -> None:
    with psycopg.connect(migrated_dsn) as connection:
        users = connection.execute("SELECT count(*) FROM hub.users").fetchone()
        applied = connection.execute(
            "SELECT app, name FROM django_migrations ORDER BY id"
        ).fetchall()
    assert users == (0,)
    assert ("hubdb", "0000_baseline") in applied


@pytest.mark.tc("TC-NFR-14-7")
def test_migrated_catalog_equals_schema_sql(migrated_dsn: str, schema_dsn: str) -> None:
    migrated = catalog_fingerprint(migrated_dsn)
    designed = catalog_fingerprint(schema_dsn)

    only_migrated = sorted(set(migrated) - set(designed))
    only_designed = sorted(set(designed) - set(migrated))
    diff = [f"only in migrated: {line}" for line in only_migrated] + [
        f"only in schema.sql: {line}" for line in only_designed
    ]
    assert diff == [], "\n".join(diff)


def test_invariants_also_pass_on_migrated_database(postgres_server, django_db_setup: None) -> None:
    dsn = postgres_server.create_database("hub_migrated_invariants")
    done = migrate(dsn)
    assert done.returncode == 0, done.stderr

    cases = chunks(INVARIANTS.read_text(encoding="utf-8"))
    assert len(cases) >= 97

    mismatches: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as connection:
        for number, (expect, sql) in enumerate(cases, start=1):
            try:
                result = connection.execute(sql)
                # A data-modifying statement returns no rows: only fetch when there is a result set.
                rows = result.fetchall() if result.description is not None else []
            except psycopg.Error:
                if expect != "error":
                    mismatches.append(f"#{number} expected {expect}, got an error")
                continue
            if expect == "ok":
                continue
            if expect.startswith("rows="):
                wanted = int(expect.split("=", 1)[1])
                if len(rows) != wanted:
                    mismatches.append(f"#{number} expected {wanted} rows, got {len(rows)}")
            else:
                mismatches.append(f"#{number} expected {expect}, but the statement succeeded")

    assert mismatches == [], "\n".join(mismatches)


def test_sql_text_runs_as_single_statement_list() -> None:
    module = importlib.import_module("hub.hubdb.migrations.0000_baseline")
    operation = module.Migration.operations[0]

    assert isinstance(operation, RunSQL)
    assert operation.sql == [sql_text("0000_baseline")]
    assert module.Migration.initial is True
    assert module.Migration.atomic is True
