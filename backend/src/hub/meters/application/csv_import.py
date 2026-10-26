"""Bulk meter load: validate every row, then insert all of them or none (Task 02.8).

The meter file has no ``external_ref`` in the contract's column list, so its idempotency key is
the serial number itself — the same natural key ``uq_meters_serial`` protects. A row whose
serial already exists is **skipped** (a rerun of the same file inserts nothing and says so),
while two rows of one file claiming the same serial are a **serial_conflict** error: that file
is contradictory, and picking a winner would silently drop a device.

The customer column is the registry's ``external_ref``, resolved through the facts port — this
module never reads ``hub.customers`` (docs/03 3.1). A row that names an unknown customer, an
inactive one, or one on another site is refused with the same codes the API uses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from hub.identity.contracts import AuditLog, Principal
from hub.meters.contracts import MeterRow, MeterStore, RegistryFacts
from hub.shared.contracts import UnitOfWork
from hub.shared.imports import ImportOutcome, ImportRefused, ImportRowError
from hub.shared.ports import Clock

__all__ = ["MeterCsvImporter"]

COLUMNS = frozenset(
    {"serial_number", "site_code", "hardware_model", "firmware_version", "customer_external_ref"}
)


class MeterCsvImporter:
    """``meters.csv``: one row per meter, resolved through site and customer references."""

    kind = "meters"
    columns = COLUMNS
    operation = "createMeter"

    def __init__(
        self,
        *,
        clock: Clock,
        ids: Any,
        meters: MeterStore,
        registry: RegistryFacts,
        audit: AuditLog,
        unit_of_work: Callable[[Principal | None], UnitOfWork],
        resolve_actor: Callable[[str], Principal],
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._meters = meters
        self._registry = registry
        self._audit = audit
        self._unit_of_work = unit_of_work
        self._resolve_actor = resolve_actor

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
                f"{actor_email} does not hold a role that may create meters "
                f"({', '.join(sorted(allowed_roles))})"
            )

        errors: list[ImportRowError] = []
        prepared: list[MeterRow] = []
        serials: set[str] = set()
        skipped = 0

        for offset, row in enumerate(rows):
            row_number = offset + 2
            serial_no = row.get("serial_number", "").strip()
            if not serial_no:
                errors.append(ImportRowError(row_number, "missing_serial_number"))
                continue
            if serial_no in serials:
                errors.append(ImportRowError(row_number, "serial_conflict"))
                continue
            serials.add(serial_no)
            if self._meters.find_by_serial(serial_no) is not None:
                skipped += 1
                continue

            model = row.get("hardware_model", "").strip()
            if not model:
                # `Meter.hardware_model` has no column (Plan notes 3), so the file's model is
                # the only place it is ever seen: a row without one is a row missing data the
                # contract promises a client will receive.
                errors.append(ImportRowError(row_number, "missing_hardware_model"))
                continue

            site = self._registry.site_by_code(row.get("site_code", "").strip())
            if site is None:
                errors.append(ImportRowError(row_number, "unknown_site"))
                continue

            customer_id = None
            reference = row.get("customer_external_ref", "").strip()
            if reference:
                customer = self._registry.customer_by_external_ref(reference)
                if customer is None or customer.status != "active":
                    errors.append(ImportRowError(row_number, "unknown_customer"))
                    continue
                if customer.site_id != site.id:
                    errors.append(ImportRowError(row_number, "site_mismatch"))
                    continue
                customer_id = customer.id

            now = self._clock.now()
            prepared.append(
                MeterRow(
                    id=self._ids.new_id(),
                    serial_no=serial_no,
                    site_id=site.id,
                    gateway_id=None,
                    customer_id=customer_id,
                    status="provisioned",
                    # The contract's `hardware_model` has no column and is kept in the audit
                    # detail of a single create; a bulk load of thousands would drown that
                    # stream, so the file's model is required above but not stored.
                    firmware=_firmware(row.get("firmware_version", "")),
                    token_counter=0,
                    installed_at=None,
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
            for meter in prepared:
                self._meters.add(meter)
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.import.completed",
                entity_type="meter",
                entity_id=None,
                site_id=None,
                details={"kind": self.kind, "inserted": len(prepared), "skipped_existing": skipped},
            )
            del uow
        return ImportOutcome(inserted=len(prepared), skipped_existing=skipped)


def _firmware(raw: str) -> str | None:
    stripped = raw.strip()
    return stripped or None
