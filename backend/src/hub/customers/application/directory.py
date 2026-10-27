"""`CustomerDirectoryImpl`: the read surface neighbours use instead of the registry's tables.

`docs/03` gives every module one way to read a customer, a plan or a meter it does not own, and
this is the registry's side of that contract (the slice's Interfaces block). Four rules shape
it:

* **A tariff is chosen by instant.** ``quote_energy`` and ``current_plan`` ask
  `hub.current_tariff` for the version in force *at the given moment* (D-08); a newer row whose
  ``effective_from`` has not arrived is invisible, and a moment before every version is a
  refusal rather than a guess of zero.
* **A quote floors.** ``energy_wh = amount_minor * 1000 // tariff_minor_per_kwh`` in integer
  arithmetic: the hub grants whole watt-hours it was paid for and drops the remainder, never
  rounding a customer up into free energy.
* **The meter link is a registration.** The rows are `hub.meters`', and this module may not
  import that one, so ``MeterLink`` is installed by ``MetersConfig.ready``; the answers are
  ``None`` when there is no meter or no owner, because "none" is a real answer to the callers'
  question.
* **A sign-in is identity's fact.** The one field identity needs for the dormancy rule arrives
  through ``SignInProbe``, so this module reads no session rows.

The class also answers identity's ``CustomerLookup`` — the one-time-code flow resolves a call
to a customer through it — which is why the same object is registered with ``identity.wiring``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from hub.customers.application.customers import customer_view
from hub.customers.contracts import (
    CustomerRow,
    CustomerStore,
    CustomerView,
    EnergyQuote,
    MeterLink,
    PlanRow,
    PlanStore,
    PlanView,
    SignInProbe,
)
from hub.customers.domain.errors import InvalidTariff, RegistryNotFound
from hub.identity.contracts import CustomerRecord
from hub.shared.ports import Clock

__all__ = ["CustomerDirectoryImpl"]


class CustomerDirectoryImpl:
    """``customers.contracts.CustomerDirectory`` + identity's ``CustomerLookup``, over the stores."""

    def __init__(
        self,
        *,
        clock: Clock,
        customers: CustomerStore,
        plans: PlanStore,
        meters: MeterLink,
        sign_ins: SignInProbe,
    ) -> None:
        self._clock = clock
        self._customers = customers
        self._plans = plans
        self._meters = meters
        self._sign_ins = sign_ins

    # --- customers -----------------------------------------------------------------------
    def get(self, customer_id: UUID) -> CustomerView:
        return customer_view(self._row(customer_id))

    def current_plan(self, customer_id: UUID) -> PlanView:
        """The plan the customer is on, with the tariff in force at the process clock."""
        row = self._row(customer_id)
        plan = self._plans.get(row.plan_id)
        if plan is None:
            raise RegistryNotFound()
        return self._plan_view(plan, at=self._clock.now())

    def quote_energy(self, customer_id: UUID, amount_minor: int, *, at: datetime) -> EnergyQuote:
        """What ``amount_minor`` buys at the tariff in force ``at``: whole watt-hours, floored."""
        row = self._row(customer_id)
        tariff = self._plans.current_tariff(row.plan_id, at=at)
        if tariff is None:
            # No price in force is a refusal: quoting zero would sell energy for nothing.
            raise InvalidTariff(
                details={
                    "errors": [
                        {
                            "field": "/plan_id",
                            "code": "no_tariff_in_force",
                            "message": "The plan has no tariff in force at that instant.",
                        }
                    ]
                }
            )
        return EnergyQuote(
            plan_tariff_id=tariff.id,
            tariff_minor_per_kwh=tariff.tariff_minor_per_kwh,
            # Integer floor, in minor units: 1 minor at 1,000/kWh is exactly 1 Wh.
            energy_wh=amount_minor * 1000 // tariff.tariff_minor_per_kwh,
        )

    # --- meters --------------------------------------------------------------------------
    def meter_for_customer(self, customer_id: UUID) -> UUID | None:
        return self._meters.meter_for_customer(customer_id)

    def customer_for_meter(self, meter_id: UUID) -> UUID | None:
        return self._meters.customer_for_meter(meter_id)

    # --- identity's CustomerLookup --------------------------------------------------------
    def find_active_by_phone(self, phone_e164: str) -> CustomerRecord | None:
        """An active, non-deleted customer holding this number; a closed or suspended one is none."""
        row = self._customers.find_by_phone(phone_e164)
        return self._sign_in_record(row)

    def by_id(self, customer_id: UUID) -> CustomerRecord | None:
        return self._sign_in_record(self._customers.get(customer_id))

    # --- helpers --------------------------------------------------------------------------
    def _row(self, customer_id: UUID) -> CustomerRow:
        row = self._customers.get(customer_id)
        if row is None:
            raise RegistryNotFound()
        return row

    def _sign_in_record(self, row: CustomerRow | None) -> CustomerRecord | None:
        if row is None or row.status != "active":
            return None
        return CustomerRecord(
            id=row.id,
            locale=row.language,
            last_login_at=self._sign_ins.last_sign_in(row.id),
        )

    def _plan_view(self, plan: PlanRow, *, at: datetime) -> PlanView:
        """The plan document, with the tariff version in force ``at`` and nothing newer.

        The tariff fields stay ``None`` until a version has arrived, which is the honest
        reading of a plan that has never been priced.
        """
        tariff = self._plans.current_tariff(plan.id, at=at)
        return PlanView(
            id=plan.id,
            code=plan.code,
            name=plan.name,
            currency=plan.currency,
            min_topup_minor=plan.min_topup_minor,
            max_topup_minor=plan.max_topup_minor,
            status=plan.status,
            tariff_minor_per_kwh=tariff.tariff_minor_per_kwh if tariff else None,
            plan_tariff_id=tariff.id if tariff else None,
            effective_from=tariff.effective_from if tariff else None,
        )
