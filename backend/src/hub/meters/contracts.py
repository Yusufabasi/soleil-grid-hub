"""Public surface of `meters`: Protocols, DTOs and domain errors other modules
may use. Nothing else in this package is importable from outside (docs/03 section 0).

``MeterRegistry`` is the read surface slices 04, 06 and 12 use: a top-up and a token issuance
must refuse a decommissioned meter or one that never belonged to the customer (FR-01,
TC-FR-01-3), and neither may read ``hub.meters`` itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

__all__ = ["MeterCounts", "MeterRef", "MeterRegistry", "MeterRow"]


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
