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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from hub.identity.infrastructure.crypto import encrypt_secret
from hub.shared.security import hash_password

SEEDABLE_ENVIRONMENTS = frozenset({"local", "test", "ci"})
# The registry demo of slice 02 lives in its own file: sites, plans with an active tariff,
# customers, meters and the conflict-of-interest link. Loading it from the identity command is
# a compromise the module note explains; slice T3 replaces the whole loader with the fixture
# package, and the file is the data either way.
DEFAULT_REGISTRY_SEED = Path("db") / "seed" / "registry_demo.json"
# `hub` is the compose stack's database and `hub_rw`/`hub_ro` are the PgBouncer routes to it;
# the suffixed forms are the throwaway ones. A name that looks production-shaped (anything else)
# is refused even in a local environment, which is the one case the variable cannot distinguish.
DATABASE_NAME_RE = re.compile(r"^hub(_(local|test|ci)(_[a-z0-9]+)?|_rw|_ro)?$")
REFUSAL_EXIT_CODE = 3
SEED_NAMESPACE = uuid.UUID("6f1c3b4e-7a2d-5b6c-9d8e-0a1b2c3d4e5f")
# Published on purpose: the account exists only in a throwaway database (see the seed file).
PASSWORD = "Soleil!Local-2026"  # noqa: S105 - a committed test credential, by design
DEFAULT_SEED_FILE = Path("db") / "seed" / "test_users.json"


def seed_file() -> Path:
    """The committed identity seed, resolved from the repository root."""
    return Path(str(settings.HUB_REPO_ROOT)) / DEFAULT_SEED_FILE


def registry_seed_file() -> Path:
    """The committed registry demo, resolved from the repository root."""
    return Path(str(settings.HUB_REPO_ROOT)) / DEFAULT_REGISTRY_SEED


