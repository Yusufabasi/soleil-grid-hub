"""Bulk customer load: validate every row, then insert all of them or none (Task 02.8).

Why this is written at the use-case layer and not by reusing the API serializers:

* ``hub.customers.application`` may not import ``hub.customers.api`` (the layer contract), and
  reaching into it from a command would invert the dependency the whole module is built on;
* the *rules* are shared, not the code: the phone goes through the same ``normalise_e164`` the
  API uses, the locale through the same two-value rule, and the site and plan are resolved by
  the same stores. A row the API would refuse is refused here with the same code.

Two keys make a rerun safe, and they are deliberately different:

* ``external_ref`` is the source system's own id. A row whose reference already exists is
  **skipped**, counted, and not an error — that is what makes a fixed file re-uploadable.
* a phone number is the registry's unique key (D-11). A row whose number is already taken by
  someone else is a **duplicate_phone** error, because importing it would merge two people.

Nothing is written until every row has been validated: one bad row imports zero rows, and the
report names the row and the code, never the value.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from hub.customers.contracts import CustomerRow, CustomerStore, PlanStore, SiteStore
from hub.customers.domain.errors import InvalidPhoneNumber
from hub.customers.domain.phone import normalise_e164
from hub.identity.contracts import AuditLog, Principal
from hub.shared.contracts import UnitOfWork
from hub.shared.imports import ImportOutcome, ImportRefused, ImportRowError
from hub.shared.ports import Clock

__all__ = ["CustomerCsvImporter"]

# One column set, in the order the slice document lists it.
COLUMNS = frozenset(
    {"external_ref", "full_name", "phone", "email", "locale", "site_code", "plan_code"}
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LOCALES = {"en": "en", "fr": "fr"}


class CustomerCsvImporter:
    """``customers.csv``: one row per customer, resolved through site and plan codes."""

    kind = "customers"
    columns = COLUMNS
    operation = "createCustomer"

    def __init__(
        self,
        *,
        clock: Clock,
        ids: Any,
        customers: CustomerStore,
        sites: SiteStore,
        plans: PlanStore,
        audit: AuditLog,
        unit_of_work: Callable[[Principal | None], UnitOfWork],
        resolve_actor: Callable[[str], Principal],
        default_phone_prefix: str = "+243",
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._customers = customers
        self._sites = sites
        self._plans = plans
        self._audit = audit
        self._unit_of_work = unit_of_work
        self._resolve_actor = resolve_actor
        self._prefix = default_phone_prefix

    def run(
        self,
        *,
        actor_email: str,
        allowed_roles: frozenset[str],
        rows: Sequence[Mapping[str, str]],
        dry_run: bool,
    ) -> ImportOutcome:
        principal = self._resolve_actor(actor_email)
        if not (principal.roles & allowed_roles):
            raise ImportRefused(
                f"{actor_email} does not hold a role that may create customers "
                f"({', '.join(sorted(allowed_roles))})"
            )

        errors: list[ImportRowError] = []
        prepared: list[CustomerRow] = []
        references: set[str] = set()
        phones: set[str] = set()
        skipped = 0

        for offset, row in enumerate(rows):
            # Row 1 is the header; the first data row is row 2, as the operator counts.
            row_number = offset + 2
            reference = row.get("external_ref", "").strip()
            if not reference:
                errors.append(ImportRowError(row_number, "missing_external_ref"))
                continue
            if reference in references:
                errors.append(ImportRowError(row_number, "duplicate_external_ref"))
                continue
            references.add(reference)
            if self._customers.find_by_external_ref(reference) is not None:
                skipped += 1
                continue

            full_name = row.get("full_name", "").strip()
            if not full_name:
                errors.append(ImportRowError(row_number, "missing_full_name"))
                continue

            try:
                phone = normalise_e164(row.get("phone", ""), self._prefix)
            except InvalidPhoneNumber:
                errors.append(ImportRowError(row_number, "invalid_phone_number"))
                continue
            if phone in phones or self._customers.find_by_phone(phone) is not None:
                errors.append(ImportRowError(row_number, "duplicate_phone"))
                continue
            phones.add(phone)

            email = row.get("email", "").strip() or None
            if email is not None and not _EMAIL.match(email):
                errors.append(ImportRowError(row_number, "invalid_email"))
                continue

            site = self._sites.find_by_code(row.get("site_code", "").strip())
            if site is None:
                errors.append(ImportRowError(row_number, "unknown_site"))
                continue
            plan = self._plans.find_by_code(row.get("plan_code", "").strip())
            if plan is None:
                errors.append(ImportRowError(row_number, "unknown_plan"))
                continue
            if plan.currency != site.currency:
                # The plan is priced in the site's money; importing across currencies would
                # bill a customer in a currency their site does not accept.
                errors.append(ImportRowError(row_number, "currency_mismatch"))
                continue

            now = self._clock.now()
            prepared.append(
                CustomerRow(
                    id=self._ids.new_id(),
                    site_id=site.id,
                    plan_id=plan.id,
                    external_ref=reference,
                    full_name=full_name,
                    phone_e164=phone,
                    email=email,
                    language=_locale(row.get("locale", "")),
                    status="active",
                    pseudonym=None,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
            )

        if errors:
            return ImportOutcome(errors=errors)
        if dry_run:
            return ImportOutcome(skipped_existing=skipped, would_insert=len(prepared))

        with self._unit_of_work(principal) as uow:
            for customer in prepared:
                self._customers.add(customer)
            self._write_audit(principal, inserted=len(prepared), skipped=skipped)
            del uow  # the audit call is the last statement, as the chain requires
        return ImportOutcome(inserted=len(prepared), skipped_existing=skipped)

    def _write_audit(self, principal: Principal, *, inserted: int, skipped: int) -> None:
        """One row for the run, counts only: a name or a phone would undo D-21."""
        self._audit.record(
            stream="registry",
            actor=principal,
            action="registry.import.completed",
            entity_type="customer",
            entity_id=None,
            site_id=None,
            details={"kind": self.kind, "inserted": inserted, "skipped_existing": skipped},
        )


def _locale(raw: str) -> str:
    """Only ``en`` and ``fr`` are stored; anything else is ``en``, as the API decides."""
    return _LOCALES.get(raw.strip().lower(), "en")
