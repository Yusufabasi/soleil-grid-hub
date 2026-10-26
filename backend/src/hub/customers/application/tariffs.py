"""Versioned plan tariffs through maker-checker (Task 02.4, D-08).

A tariff is not edited; it is replaced by a version with its own ``effective_from``. That single
decision shapes this module:

* **Proposing writes nothing.** The proposal is a payload in ``hub.approvals`` — plan, price,
  date, reason — and ``GET /plans/{id}`` keeps answering the previous ``current_tariff``. The
  ``plan_tariffs`` row is inserted by the executor, after a *different* finance lead approves,
  which is why a proposal can be rejected without leaving a dead row behind for its date.
* **The cap is the database's.** ``trg_plan_tariffs_guard`` refuses a revision more than 25
  percent away from the version it replaces. The proposal mirrors that arithmetic so the maker
  hears about it immediately, and the adapter maps the trigger's ``check_violation`` to the same
  422 for the race the mirror cannot see. There is exactly one authority for the number.
* **History is never rewritten.** A date that has already passed cannot be proposed, and a
  proposal approved too late (its date went by while it waited) is refused rather than inserted
  retroactively. ``effective_from`` decides when a price becomes current — not the approval
  instant — so ``current_tariff`` can disagree with ``status = 'active'`` for a while, on purpose.

``daily_fixed_fee`` has no column in ``hub.plan_tariffs`` (Plan notes 3): it is validated and
kept in the approval payload, so the decision records the whole price even though only the
per-kWh part is stored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from hub.customers.contracts import (
    ApprovalProposer,
    PlanRow,
    PlanStore,
    PlanTariffRow,
    ProposalReceipt,
    TariffStore,
)
from hub.customers.domain.errors import (
    InvalidTariff,
    RegistryNotFound,
    TariffChangeTooLarge,
)
from hub.identity.contracts import AuditLog, Principal
from hub.shared.contracts import UnitOfWork
from hub.shared.errors import Duplicate
from hub.shared.ports import Clock, IdGenerator

__all__ = [
    "ListTariffs",
    "ProposeTariff",
    "TariffChangeExecutor",
    "TariffService",
]

# The contract's cap per revision (db/schema.sql 3b, D-08). Tunable only by migration.
CAP_PERCENT = 25

KIND = "tariff_change"


@dataclass(frozen=True, slots=True)
class ProposeTariff:
    """``TariffCreate`` of the contract: an amount in the plan's currency and a future date."""

    plan_id: UUID
    price_minor_per_kwh: int
    currency: str
    effective_from: datetime
    reason: str
    daily_fixed_fee_minor: int | None = None
    daily_fixed_fee_currency: str | None = None


@dataclass(frozen=True, slots=True)
class ListTariffs:
    """One page of the history, keyset on ``(effective_from, id)``."""

    plan_id: UUID
    after: tuple[datetime, UUID] | None = None
    limit: int = 25


def _invalid(field: str, code: str, message: str) -> InvalidTariff:
    """A 422 with a JSON pointer, the shape every schema failure of this API uses."""
    return InvalidTariff(details={"errors": [{"field": field, "code": code, "message": message}]})


