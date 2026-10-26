"""Public surface of `meters`: Protocols, DTOs and domain errors other modules
may use. Nothing else in this package is importable from outside (docs/03 section 0).

``MeterRegistry`` is the read surface slices 04, 06 and 12 use: a top-up and a token issuance
must refuse a decommissioned meter or one that never belonged to the customer (FR-01,
TC-FR-01-3), and neither may read ``hub.meters`` itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

__all__ = [
    "CustomerFacts",
    "MeterCounts",
    "MeterRef",
    "MeterRegistry",
    "MeterRow",
    "MeterStore",
    "RegistryFacts",
    "SiteFacts",
]


@dataclass(frozen=True, slots=True)
class MeterRow:
    """One row of ``hub.meters``, version included."""

    id: UUID
    serial_no: str
    site_id: UUID
    gateway_id: UUID | None
    customer_id: UUID | None
    status: str
    firmware: str | None
    token_counter: int
    installed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class MeterRef:
    """What another module sees of a meter (``api/openapi.yaml`` Meter, reduced)."""

    id: UUID
    serial_no: str
    site_id: UUID
    customer_id: UUID | None
    status: str
    version: int


class MeterRegistry(Protocol):
    """The meter facts a money or credit path may rely on."""

    def get(self, meter_id: UUID) -> MeterRef: ...

    def assert_usable_for_credit(self, meter_id: UUID, customer_id: UUID) -> None:
        """Raise ``MeterNotUsable("meter_decommissioned" | "meter_not_owned")``."""
        ...


class MeterCounts(Protocol):
    """Meters per site, for the site document's computed ``meter_count``."""

    def per_site(self) -> dict[UUID, int]: ...


@dataclass(frozen=True, slots=True)
class CustomerFacts:
    """The three facts an assignment has to check, and nothing else (FR-01).

    An assignment asks whether the customer exists, whether it may still hold a meter and
    which site it belongs to. Naming exactly those keeps a meter's code from acquiring an
    opinion about a customer's name, phone or plan.
    """

    id: UUID
    site_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class SiteFacts:
    """A site as meters sees it: it exists, and it is the one a meter may be installed at."""

    id: UUID
    status: str


class RegistryFacts(Protocol):
    """What `meters` asks `customers` instead of reading its tables (docs/03 3.1).

    ``hub.customers`` registers the adapter at start-up (``MetersConfig.ready``), so this
    package imports only the Protocol, never the registry's repositories.
    """

    def customer(self, customer_id: UUID) -> CustomerFacts | None: ...

    def site(self, site_id: UUID) -> SiteFacts | None: ...


class MeterStore(Protocol):
    """Persistence of ``hub.meters`` as the registry use cases need it (no SQL here).

    ``lock_for_assign`` is the race breaker: it reads the row ``FOR UPDATE`` inside the
    caller's transaction, so a second operator waits and then sees a ``version`` its
    ``If-Match`` no longer matches (Review focus 1).
    """

    def add(self, meter: MeterRow) -> None: ...

    def get(self, meter_id: UUID) -> MeterRow | None: ...

    def find_by_serial(self, serial_no: str) -> MeterRow | None: ...

    def lock_for_assign(self, meter_id: UUID) -> MeterRow | None: ...

    def update_with_version(self, meter: MeterRow, *, expected_version: int) -> None: ...

    def list_scoped(
        self,
        *,
        site_ids: Sequence[UUID] | None,
        customer_id: UUID | None = None,
        site_id: UUID | None = None,
        statuses: Sequence[str] = (),
        serial_no: str | None = None,
        serial_prefix: str | None = None,
        after_id: UUID | None = None,
        limit: int = 25,
    ) -> list[MeterRow]: ...

    def assignment_history(self, meter_id: UUID) -> list[UUID]: ...
