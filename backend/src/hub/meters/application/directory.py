"""The registry facts an assignment needs, read through `customers`' own contracts.

``meters`` may import ``hub.customers.contracts`` and nothing else of that module (docs/03
3.1), so this adapter speaks to the two store Protocols the registry publishes. The direction
is deliberate: ``hub.customers`` is *not* allowed to import ``hub.meters`` at all, which rules
out putting this adapter on the registry's side of the boundary.

Every call is the repository's, so a customer suspended a moment ago is refused a meter now;
nothing is cached here, and nothing of the customer's name, phone or plan crosses the boundary.
"""

from __future__ import annotations

from uuid import UUID

from hub.customers.contracts import CustomerStore, SiteStore
from hub.meters.contracts import CustomerFacts, MeterStore, SiteFacts

__all__ = ["MeterLinkAdapter", "RegistryFactsAdapter"]


class RegistryFactsAdapter:
    """``meters.contracts.RegistryFacts`` over the registry's stores."""

    def __init__(self, *, customers: CustomerStore, sites: SiteStore) -> None:
        self._customers = customers
        self._sites = sites

    def customer(self, customer_id: UUID) -> CustomerFacts | None:
        row = self._customers.get(customer_id)
        if row is None:
            return None
        return CustomerFacts(id=row.id, site_id=row.site_id, status=row.status)

    def site(self, site_id: UUID) -> SiteFacts | None:
        row = self._sites.get(site_id)
        if row is None:
            return None
        return SiteFacts(id=row.id, status=row.status)

    def site_by_code(self, code: str) -> SiteFacts | None:
        """A site as a bulk file names it: stable, human-chosen, and unique."""
        row = self._sites.find_by_code(code) if code else None
        if row is None:
            return None
        return SiteFacts(id=row.id, status=row.status)

    def customer_by_external_ref(self, external_ref: str) -> CustomerFacts | None:
        """The registry's idempotency key, which is how a meter file names its owner."""
        row = self._customers.find_by_external_ref(external_ref) if external_ref else None
        if row is None:
            return None
        return CustomerFacts(id=row.id, site_id=row.site_id, status=row.status)


class MeterLinkAdapter:
    """``customers.contracts.MeterLink`` over the meter rows (Task 02.9).

    The registry needs the customer-to-meter link (``docs/03`` 2.3 lists it among the things
    ``customers`` owns), but the rows are this module's and the module DAG runs only one way,
    so ``customers`` cannot read them itself. This adapter is the answer, installed by
    ``MetersConfig.ready`` through ``customers.wiring.use_meter_link``.

    Both directions read at call time and answer ``None`` rather than raising: an unassigned
    meter and a customer with no meter are ordinary states, not errors, and every caller
    (top-ups, token issuance) asks a question whose honest answer is sometimes "none".
    """

    def __init__(self, meters: MeterStore) -> None:
        self._meters = meters

    def meter_for_customer(self, customer_id: UUID) -> UUID | None:
        """The customer's active meter, if any: v1 allows one per customer (Plan notes 5)."""
        rows = self._meters.list_scoped(site_ids=None, customer_id=customer_id, limit=1)
        return rows[0].id if rows else None

    def customer_for_meter(self, meter_id: UUID) -> UUID | None:
        meter = self._meters.get(meter_id)
        return meter.customer_id if meter is not None else None