class TariffService:
    """``listPlanTariffs`` and ``createPlanTariff``."""

    def __init__(
        self,
        *,
        clock: Clock,
        plans: PlanStore,
        tariffs: TariffStore,
        approvals: ApprovalProposer,
    ) -> None:
        self._clock = clock
        self._plans = plans
        self._tariffs = tariffs
        self._approvals = approvals

    def list(
        self, principal: Principal, query: ListTariffs
    ) -> tuple[Sequence[PlanTariffRow], bool]:
        """The history newest first; ``has_next`` says whether another page exists."""
        del principal  # plans are not site-scoped: every caller reads the same history
        self._plan(query.plan_id)
        rows = self._tariffs.page(query.plan_id, after=query.after, limit=query.limit + 1)
        return rows[: query.limit], len(rows) > query.limit

    def propose(self, principal: Principal, command: ProposeTariff) -> ProposalReceipt:
        """Validate the price, store the proposal and answer with the approval to decide."""
        plan = self._plan(command.plan_id)
        now = self._clock.now()
        if command.effective_from <= now:
            raise _invalid(
                "/effective_from",
                "out_of_range",
                "A tariff date must be in the future: a published price is never rewritten.",
            )
        if command.price_minor_per_kwh <= 0:
            raise _invalid(
                "/price_per_kwh/amount_minor", "out_of_range", "The price must be positive."
            )
        if command.currency != plan.currency:
            raise _invalid(
                "/price_per_kwh/currency",
                "mismatch",
                f"The price must be in the plan's currency ({plan.currency}).",
            )
        self._check_daily_fee(command, plan)

        previous = self._tariffs.previous(plan.id, before=command.effective_from)
        if previous is not None and (
            abs(command.price_minor_per_kwh - previous.tariff_minor_per_kwh) * 100
            > previous.tariff_minor_per_kwh * CAP_PERCENT
        ):
            raise TariffChangeTooLarge(
                details={
                    "cap_percent": CAP_PERCENT,
                    "previous_minor_per_kwh": previous.tariff_minor_per_kwh,
                }
            )
        if self._tariffs.has_pending_proposal(
            plan.id, effective_from=command.effective_from, at=now
        ):
            # Two proposals for one instant would collide on the unique index at decision time;
            # refusing the second now is the useful moment to say so.
            raise Duplicate()

        payload: dict[str, Any] = {
            "plan_id": str(plan.id),
            "plan_code": plan.code,
            "currency": plan.currency,
            "tariff_minor_per_kwh": command.price_minor_per_kwh,
            "effective_from": command.effective_from.isoformat(),
            "reason": command.reason,
            "proposed_by": str(principal.subject_id),
        }
        if command.daily_fixed_fee_minor is not None:
            payload["daily_fixed_fee_minor"] = command.daily_fixed_fee_minor
        return self._approvals.propose(principal, kind=KIND, payload=payload, reason=command.reason)

    def _plan(self, plan_id: UUID) -> PlanRow:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise RegistryNotFound()
        return plan

    @staticmethod
    def _check_daily_fee(command: ProposeTariff, plan: PlanRow) -> None:
        if command.daily_fixed_fee_minor is None:
            return
        if command.daily_fixed_fee_minor < 0:
            raise _invalid(
                "/daily_fixed_fee/amount_minor",
                "out_of_range",
                "The daily fee cannot be negative.",
            )
        if command.daily_fixed_fee_currency != plan.currency:
            raise _invalid(
                "/daily_fixed_fee/currency",
                "mismatch",
                f"The daily fee must be in the plan's currency ({plan.currency}).",
            )


class TariffChangeExecutor:
    """``tariff.change``: write the version the second finance lead approved.

    Registered by ``customers.wiring`` on the identity executor registry, so an approval kind
    nobody can execute never exists. ``required_attribute`` is what makes an administrator
    (who holds ``config.manage``) unable to decide a price: four eyes, and one of the pairs
    must be finance.
    """

    kind = KIND
    required_permission = "config.manage"
    required_attribute = "finance_lead"

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        tariffs: TariffStore,
        audit: AuditLog,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._tariffs = tariffs
        self._audit = audit

    def __call__(
        self,
        payload: Mapping[str, Any],
        principal: Principal,
        uow: UnitOfWork,
        *,
        approval_id: UUID,
    ) -> Mapping[str, Any]:
        plan_id = UUID(str(payload["plan_id"]))
        effective_from = datetime.fromisoformat(str(payload["effective_from"]))
        now = self._clock.now()
        if effective_from <= now:
            # The decision came too late: inserting now would put a price in force retroactively.
            raise InvalidTariff(
                details={
                    "errors": [
                        {
                            "field": "/effective_from",
                            "code": "out_of_range",
                            "message": "The date passed while the proposal waited for a decision.",
                        }
                    ]
                }
            )

        proposed_by = payload.get("proposed_by")
        tariff = PlanTariffRow(
            id=self._ids.new_id(),
            plan_id=plan_id,
            tariff_minor_per_kwh=int(payload["tariff_minor_per_kwh"]),
            effective_from=effective_from,
            status="active",
            proposed_by=UUID(str(proposed_by)) if proposed_by is not None else None,
            approval_id=approval_id,
            created_at=now,
        )
        # Retire what this version replaces before it lands: the trigger's cap compares against
        # the row before the new date either way, so the order only matters to the reader.
        self._tariffs.supersede_before(plan_id, before=effective_from)
        self._tariffs.add(tariff)
        self._audit.record(
            stream="registry",
            actor=principal,
            action="registry.tariff.activated",
            entity_type="plan_tariff",
            entity_id=tariff.id,
            site_id=None,
            details={
                "plan_id": str(plan_id),
                "approval_id": str(approval_id),
                "fields": ["tariff_minor_per_kwh", "effective_from"],
            },
        )
        del uow  # the audit call is the last statement, as the chain requires
        return {"plan_tariff_id": str(tariff.id)}
