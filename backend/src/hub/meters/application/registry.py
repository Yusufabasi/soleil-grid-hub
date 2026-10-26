"""The meter registry (FR-01): provisioning, reassignment and decommissioning.

Deliberate decisions, all of them testable:

* **Scope is silence.** A meter outside the caller's ``user_sites`` answers 404, and a portal
  token sees only the meter whose ``customer_id`` is its own subject. The refusal is written to
  the ``registry`` stream because a client that only ever sees "not found" leaves no other trace
  of the attempt (S-8).
* **Assignment is a compare-and-set.** The meter is locked in the row for the length of the
  transaction and the ``UPDATE`` carries ``version = <the If-Match we were given>``; a second
  operator who passed the same check loses with 409 rather than writing a second assignment.
* **The history is the trigger's.** ``trg_meters_assignment`` records every pair that ever
  existed, so a reassignment or an unassignment can never lose the fact that a past customer
  held this meter — a top-up or a token keeps a composite key that proves it.
* **A meter's life is one-way.** ``decommissioned`` is final, and leaving it is refused with
  409 ``invalid_state_transition``; decommissioning itself is refused while credit is on the
  meter, because a meter with energy left on it is not ours to retire.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol
from uuid import UUID

from hub.identity.contracts import AuditLog, Principal
from hub.meters.contracts import CustomerFacts, MeterRow, MeterStore, RegistryFacts
from hub.meters.domain.errors import (
    InvalidMeterState,
    MeterCreditOutstanding,
    MeterSerialConflict,
    MeterUnknown,
    SiteMismatch,
    VersionConflict,
)
from hub.meters.events import meter_assigned
from hub.shared.contracts import UnitOfWork
from hub.shared.errors import ValidationFailed
from hub.shared.ports import Clock

__all__ = [
    "DecommissionMeter",
    "GetMeter",
    "ListCustomerMeters",
    "ListMeters",
    "MeterInput",
    "MeterQuery",
    "MeterService",
    "RegisterMeter",
    "UpdateMeter",
]

UnitOfWorkFactory = Callable[[Principal | None], UnitOfWork]

# The lifecycle of `hub.meters.status` (ck_meters_status). `provisioned` is only ever the
# starting point: a meter is activated, never re-provisioned.
METER_STATUSES = ("provisioned", "active", "offline", "disconnected", "decommissioned")
_UPDATABLE_STATUSES = ("active", "offline", "disconnected", "decommissioned")
_TRANSITIONS = {
    "provisioned": {"active", "offline", "disconnected", "decommissioned"},
    "active": {"offline", "disconnected", "decommissioned"},
    "offline": {"active", "disconnected", "decommissioned"},
    "disconnected": {"active", "offline", "decommissioned"},
    "decommissioned": set(),
}
# The one value of the assignment sentinel: `customer_id: null` means "unassign", while an
# absent field means "leave the owner alone", and the two must not be confused.
UNASSIGNED = "unassigned"


class CreditProbe(Protocol):
    """Energy left on the meter; slice 06 binds the token ledger, 0 until then."""

    def credit_wh(self, meter_id: UUID) -> int: ...


@dataclass(frozen=True, slots=True)
class MeterInput:
    """The mutable half of a meter: what a create carries."""

    serial_number: str
    site_id: UUID
    hardware_model: str
    customer_id: UUID | None = None
    firmware: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterMeter:
    meter: MeterInput


@dataclass(frozen=True, slots=True)
class UpdateMeter:
    """A reassignment, a status move or a firmware change — each field optional.

    ``expected_version`` is the ``If-Match`` the caller presented, already checked against the
    row it read; the repository compares it again inside the transaction, which is what turns a
    lost race into 409 ``version_conflict``.
    """

    meter_id: UUID
    expected_version: int
    customer_id: UUID | None = None
    customer_supplied: bool = False
    site_id: UUID | None = None
    status: str | None = None
    firmware: str | None = None
    firmware_supplied: bool = False


@dataclass(frozen=True, slots=True)
class DecommissionMeter:
    meter_id: UUID


@dataclass(frozen=True, slots=True)
class GetMeter:
    meter_id: UUID


@dataclass(frozen=True, slots=True)
class MeterQuery:
    """The filters ``listMeters`` accepts, already parsed by the view."""

    statuses: Sequence[str] = ()
    site_id: UUID | None = None
    customer_id: UUID | None = None
    serial_no: str | None = None
    q: str | None = None
    after_id: UUID | None = None
    limit: int = 25


@dataclass(frozen=True, slots=True)
class ListMeters:
    query: MeterQuery


@dataclass(frozen=True, slots=True)
class ListCustomerMeters:
    customer_id: UUID
    query: MeterQuery


class MeterService:
    """`createMeter`, `getMeter`, `updateMeter`, `decommissionMeter` and the two lists."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: Any,
        meters: MeterStore,
        registry: RegistryFacts,
        credit: CreditProbe,
        audit: AuditLog,
        unit_of_work: UnitOfWorkFactory,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._meters = meters
        self._registry = registry
        self._credit = credit
        self._audit = audit
        self._unit_of_work = unit_of_work

    # --- scope ---------------------------------------------------------------------------
    def _scope(self, principal: Principal) -> list[UUID] | None:
        return None if principal.all_sites else sorted(principal.site_ids)

    def _require_site(self, principal: Principal, site_id: UUID) -> None:
        scope = self._scope(principal)
        if scope is not None and site_id not in set(scope):
            raise MeterUnknown()

    def _denied(self, principal: Principal, meter: MeterRow) -> None:
        self._audit.record(
            stream="registry",
            actor=principal,
            action="registry.meter.denied",
            entity_type="meter",
            entity_id=meter.id,
            site_id=meter.site_id,
            details={"reason": "site_scope"},
            outcome="denied",
        )

    def _visible(self, principal: Principal, meter_id: UUID) -> MeterRow:
        """The row, or 404 — a meter outside the caller's sites does not exist for them.

        A portal token may only name the meter it serves: the claim is the scope, not the id in
        the URL. A staff read outside ``user_sites`` is refused *and* recorded.
        """
        meter = self._meters.get(meter_id)
        if meter is None:
            raise MeterUnknown()
        if principal.kind == "customer":
            if meter.customer_id != principal.customer_id:
                raise MeterUnknown()
            return meter
        scope = self._scope(principal)
        if scope is not None and meter.site_id not in set(scope):
            self._denied(principal, meter)
            raise MeterUnknown()
        return meter

    # --- reads ---------------------------------------------------------------------------
    def get(self, principal: Principal, query: GetMeter) -> MeterRow:
        return self._visible(principal, query.meter_id)

    def list(
        self, principal: Principal, command: ListMeters
    ) -> tuple[Sequence[MeterRow], UUID | None]:
        query = command.query
        customer_id = query.customer_id
        if principal.kind == "customer":
            if customer_id is not None and customer_id != principal.customer_id:
                raise MeterUnknown()
            customer_id = principal.customer_id
        rows = self._meters.list_scoped(
            # A portal token's scope is its own customer id, not a site list: it holds no
            # `user_sites` row, and an empty site list would match nothing at all.
            site_ids=None if principal.kind == "customer" else self._scope(principal),
            customer_id=customer_id,
            site_id=query.site_id,
            statuses=query.statuses,
            serial_no=query.serial_no,
            serial_prefix=query.q,
            after_id=query.after_id,
            limit=query.limit + 1,
        )
        page = rows[: query.limit]
        next_cursor = page[-1].id if len(rows) > query.limit else None
        return page, next_cursor

    def list_for_customer(
        self, principal: Principal, command: ListCustomerMeters
    ) -> tuple[Sequence[MeterRow], UUID | None]:
        """``listCustomerMeters``: the customer document gates the list, then the meters."""
        facts = self._customer(principal, command.customer_id)
        rows = self._meters.list_scoped(
            site_ids=[facts.site_id],
            customer_id=command.customer_id,
            statuses=command.query.statuses,
            after_id=command.query.after_id,
            limit=command.query.limit + 1,
        )
        page = rows[: command.query.limit]
        next_cursor = page[-1].id if len(rows) > command.query.limit else None
        return page, next_cursor

    # --- commands ------------------------------------------------------------------------
    def register(self, principal: Principal, command: RegisterMeter) -> MeterRow:
        meter_input = command.meter
        self._require_site(principal, meter_input.site_id)
        if self._registry.site(meter_input.site_id) is None:
            raise MeterUnknown()
        serial_no = _serial(meter_input.serial_number)
        if self._meters.find_by_serial(serial_no) is not None:
            raise MeterSerialConflict()
        if meter_input.customer_id is not None:
            self._require_customer(meter_input.customer_id, meter_input.site_id)

        now = self._clock.now()
        meter = MeterRow(
            id=self._ids.new_id(),
            serial_no=serial_no,
            site_id=meter_input.site_id,
            gateway_id=None,
            customer_id=meter_input.customer_id,
            status="provisioned",
            firmware=_firmware(meter_input.firmware),
            token_counter=0,
            installed_at=None,
            created_at=now,
            updated_at=now,
            version=1,
        )
        with self._unit_of_work(principal) as uow:
            self._meters.add(meter)
            if meter.customer_id is not None:
                uow.outbox.add(
                    meter_assigned(
                        ids=self._ids,
                        occurred_at=now,
                        meter_id=meter.id,
                        payload={
                            "meter_id": str(meter.id),
                            "site_id": str(meter.site_id),
                            "customer_id": str(meter.customer_id),
                            "previous_customer_id": None,
                        },
                    )
                )
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.meter.created",
                entity_type="meter",
                entity_id=meter.id,
                site_id=meter.site_id,
                # The contract asks for a model the schema has no column for (Plan notes 3):
                # it is kept here, where a support call can find it, and the document says
                # "unknown" rather than inventing one.
                details={
                    "hardware_model": meter_input.hardware_model,
                    "fields": ["serial_no", "site_id", "firmware"],
                },
            )
        return meter

    def update(self, principal: Principal, command: UpdateMeter) -> MeterRow:
        current = self._visible(principal, command.meter_id)
        # `decommissioned` is final (the slice's lifecycle): no field of a retired meter moves,
        # not even its firmware, so an operator cannot quietly reactivate one field at a time.
        if current.status == "decommissioned":
            raise InvalidMeterState()
        if command.site_id is not None and command.site_id != current.site_id:
            self._require_site(principal, command.site_id)
            if self._registry.site(command.site_id) is None:
                raise MeterUnknown()
        site_id = command.site_id or current.site_id

        customer_id = current.customer_id
        if command.customer_supplied:
            customer_id = command.customer_id
            if customer_id is not None:
                self._require_customer(customer_id, site_id)
        elif site_id != current.site_id and current.customer_id is not None:
            # Moving a meter to another site would leave its owner on the old one.
            raise SiteMismatch(
                details={
                    "errors": [
                        {
                            "field": "/customer_id",
                            "code": "site_mismatch",
                            "message": "Unassign the meter before moving it to another site.",
                        }
                    ]
                }
            )

        status = command.status if command.status is not None else current.status
        if command.status is not None:
            if command.status not in _UPDATABLE_STATUSES:
                raise ValidationFailed(
                    details={
                        "errors": [
                            {
                                "field": "/status",
                                "code": "invalid_enum",
                                "message": "Only active, offline, disconnected and "
                                "decommissioned may be set.",
                            }
                        ]
                    }
                )
            if status != current.status:
                if current.status == "decommissioned":
                    raise InvalidMeterState()
                if status not in _TRANSITIONS.get(current.status, set()):
                    raise InvalidMeterState()
            if status == "decommissioned" and current.status != "decommissioned":
                self._assert_no_credit(current)

        now = self._clock.now()
        updated = replace(
            current,
            site_id=site_id,
            customer_id=customer_id,
            status=status,
            firmware=_firmware(command.firmware) if command.firmware_supplied else current.firmware,
            # A meter that first goes live is installed now; a later status change keeps the
            # original instant, because it is the same physical installation.
            installed_at=now
            if current.installed_at is None and status == "active"
            else current.installed_at,
            updated_at=now,
        )
        changed = [
            field
            for field, supplied in (
                ("customer_id", command.customer_supplied),
                ("site_id", command.site_id is not None and command.site_id != current.site_id),
                ("status", status != current.status),
                ("firmware", command.firmware_supplied),
            )
            if supplied
        ]
        with self._unit_of_work(principal) as uow:
            locked = self._meters.lock_for_assign(current.id)
            if locked is None:
                raise MeterUnknown()
            if locked.version != command.expected_version:
                # Somebody wrote between the read and the lock: the `If-Match` is stale after
                # all, and the statement must not run with a version that no longer exists.
                raise VersionConflict()
            self._meters.update_with_version(updated, expected_version=locked.version)
            if customer_id != current.customer_id:
                uow.outbox.add(
                    meter_assigned(
                        ids=self._ids,
                        occurred_at=now,
                        meter_id=updated.id,
                        payload={
                            "meter_id": str(updated.id),
                            "site_id": str(updated.site_id),
                            "customer_id": str(customer_id) if customer_id is not None else None,
                            "previous_customer_id": (
                                str(current.customer_id)
                                if current.customer_id is not None
                                else None
                            ),
                        },
                    )
                )
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.meter.updated",
                entity_type="meter",
                entity_id=updated.id,
                site_id=updated.site_id,
                details={"fields": changed},
            )
        stored = self._meters.get(updated.id)
        return stored if stored is not None else updated

    def decommission(self, principal: Principal, command: DecommissionMeter) -> None:
        current = self._visible(principal, command.meter_id)
        if current.status == "decommissioned":
            raise InvalidMeterState()
        self._assert_no_credit(current)
        now = self._clock.now()
        with self._unit_of_work(principal):
            locked = self._meters.lock_for_assign(current.id)
            if locked is None:
                raise MeterUnknown()
            if locked.status == "decommissioned":
                raise InvalidMeterState()
            self._meters.update_with_version(
                replace(locked, status="decommissioned", updated_at=now),
                expected_version=locked.version,
            )
            self._audit.record(
                stream="registry",
                actor=principal,
                action="registry.meter.decommissioned",
                entity_type="meter",
                entity_id=locked.id,
                site_id=locked.site_id,
                details={"fields": ["status"]},
            )

    # --- shared checks -------------------------------------------------------------------
    def _customer(self, principal: Principal, customer_id: UUID) -> CustomerFacts:
        """The customer this operation names, or 404 when it is not the caller's to see."""
        if principal.kind == "customer" and customer_id != principal.customer_id:
            raise MeterUnknown()
        facts = self._registry.customer(customer_id)
        if facts is None:
            raise MeterUnknown()
        if principal.kind != "customer":
            self._require_site(principal, facts.site_id)
        return facts

    def _require_customer(self, customer_id: UUID, site_id: UUID) -> CustomerFacts:
        facts = self._registry.customer(customer_id)
        if facts is None or facts.status != "active":
            raise ValidationFailed(
                details={
                    "errors": [
                        {
                            "field": "/customer_id",
                            "code": "unknown",
                            "message": "No active customer with that id.",
                        }
                    ]
                }
            )
        if facts.site_id != site_id:
            raise SiteMismatch(
                details={
                    "errors": [
                        {
                            "field": "/customer_id",
                            "code": "site_mismatch",
                            "message": "The customer belongs to another site than the meter.",
                        }
                    ]
                }
            )
        return facts

    def _assert_no_credit(self, meter: MeterRow) -> None:
        if self._credit.credit_wh(meter.id) > 0:
            raise MeterCreditOutstanding()


def _serial(raw: str) -> str:
    serial_no = raw.strip()
    if not serial_no:
        raise ValidationFailed(
            details={
                "errors": [
                    {"field": "/serial_number", "code": "blank", "message": "A serial is required."}
                ]
            }
        )
    return serial_no


def _firmware(raw: str | None) -> str | None:
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None
