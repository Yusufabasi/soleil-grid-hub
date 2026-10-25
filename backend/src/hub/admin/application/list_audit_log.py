"""Reading the audit log, one stream at a time (D-22, docs/03 section 2.14).

``hub.audit_page`` is the only read path the schema allows, and it is forward-only: it returns
rows with ``seq > after_seq``, oldest first. That shape decides the rest of this use case —
the page is a window on one stream, the cursor is a sequence number, and the filters
(``action``, ``actor``, ``entity``, ``outcome``, time window) are applied here because the
function cannot push them down.

Reading is itself an audited action: a successful page appends ``audit.read`` to the
``admin`` stream in the same transaction, so "who looked, at what, and found nothing" is
answerable. The append is the last statement of the transaction, as ``AuditLog.record``
requires.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from hub.identity.contracts import AuditEntry, AuditLog, Principal
from hub.shared.contracts import UnitOfWork

__all__ = ["AUDIT_READ_ACTION", "AUDIT_READ_STREAM", "AuditPage", "AuditQuery", "ListAuditLog"]

# The stream the administrator's own reads are recorded on, and the action they are recorded
# under (the schema's streams are auth, money, admin, registry, privacy).
AUDIT_READ_STREAM = "admin"
AUDIT_READ_ACTION = "audit.read"


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """One requested page: which stream, where to resume, and the filters on top."""

    stream: str
    after_seq: int = 0
    limit: int = 25
    action: str | None = None
    actor_id: UUID | None = None
    entity_type: str | None = None
    entity_id: UUID | None = None
    outcomes: frozenset[str] = field(default_factory=frozenset)
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditPage:
    """One page and where the next one starts; ``next_after_seq`` is None on the last page."""

    entries: Sequence[AuditEntry]
    next_after_seq: int | None


class ListAuditLog:
    """List one stream of the chain, and record that it was listed."""

    def __init__(
        self,
        *,
        audit_log: AuditLog,
        unit_of_work: Callable[[Principal | None], UnitOfWork],
    ) -> None:
        self._audit_log = audit_log
        self._unit_of_work = unit_of_work

    def run(self, actor: Principal, query: AuditQuery) -> AuditPage:
        """Read the page inside a transaction that announces ``actor`` and then audit it."""
        # The transaction has to name the actor before the first statement: `audit_page`
        # resolves `audit.read` from `app.user_id` (D-22, docs/04 10.9).
        with self._unit_of_work(actor):
            page = self._scan(query)
            self._audit_log.record(
                stream=AUDIT_READ_STREAM,
                actor=actor,
                action=AUDIT_READ_ACTION,
                entity_type="audit_log",
                entity_id=None,
                site_id=None,
                details=self._read_details(query, page),
            )
        return page

    # --- internals ----------------------------------------------------------------------
    def _scan(self, query: AuditQuery) -> AuditPage:
        """Walk forward until ``limit`` matching rows are found or the stream ends."""
        matches: list[AuditEntry] = []
        cursor = query.after_seq
        while True:
            batch = self._audit_log.page(query.stream, after_seq=cursor, limit=query.limit + 1)
            if not batch:
                return AuditPage(entries=matches, next_after_seq=None)
            for entry in batch:
                cursor = entry.seq
                if _matches(entry, query):
                    matches.append(entry)
                    if len(matches) > query.limit:
                        # The extra row proves there is another page; the cursor goes back to
                        # the last returned row so the next page starts with that one.
                        return AuditPage(
                            entries=matches[: query.limit],
                            next_after_seq=matches[query.limit - 1].seq,
                        )

    @staticmethod
    def _read_details(query: AuditQuery, page: AuditPage) -> dict[str, Any]:
        """What the `audit.read` row remembers: the stream, the window and the filters."""
        filters: dict[str, Any] = {
            name: value
            for name, value in (
                ("action", query.action),
                ("actor_id", str(query.actor_id) if query.actor_id else None),
                ("entity_type", query.entity_type),
                ("entity_id", str(query.entity_id) if query.entity_id else None),
                ("outcome", sorted(query.outcomes) or None),
                (
                    "occurred_after",
                    query.occurred_after.isoformat() if query.occurred_after else None,
                ),
                (
                    "occurred_before",
                    query.occurred_before.isoformat() if query.occurred_before else None,
                ),
            )
            if value is not None
        }
        return {
            "stream": query.stream,
            "after_seq": query.after_seq,
            "limit": query.limit,
            "returned": len(page.entries),
            "filters": filters,
        }


def _matches(entry: AuditEntry, query: AuditQuery) -> bool:
    """Every filter must hold; each one is either absent or an exact match."""
    if query.action is not None and not _action_matches(entry.action, query.action):
        return False
    if query.actor_id is not None and entry.actor_id != query.actor_id:
        return False
    if query.entity_type is not None and entry.entity_type != query.entity_type:
        return False
    if query.entity_id is not None and entry.entity_id != query.entity_id:
        return False
    if query.outcomes and entry.outcome not in query.outcomes:
        return False
    if query.occurred_after is not None and entry.occurred_at < query.occurred_after:
        return False
    return not (query.occurred_before is not None and entry.occurred_at > query.occurred_before)


def _action_matches(action: str, wanted: str) -> bool:
    """Exact event name, or a prefix when the filter ends in ``*`` (api/openapi.yaml)."""
    if wanted.endswith("*"):
        return action.startswith(wanted[:-1])
    return action == wanted
