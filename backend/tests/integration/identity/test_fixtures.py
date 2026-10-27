"""`manage.py load_fixtures`: the published identities slice 01 depends on (NFR-14, D-38).

The command writes real rows, so these tests call it against the test database and then read
what a sign-in would read: a hash the password verifier accepts, a TOTP secret the decryptor
returns unchanged, roles, attributes and site scopes. Idempotence matters as much as the first
run — `make up && manage.py load_fixtures` is in the demo script, twice is a mistake anyone can
make, and duplicate grants or a second customer would be worse than a no-op.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection

from hub.identity.infrastructure.crypto import decrypt_secret
from hub.identity.management.commands.load_fixtures import refusal
from hub.shared.security import verify_password

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

SEED = Path(__file__).resolve().parents[4] / "db" / "seed" / "test_users.json"


def seed_data() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(SEED.read_text(encoding="utf-8"))
    return document


def staff_row(email: str) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT u.id, u.password_hash, u.mfa_secret_enc, u.status, u.all_sites, "
            "       u.finance_lead, coalesce(array_agg(ur.role_code) FILTER "
            "       (WHERE ur.role_code IS NOT NULL), '{}') "
            "  FROM hub.users u LEFT JOIN hub.user_roles ur ON ur.user_id = u.id "
            " WHERE u.email = %s GROUP BY u.id",
            [email],
        )
        row = cursor.fetchone()
    assert row is not None, f"{email} was not seeded"
    return row


def test_seeds_a_signed_in_account_for_every_role() -> None:
    data = seed_data()

    call_command("load_fixtures", verbosity=0)

    for record in data["staff"]:
        row = staff_row(str(record["email"]))
        assert row[3] == "active"
        assert bool(row[4]) is bool(record.get("all_sites", False))
        assert bool(row[5]) is ("finance_lead" in (record.get("attributes") or ()))
        assert sorted(row[6]) == sorted(record["roles"])
    assert verify_password(staff_row("support@example.test")[1], data["password"]) is True


def test_totp_secret_is_the_one_in_the_file() -> None:
    """The demo script and the tests read the file, so the stored secret must match it."""
    call_command("load_fixtures", verbosity=0)

    lead = staff_row("finance.lead@example.test")
    secret = next(
        record["totp_secret"]
        for record in seed_data()["staff"]
        if record["email"] == "finance.lead@example.test"
    )
    assert decrypt_secret(lead[2]) == secret


def test_two_administrators_and_one_active_customer() -> None:
    data = seed_data()

    call_command("load_fixtures", verbosity=0)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM hub.users u JOIN hub.user_roles r ON r.user_id = u.id "
            "WHERE r.role_code = 'administrator'"
        )
        assert cursor.fetchone()[0] == 2
        cursor.execute(
            "SELECT status, language, phone_e164 FROM hub.customers WHERE id = %s",
            [uuid.UUID(str(data["customer"]["id"]))],
        )
        customer = cursor.fetchone()
    assert customer == ("active", "fr", data["customer"]["phone_e164"])


def test_running_twice_changes_nothing() -> None:
    call_command("load_fixtures", verbosity=0)
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table in ("users", "user_roles", "customers", "meters", "plan_tariffs"):
            cursor.execute(f"SELECT count(*) FROM hub.{table}")  # noqa: S608 - literal table names
            counts[table] = int(cursor.fetchone()[0])

    call_command("load_fixtures", verbosity=0)

    with connection.cursor() as cursor:
        for table, before in counts.items():
            cursor.execute(f"SELECT count(*) FROM hub.{table}")  # noqa: S608
            assert cursor.fetchone()[0] == before, f"{table} changed on the second run"
    # The registry demo (slice 02) rides along, so the counts are not one anymore; what this test
    # pins is that the *second* run adds nothing, whatever the first one wrote.


def test_target_names_are_guard_rails() -> None:
    """The environment is the real guard; the name catches a shell pointed somewhere else."""
    assert refusal("local", "hub") is None
    assert refusal("local", "hub_rw") is None, "the compose stack connects through PgBouncer"
    assert refusal("test", "hub_test") is None
    assert refusal("ci", "hub_ci_build7") is None
    assert refusal("production", "hub") is not None
    assert refusal("local", "hub_prod") is not None
    assert refusal("local", "soleil") is not None


def test_refuses_outside_a_seedable_environment(settings: Any) -> None:
    """Published TOTP secrets must never reach a real database."""
    settings.HUB_ENV = "production"

    with pytest.raises(SystemExit) as refused:
        call_command("load_fixtures", verbosity=0)

    assert refused.value.code == 3
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM hub.users")
        assert cursor.fetchone()[0] == 0
