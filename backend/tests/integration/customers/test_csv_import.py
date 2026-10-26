"""``manage.py import_registry``: one file, one transaction, or nothing (Task 02.8).

The command is the registry's only bulk channel (Plan notes 1: the contract has no import
path), so the tests here are about the promises an operator relies on at three in the morning:

* a single bad row imports **zero** rows, and the report names the row and the code — never
  the value, because a bad row is usually a name or a phone number;
* a rerun of a fixed file inserts nothing and says how many rows it recognised;
* a dry run touches nothing, not even the audit stream;
* the caps are checked before the first insert;
* the run leaves one audit row with counts only.

The command runs in-process under ``hub.settings.test``; production invokes the same code as
``hub_api`` through ``hub.settings.api``, which differs only in the DSN and the role.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from hub.customers import wiring as customers_wiring
from hub.identity import wiring as identity_wiring
from hub.identity.infrastructure.models import User, UserRole
from hub.shared.clock import FrozenClock

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "registry"
SITE_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0501")
PLAN_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0502")
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SUPPORT = "support.import@example.test"
OPERATIONS = "operations.import@example.test"

# Only the tables a run can write; the name is never taken from a test's own string.
COUNTED = frozenset({"customers", "meters", "audit_log"})


def count(table: str) -> int:
    assert table in COUNTED
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM hub.{table}")  # noqa: S608 - asserted above
        return int(cursor.fetchone()[0])


def _user(email: str, role: str) -> UUID:
    user_id = uuid.uuid4()
    User.objects.create(
        id=user_id,
        email=email,
        display_name=email.split("@", maxsplit=1)[0],
        status="active",
        all_sites=True,
        token_version=1,
    )
    UserRole.objects.create(user_id=user_id, role_code=role)
    return user_id


@pytest.fixture(autouse=True)
def registry(redis_store: Any) -> Any:  # noqa: ANN401 - the fixture's job is the store
    """A frozen clock, the registry's site and plan, and one user per relevant role."""
    clock = FrozenClock(NOW)
    identity_wiring.use_clock(clock)
    customers_wiring.reset()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency, timezone, status) "
            "VALUES (%s, 'KIN', 'Kinshasa', 'USD', 'Africa/Kinshasa', 'active')",
            [SITE_ID],
        )
        cursor.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor, "
            "status) VALUES (%s, 'BASIC', 'Basic', 'USD', 100, 100000, 'active')",
            [PLAN_ID],
        )
    support_id = _user(SUPPORT, "support_agent")
    _user(OPERATIONS, "operations_engineer")
    yield support_id
    customers_wiring.reset()
    identity_wiring.use_clock(None)
    identity_wiring.reset()


def run(
    kind: str,
    path: Path,
    *,
    dry_run: bool = False,
    actor: str = SUPPORT,
) -> tuple[dict[str, Any], int]:
    """Run the command and return its JSON report and exit code (0, 2 or 3)."""
    buffer = io.StringIO()
    code = 0
    try:
        call_command(
            "import_registry",
            kind=kind,
            path=str(path),
            actor_email=actor,
            dry_run=dry_run,
            stdout=buffer,
        )
    except CommandError as refused:
        code = int(refused.returncode or 1)
    printed = [line for line in buffer.getvalue().splitlines() if line.strip()]
    return (json.loads(printed[-1]) if printed else {}), code


def test_import_all_or_nothing_and_idempotent_rerun() -> None:
    report, code = run("customers", FIXTURES / "customers_one_bad_row.csv")
    assert code == 2
    assert report["inserted"] == 0
    assert report["errors"] == [{"row": 3, "code": "duplicate_phone"}]
    assert count("customers") == 0, "one bad row must import nothing"

    report, code = run("customers", FIXTURES / "customers_ok.csv")
    assert code == 0, report
    assert (report["inserted"], report["skipped_existing"]) == (2, 0)
    assert count("customers") == 2

    report, code = run("customers", FIXTURES / "customers_ok.csv")
    assert code == 0, report
    assert (report["inserted"], report["skipped_existing"]) == (0, 2)
    assert count("customers") == 2, "a rerun keyed on external_ref adds nobody"


def test_formula_cells_neutralised() -> None:
    report, code = run("customers", FIXTURES / "customers_formula.csv")

    assert code == 0, report
    assert report["neutralised"] == [2]
    with connection.cursor() as cursor:
        cursor.execute("SELECT full_name FROM hub.customers")
        assert cursor.fetchone()[0] == "'=cmd()|' /C calc'!A0"


