"""Run the designed schema in a real PostgreSQL 16 and prove its invariants.

Starts a throwaway container on a random localhost port with a random password and removes it
afterwards. Skipped when docker, psql or the postgres:16-alpine image is unavailable.
"""

import os
import re
import secrets
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
INVARIANTS = ROOT / "db" / "invariant_tests.sql"
DOC = ROOT / "docs" / "04-data-model.md"
IMAGE = "postgres:16-alpine"


def _docker_ready() -> bool:
    if shutil.which("docker") is None or shutil.which("psql") is None:
        return False
    done = subprocess.run(
        ["docker", "image", "inspect", IMAGE],
        capture_output=True,
        check=False,
        timeout=60,
    )
    return done.returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_ready(), reason="needs docker, psql and " + IMAGE
)


class Postgres:
    def __init__(self) -> None:
        self.name = f"sgh-pg-verify-{secrets.token_hex(4)}"
        self.password = secrets.token_hex(12)
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                self.name,
                "-e",
                f"POSTGRES_PASSWORD={self.password}",
                "-p",
                "127.0.0.1::5432",
                IMAGE,
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        mapping = subprocess.run(
            ["docker", "port", self.name, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.splitlines()[0]
        self.port = mapping.rsplit(":", 1)[1]
        self._wait_ready()

    def psql(
        self, db: str, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "psql",
                "-h",
                "127.0.0.1",
                "-p",
                self.port,
                "-U",
                "postgres",
                "-d",
                db,
                "-X",
                "-q",
                "-t",
                "-A",
                "-v",
                "ON_ERROR_STOP=1",
                *args,
            ],
            env={**os.environ, "PGPASSWORD": self.password},
            capture_output=True,
            text=True,
            timeout=120,
            check=check,
        )

    def _wait_ready(self) -> None:
        good = 0
        for _ in range(
            90
        ):  # the image restarts once after initialising; wait for stability
            ok = self.psql("postgres", "-c", "select 1", check=False).returncode == 0
            good = good + 1 if ok else 0
            if good >= 3:
                return
            time.sleep(1)
        raise RuntimeError("postgres did not become ready")

    def fresh_db(self, name: str) -> str:
        self.psql("postgres", "-c", f"CREATE DATABASE {name}")
        return name

    def load_schema(self, db: str) -> None:
        self.psql(db, "-f", str(SCHEMA))

    def stop(self) -> None:
        subprocess.run(
            ["docker", "stop", "-t", "2", self.name], capture_output=True, check=False
        )


@pytest.fixture(scope="module")
def pg():
    server = Postgres()
    yield server
    server.stop()


def chunks(text: str) -> list[tuple[str, str]]:
    """Split the invariant file into (expectation, sql) pairs at each '-- EXPECT:' marker."""
    result: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        marker = re.match(r"^-- EXPECT:\s*(ok|error|rows=\d+)\s*$", line)
        if marker:
            result.append((marker.group(1), []))
        elif result:
            result[-1][1].append(line)
    return [(expect, "\n".join(sql).strip()) for expect, sql in result]


def test_the_schema_loads_on_an_empty_database(pg):
    db = pg.fresh_db("schema_loads")

    pg.load_schema(db)

    tables = pg.psql(
        db,
        "-c",
        "select count(*) from information_schema.tables where table_schema='hub'",
    )
    assert int(tables.stdout.strip()) >= 30


def test_every_invariant_statement_behaves_as_expected(pg):
    db = pg.fresh_db("invariants")
    pg.load_schema(db)
    cases = chunks(INVARIANTS.read_text())
    assert len(cases) >= 97  # the first revision had 97; new controls add more

    mismatches = []
    for number, (expect, sql) in enumerate(cases, start=1):
        done = pg.psql(db, "-c", sql, check=False)
        if expect == "ok":
            good = done.returncode == 0
        elif expect == "error":
            good = done.returncode != 0
        else:
            wanted = int(expect.split("=")[1])
            lines = [r for r in done.stdout.splitlines() if r.strip()]
            good = done.returncode == 0 and len(lines) == wanted
        if not good:
            mismatches.append(
                f"#{number} expected {expect}, rc={done.returncode}: "
                f"{sql[:90]!r} {done.stderr.strip()[:160]}"
            )

    assert mismatches == []


def _setup_before_first_posting(cases):
    setup = []
    for expect, sql in cases:
        if "ledger_transactions" in sql:
            break
        setup.append((expect, sql))
    return setup


def test_twenty_simultaneous_deliveries_of_one_payment_credit_the_wallet_exactly_once(
    pg,
):
    db = pg.fresh_db("concurrency")
    pg.load_schema(db)
    for _, sql in _setup_before_first_posting(chunks(INVARIANTS.read_text())):
        pg.psql(db, "-c", sql)
    clearing = "66666666-0000-7000-8000-000000000002"
    wallet = "66666666-0000-7000-8000-000000000001"
    posting = f"""
BEGIN;
WITH t AS (
  INSERT INTO hub.ledger_transactions (id, posting_key, kind)
  VALUES (hub.uuid_v7(), 'concurrent:topup:1', 'topup_received') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'USD', v.amt
FROM t, (VALUES ('{clearing}'::uuid, 1000::bigint), ('{wallet}'::uuid, -1000::bigint)) AS v(acc, amt);
SELECT pg_sleep(1.0);
COMMIT;
"""
    with ThreadPoolExecutor(max_workers=20) as pool:
        runs = list(
            pool.map(lambda _: pg.psql(db, "-c", posting, check=False), range(20))
        )

    winners = [r for r in runs if r.returncode == 0]
    losers = [r for r in runs if r.returncode != 0]
    assert len(winners) == 1 and len(losers) == 19
    assert all("uq_ledger_transactions_posting_key" in r.stderr for r in losers)
    count = pg.psql(
        db,
        "-c",
        "select count(*) from hub.ledger_transactions where posting_key='concurrent:topup:1'",
    )
    balance = pg.psql(
        db,
        "-c",
        f"select balance_minor from hub.wallet_balances where account_id='{wallet}'",
    )
    assert count.stdout.strip() == "1" and balance.stdout.strip() == "1000"


def test_the_sql_in_the_document_is_the_sql_in_the_files():
    doc = DOC.read_text()
    schema = re.search(r"```sql schema\n(.*?)\n```", doc, re.DOTALL).group(1)
    tests = re.search(r"```sql invariant_tests\n(.*?)\n```", doc, re.DOTALL).group(1)

    assert schema.rstrip() == SCHEMA.read_text().rstrip()
    assert tests.rstrip() == INVARIANTS.read_text().rstrip()
