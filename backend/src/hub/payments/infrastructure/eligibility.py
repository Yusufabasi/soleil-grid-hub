"""Whether a confirmed payment may still be credited to the meter it was opened for (Task 04.8).

Two facts decide it, and both belong to other modules: whether the customer account still exists
(erase it, and its money must not land in a wallet nobody may read — D-33) and whether the meter
may receive credit at all (a decommissioned meter cannot). This adapter asks the two contracts
and answers with a *reason* instead of raising, because a refusal here is a route to suspense
rather than a failure: raising would leave the provider's money unaccounted for, which is the one
thing the suspense path exists to prevent.

The registry's refusal is read from its *code* (``meter_decommissioned`` / ``meter_not_owned``)
rather than by importing its exception type: the module DAG lets `payments` see
``meters.contracts`` and nothing else of that module, and a domain error is a domain error. A code
that is not one of the two suspense reasons is re-raised: this adapter answers the two questions
it was given, and swallowing anything else would credit money on an answer nobody checked.
"""

from __future__ import annotations

from uuid import UUID

from hub.customers.contracts import CustomerDirectory
from hub.customers.domain.errors import RegistryNotFound
from hub.payments.application.initiate_top_up import MeterStatusLookup
from hub.shared.errors import DomainError, NotFound

__all__ = ["METER_REASONS", "RegistryEligibility"]

# The registry's own refusal codes, which are also the suspense reasons they route to.
METER_REASONS = frozenset({"meter_decommissioned", "meter_not_owned"})


class RegistryEligibility:
    """``EligibilityChecker`` over the customer directory and the meter registry."""

    def __init__(self, *, customers: CustomerDirectory, meters: MeterStatusLookup) -> None:
        self._customers = customers
        self._meters = meters

    def check(self, customer_id: UUID, meter_id: UUID) -> str | None:
        """``None`` when the credit may proceed, else the suspense reason it may not."""
        try:
            customer = self._customers.get(customer_id)
        except RegistryNotFound:
            # A row that is gone: no wallet a person can be shown, so no credit.
            return "erased_customer"
        if customer.status == "erased":
            # Erasure keeps the row (so its history stays referentially sound) and scrubs the
            # person from it. Crediting the wallet behind it would put money in a place no
            # screen may show, which is the reason D-33 exists.
            return "erased_customer"
        try:
            self._meters.assert_usable_for_credit(meter_id, customer_id)
        except NotFound:
            # A meter that is gone cannot receive credit either.
            return "meter_decommissioned"
        except DomainError as exc:
            if exc.code in METER_REASONS:
                return exc.code
            raise
        return None