def test_row_and_size_caps(settings: Any) -> None:  # noqa: ANN401 - pytest-django's fixture
    settings.HUB_IMPORT_MAX_ROWS = 1
    _, code = run("customers", FIXTURES / "customers_ok.csv")
    assert code == 2
    assert count("customers") == 0

    settings.HUB_IMPORT_MAX_ROWS = 5000
    settings.HUB_IMPORT_MAX_BYTES = 32
    _, code = run("customers", FIXTURES / "customers_ok.csv")
    assert code == 2
    assert count("customers") == 0


def test_unknown_site_code_row_error(tmp_path: Path) -> None:
    path = tmp_path / "unknown-site.csv"
    path.write_text(
        "external_ref,full_name,phone,email,locale,site_code,plan_code\n"
        "EXT-301,Nobody Home,0812345688,,en,LUB,BASIC\n",
        encoding="utf-8",
    )

    report, code = run("customers", path)

    assert code == 2
    assert report["errors"] == [{"row": 2, "code": "unknown_site"}]
    assert count("customers") == 0


def test_meters_import_serial_conflict_reported() -> None:
    report, code = run("meters", FIXTURES / "meters_serial_conflict.csv", actor=OPERATIONS)

    assert code == 2
    assert report["errors"] == [{"row": 3, "code": "serial_conflict"}]
    assert count("meters") == 0


def test_meters_import_links_the_customer_by_external_ref() -> None:
    assert run("customers", FIXTURES / "customers_ok.csv")[1] == 0

    # Creating a meter is `createMeter`'s role set, not `createCustomer`'s: an import never
    # borrows another operation's power.
    report, code = run("meters", FIXTURES / "meters_ok.csv", actor=OPERATIONS)

    assert code == 0, report
    assert report["inserted"] == 2
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT m.serial_no, c.external_ref "
            "  FROM hub.meters m LEFT JOIN hub.customers c ON c.id = m.customer_id "
            " ORDER BY m.serial_no"
        )
        assert cursor.fetchall() == [("SN-0001", "EXT-001"), ("SN-0002", None)]


def test_dry_run_changes_nothing() -> None:
    report, code = run("customers", FIXTURES / "customers_ok.csv", dry_run=True)

    assert code == 0, report
    assert report["dry_run"] is True
    assert (report["inserted"], report["would_insert"]) == (0, 2)
    assert count("customers") == 0
    assert count("audit_log") == 0, "a dry run leaves no trace to explain later"


def test_import_writes_one_audit_event_with_counts_only(registry: UUID) -> None:
    report, code = run("customers", FIXTURES / "customers_ok.csv")
    assert code == 0, report

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT actor_id, action, entity_type, details::text FROM hub.audit_log "
            "WHERE stream = 'registry'"
        )
        rows = cursor.fetchall()

    assert len(rows) == 1
    actor_id, action, entity_type, details = rows[0]
    assert actor_id == registry
    assert action == "registry.import.completed"
    assert entity_type == "customer"
    # `outcome` is the audit writer's own field, added to every row it stores.
    assert json.loads(details) == {
        "kind": "customers",
        "inserted": 2,
        "skipped_existing": 0,
        "outcome": "success",
    }
    assert "Amani" not in details
    assert "812345678" not in details, "a phone in the audit details would undo D-21"


def test_import_refuses_non_utf8_and_bom_handled(tmp_path: Path) -> None:
    latin = tmp_path / "latin1.csv"
    latin.write_bytes(
        (
            "external_ref,full_name,phone,email,locale,site_code,plan_code\n"
            "EXT-401,André Mbala,0812345691,,fr,KIN,BASIC\n"
        ).encode("latin-1")
    )

    _, code = run("customers", latin)

    assert code == 2, "a mis-decoded name would be stored corrupted"
    assert count("customers") == 0

    bom = tmp_path / "bom.csv"
    bom.write_bytes(
        (
            "\ufeffexternal_ref,full_name,phone,email,locale,site_code,plan_code\n"
            "EXT-402,Bom Handled,0812345692,,en,KIN,BASIC\n"
        ).encode("utf-8")
    )

    report, code = run("customers", bom)

    assert code == 0, report
    assert report["inserted"] == 1


def test_import_refuses_an_actor_without_the_create_role() -> None:
    _, code = run("customers", FIXTURES / "customers_ok.csv", actor=OPERATIONS)

    assert code == 3
    assert count("customers") == 0


def test_import_refuses_an_unknown_actor() -> None:
    _, code = run("customers", FIXTURES / "customers_ok.csv", actor="nobody@example.test")

    assert code == 3
    assert count("customers") == 0
