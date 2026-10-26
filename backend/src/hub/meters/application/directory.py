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
from hub.meters.contracts import CustomerFacts, SiteFacts

__all__ = ["RegistryFactsAdapter"]


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
