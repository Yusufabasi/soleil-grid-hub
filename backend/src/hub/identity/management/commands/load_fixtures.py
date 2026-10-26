"""Seed the local or test database with the identity fixtures (D-38, NFR-14).

One command, one file of published test identities: a staff account per role, a finance officer
holding the ``finance_lead`` attribute, a second administrator (maker-checker needs two), and one
customer. Everything is committed on purpose — these accounts exist only in a throwaway database,
and every value is on the synthetic ranges the plan fixes (``@example.test``, ``+243810…``).

Three rules the command keeps:

* **It refuses to run anywhere real.** ``HUB_ENV`` must be ``local``, ``test`` or ``ci`` and the
  database name must look like ``hub_<env>``, or it writes nothing and exits 3. Seeding known
  TOTP secrets into a production database would be a back door, so the guard is a check on the
  target rather than a confirmation prompt.
* **It is idempotent.** Ids are derived from the email (UUIDv5), roles and site scopes are
  replaced rather than appended, so ``make up && manage.py load_fixtures`` twice leaves the same
  rows and does not duplicate grants.
* **It never prints a secret.** The summary lists addresses and roles; passwords and TOTP
  secrets stay in the file, which is how the demo script and the tests know them.

The command lives in `identity` rather than in the kernel's `shared` package because it writes
`users.mfa_secret_enc` with this module's key: `hub.shared` may not import a business module,
and duplicating the key derivation in the kernel would be worse than a command in the module
that owns the table. Slice T3 replaces this with the full fixture package (customers, meters,
telemetry); this command only owns what slice 01 needs to demonstrate sign-in and approvals.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from hub.identity.infrastructure.crypto import encrypt_secret
from hub.shared.security import hash_password

SEEDABLE_ENVIRONMENTS = frozenset({"local", "test", "ci"})
DATABASE_NAME_RE = re.compile(r"^hub_(local|test|ci)(_[a-z0-9]+)?$")
REFUSAL_EXIT_CODE = 3
SEED_NAMESPACE = uuid.UUID("6f1c3b4e-7a2d-5b6c-9d8e-0a1b2c3d4e5f")
# Published on purpose: the account exists only in a throwaway database (see the seed file).
PASSWORD = "Soleil!Local-2026"  # noqa: S105 - a committed test credential, by design
DEFAULT_SEED_FILE = Path("db") / "seed" / "test_users.json"


def seed_file() -> Path:
    """The committed identity seed, resolved from the repository root."""
    return Path(str(settings.HUB_REPO_ROOT)) / DEFAULT_SEED_FILE


def refusal(environment: str, database_name: str) -> str | None:
    """Why seeding is unsafe here, or ``None`` when it is allowed."""
    if environment not in SEEDABLE_ENVIRONMENTS:
        return f"HUB_ENV is {environment!r}; fixtures run only in local, test or ci"
    if not DATABASE_NAME_RE.fullmatch(database_name):
        return f"database {database_name!r} is not a hub_<local|test|ci> database"
    return None


class Command(BaseCommand):
    """Write the published identity fixtures into the configured database."""

    help = (
        "Seed the identity fixtures (staff per role, a finance lead, a second admin, a customer)."
    )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 - Django's signature
        environment = str(getattr(settings, "HUB_ENV", ""))
        database_name = str(connection.settings_dict["NAME"])
        reason = refusal(environment, database_name)
        if reason is not None:
            self.stderr.write(self.style.ERROR(f"refusing to seed: {reason}"))
            raise SystemExit(REFUSAL_EXIT_CODE)

        path = seed_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CommandError(f"seed file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"seed file is not JSON: {path}: {exc}") from exc

        password = str(data.get("password") or PASSWORD)
        now = datetime.now(UTC)
        with transaction.atomic():
            site_id = self._site(data["site"])
            plan_id = self._plan(data["plan"])
            for record in data["staff"]:
                self._staff(record, password=password, site_id=site_id, now=now)
            self._customer(data["customer"], site_id=site_id, plan_id=plan_id)

        self.stdout.write(
            self.style.SUCCESS(
                f"seeded {len(data['staff'])} staff accounts and 1 customer from {path}"
            )
        )
        for record in data["staff"]:
            self.stdout.write(f"  {record['email']}: {', '.join(record['roles'])}")

    # --- rows ---------------------------------------------------------------------------
    def _site(self, record: dict[str, Any]) -> uuid.UUID:
        site_id = uuid.UUID(str(record["id"]))
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                [site_id, record["code"], record["name"], record["currency"]],
            )
            return uuid.UUID(str(cursor.fetchone()[0]))

    def _plan(self, record: dict[str, Any]) -> uuid.UUID:
        plan_id = uuid.UUID(str(record["id"]))
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name "
                "RETURNING id",
                [
                    plan_id,
                    record["code"],
                    record["name"],
                    record["currency"],
                    int(record["min_topup_minor"]),
                    int(record["max_topup_minor"]),
                ],
            )
            row = cursor.fetchone()
        return uuid.UUID(str(row[0])) if row is not None else plan_id

    def _staff(
        self, record: dict[str, Any], *, password: str, site_id: uuid.UUID, now: datetime
    ) -> None:
        email = str(record["email"]).lower()
        user_id = uuid.uuid5(SEED_NAMESPACE, email)
        attributes = set(record.get("attributes") or ())
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.users "
                "(id, email, display_name, password_hash, mfa_secret_enc, mfa_enrolled_at, "
                " status, preferred_language, all_sites, finance_lead, dp_admin) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  display_name = EXCLUDED.display_name, "
                "  password_hash = EXCLUDED.password_hash, "
                "  mfa_secret_enc = EXCLUDED.mfa_secret_enc, "
                "  status = 'active', "
                "  preferred_language = EXCLUDED.preferred_language, "
                "  all_sites = EXCLUDED.all_sites, "
                "  finance_lead = EXCLUDED.finance_lead, "
                "  dp_admin = EXCLUDED.dp_admin",
                [
                    user_id,
                    email,
                    record["display_name"],
                    hash_password(password),
                    encrypt_secret(str(record["totp_secret"])),
                    now,
                    record.get("locale", "en"),
                    bool(record.get("all_sites", False)),
                    "finance_lead" in attributes,
                    "dp_admin" in attributes,
                ],
            )
            # Replace, never append: a role removed from the file must disappear here.
            cursor.execute("DELETE FROM hub.user_roles WHERE user_id = %s", [user_id])
            for role in record["roles"]:
                cursor.execute(
                    "INSERT INTO hub.user_roles (user_id, role_code) VALUES (%s, %s)",
                    [user_id, role],
                )
            cursor.execute("DELETE FROM hub.user_sites WHERE user_id = %s", [user_id])
            for _code in record.get("sites") or ():
                cursor.execute(
                    "INSERT INTO hub.user_sites (user_id, site_id) VALUES (%s, %s)",
                    [user_id, site_id],
                )

    def _customer(self, record: dict[str, Any], *, site_id: uuid.UUID, plan_id: uuid.UUID) -> None:
        customer_id = uuid.UUID(str(record["id"]))
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.customers "
                "(id, site_id, plan_id, full_name, phone_e164, email, language) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                [
                    customer_id,
                    site_id,
                    plan_id,
                    record["full_name"],
                    record.get("phone_e164"),
                    record.get("email"),
                    record.get("language", "en"),
                ],
            )