def refusal(environment: str, database_name: str) -> str | None:
    """Why seeding is unsafe here, or ``None`` when it is allowed."""
    if environment not in SEEDABLE_ENVIRONMENTS:
        return f"HUB_ENV is {environment!r}; fixtures run only in local, test or ci"
    if not DATABASE_NAME_RE.fullmatch(database_name):
        return f"database {database_name!r} is not a local, test or ci hub database"
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
            actor = next(
                (record for record in data["staff"] if "administrator" in record["roles"]),
                data["staff"][0],
            )
            actor_id = uuid.uuid5(SEED_NAMESPACE, str(actor["email"]).lower())
            self._customer(
                data["customer"],
                site_id=site_id,
                plan_id=plan_id,
                actor_id=actor_id,
            )
            # The registry demo goes in the same transaction: a half-seeded registry would be
            # worse than none, and the RLS claim the customers need is the administrator's.
            registry = self._registry_demo(actor_id=actor_id)

        self.stdout.write(
            self.style.SUCCESS(
                f"seeded {len(data['staff'])} staff accounts and 1 customer from {path}"
            )
        )
        for record in data["staff"]:
            self.stdout.write(f"  {record['email']}: {', '.join(record['roles'])}")
        if registry:
            self.stdout.write(
                self.style.SUCCESS(
                    "seeded the registry demo: "
                    f"{registry['sites']} sites, {registry['plans']} plans, "
                    f"{registry['tariffs']} tariffs, {registry['customers']} customers, "
                    f"{registry['meters']} meters, {registry['conflict_links']} conflict link(s)"
                )
            )

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

    def _customer(
        self,
        record: dict[str, Any],
        *,
        site_id: uuid.UUID,
        plan_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        """Insert the customer as an administrator would: the policy needs a visible site.

        `hub.customers` has row-level security forced even for its owner, and the staff policy
        asks `hub.site_visible(site_id)` — nobody without a claim may write a customer. The
        seed announces the administrator it just created for this transaction only.
        """
        customer_id = uuid.UUID(str(record["id"]))
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.user_id', %s, true)", [str(actor_id)])
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

    # --- registry demo (slice 02) ---------------------------------------------------------
    def _registry_demo(self, *, actor_id: uuid.UUID) -> dict[str, int]:
        """Load `db/seed/registry_demo.json`: the registry the slice's demo walks through.

        Every row is ``ON CONFLICT (id) DO NOTHING``, so a second ``make seed`` is a no-op and
        the fixed ids in the file are also the ids the demo and the tests can name. The tariff
        rows are ``active``, which the schema only permits with a decided approval behind them
        (``ck_plan_tariffs_approved``); the loader writes that approval, using the two finance
        accounts the identity seed just created rather than inventing an approver.
        """
        path = registry_seed_file()
        if not path.exists():
            return {}
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"registry seed is not JSON: {path}: {exc}") from exc

        sites = {record["code"]: self._demo_site(record) for record in data.get("sites", [])}
        plans = {record["code"]: self._demo_plan(record) for record in data.get("plans", [])}
        approval = data.get("tariff_approval") or {}
        maker = self._user_id(str(approval.get("maker_email", "")))
        approver = self._user_id(str(approval.get("approver_email", "")))

        with connection.cursor() as cursor:
            # `hub.customers` is forced RLS and its staff policy asks `hub.site_visible`; the
            # administrator the identity seed wrote lends this transaction its claim.
            cursor.execute("SELECT set_config('app.user_id', %s, true)", [str(actor_id)])

        tariffs = list(data.get("tariffs", []))
        for record in tariffs:
            self._demo_tariff(
                record,
                plan_id=plans[str(record["plan_code"])],
                maker=maker,
                approver=approver,
            )
        customers = {
            str(record["external_ref"]): self._demo_customer(record, sites=sites, plans=plans)
            for record in data.get("customers", [])
        }
        meters = list(data.get("meters", []))
        for record in meters:
            self._demo_meter(record, sites=sites, customers=customers)
        links = list(data.get("conflict_links", []))
        for record in links:
            self._demo_link(record, customers=customers)
        return {
            "sites": len(sites),
            "plans": len(plans),
            "tariffs": len(tariffs),
            "customers": len(customers),
            "meters": len(meters),
            "conflict_links": len(links),
        }

    def _user_id(self, email: str) -> uuid.UUID:
        """The account an email names, which the identity seed has just written."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM hub.users WHERE email = %s", [email.lower()])
            row = cursor.fetchone()
        if row is None:
            raise CommandError(f"registry demo needs the staff account {email!r}")
        return uuid.UUID(str(row[0]))

    def _demo_site(self, record: dict[str, Any]) -> uuid.UUID:
        site_id = uuid.UUID(str(record["id"]))
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.sites (id, code, name, country_code, currency, timezone) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                [
                    site_id,
                    record["code"],
                    record["name"],
                    record.get("country_code", "CD"),
                    record["currency"],
                    record.get("timezone", "Africa/Lubumbashi"),
                ],
            )
        return site_id

    def _demo_plan(self, record: dict[str, Any]) -> uuid.UUID:
        plan_id = uuid.UUID(str(record["id"]))
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, "
                "max_topup_minor, status) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                [
                    plan_id,
                    record["code"],
                    record["name"],
                    record["currency"],
                    int(record["min_topup_minor"]),
                    int(record["max_topup_minor"]),
                    record.get("status", "active"),
                ],
            )
        return plan_id

    def _demo_tariff(
        self,
        record: dict[str, Any],
        *,
        plan_id: uuid.UUID,
        maker: uuid.UUID,
        approver: uuid.UUID,
    ) -> None:
        tariff_id = uuid.UUID(str(record["id"]))
        approval_id = uuid.uuid5(SEED_NAMESPACE, f"tariff-approval:{tariff_id}")
        effective = _parse_instant(str(record["effective_from"]))
        price = int(record["tariff_minor_per_kwh"])
        payload = json.dumps(
            {
                "plan_id": str(plan_id),
                "tariff_minor_per_kwh": price,
                "effective_from": str(record["effective_from"]),
                "reason": "registry demo seed",
            }
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, "
                "payload, status, approver_user_id, approver_session_id, decided_at, "
                "created_at, expires_at) "
                "VALUES (%s, 'tariff.change', %s, %s, %s::jsonb, 'approved', %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                [
                    approval_id,
                    maker,
                    # Deterministic session ids: the seed derives every id from the file, so a
                    # second run rewrites the same rows instead of inventing new ones.
                    uuid.uuid5(SEED_NAMESPACE, f"tariff-maker-session:{tariff_id}"),
                    payload,
                    approver,
                    uuid.uuid5(SEED_NAMESPACE, f"tariff-approver-session:{tariff_id}"),
                    effective - timedelta(hours=1),
                    effective - timedelta(days=1),
                    effective + timedelta(days=2),
                ],
            )
            cursor.execute(
                "INSERT INTO hub.plan_tariffs (id, plan_id, tariff_minor_per_kwh, "
                "effective_from, status, proposed_by, approval_id, created_at) "
                "VALUES (%s, %s, %s, %s, 'active', %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                [tariff_id, plan_id, price, effective, maker, approval_id, effective],
            )

    def _demo_customer(
        self,
        record: dict[str, Any],
        *,
        sites: dict[str, uuid.UUID],
        plans: dict[str, uuid.UUID],
    ) -> uuid.UUID:
        customer_id = uuid.UUID(str(record["id"]))
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.customers (id, site_id, plan_id, external_ref, full_name, "
                "phone_e164, email, language, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                [
                    customer_id,
                    sites[str(record["site_code"])],
                    plans[str(record["plan_code"])],
                    record["external_ref"],
                    record["full_name"],
                    record.get("phone_e164"),
                    record.get("email"),
                    record.get("language", "fr"),
                    record.get("status", "active"),
                ],
            )
        return customer_id

    def _demo_meter(
        self,
        record: dict[str, Any],
        *,
        sites: dict[str, uuid.UUID],
        customers: dict[str, uuid.UUID],
    ) -> None:
        owner = record.get("customer_external_ref")
        with connection.cursor() as cursor:
            # Naming `customer_id` on the insert is what lets the schema trigger write the
            # `meter_assignments` history row; an unassigned demo meter simply has none.
            cursor.execute(
                "INSERT INTO hub.meters (id, serial_no, site_id, customer_id, status, firmware) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                [
                    uuid.UUID(str(record["id"])),
                    record["serial_no"],
                    sites[str(record["site_code"])],
                    customers[str(owner)] if owner else None,
                    record.get("status", "provisioned"),
                    record.get("firmware"),
                ],
            )

    def _demo_link(self, record: dict[str, Any], *, customers: dict[str, uuid.UUID]) -> None:
        """The conflict-of-interest register: a staff member linked to a customer (D-15)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.staff_customer_link (user_id, customer_id, relation) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                [
                    self._user_id(str(record["staff_email"])),
                    customers[str(record["customer_external_ref"])],
                    record.get("relation", "other"),
                ],
            )


def _parse_instant(raw: str) -> datetime:
    """An RFC 3339 instant from the seed file, in UTC."""
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
