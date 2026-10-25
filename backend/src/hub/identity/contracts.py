"""Public surface of `identity`: Protocols, DTOs and domain errors other modules
may use. Nothing else in this package is importable from outside (docs/03 section 0).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from hub.identity.domain.principal import Principal, PrincipalKind

__all__ = [
    "AuditEntry",
    "AuditLog",
    "AuditOutcome",
    "Principal",
    "PrincipalKind",
]

# The three endings of an audited action (api/openapi.yaml, ``AuditEntry.outcome``).
AuditOutcome = Literal["success", "denied", "error"]


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One row of a stream, as the read path returns it (``api/openapi.yaml`` AuditEntry).

    ``hash`` and ``prev_hash`` are hex so a client can display the chain; the first row of a
    stream has no predecessor and carries an empty ``prev_hash``.
    """

    id: UUID
    stream: str
    seq: int
    occurred_at: datetime
    actor_type: str
    actor_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID | None
    outcome: str
    request_id: str | None
    details: Mapping[str, Any]
    hash: str
    prev_hash: str


class AuditLog(Protocol):
    """The append-only, hash-chained audit trail (D-22, docs/03 section 2.2).

    ``record`` writes through ``hub.audit_append`` and must be the last call in a unit of
    work: the advisory lock it takes lives until the transaction commits, which is what makes
    each stream's ``seq`` dense and its chain gap-free. ``page`` reads one stream forward
    through ``hub.audit_page``.
    """

    def record(
        self,
        *,
        stream: str,
        actor: Principal | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        site_id: UUID | None,
        details: Mapping[str, Any],
        outcome: AuditOutcome = "success",
        ip: str | None = None,
        subject_customer_id: UUID | None = None,
    ) -> UUID:
        """Append one row and return its id."""
        ...

    def page(self, stream: str, *, after_seq: int, limit: int) -> Sequence[AuditEntry]:
        """Rows of ``stream`` with ``seq > after_seq``, oldest first."""
        ...
