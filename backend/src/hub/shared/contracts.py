"""The kernel's contracts: Protocols and DTOs a use case may depend on (docs/03 2.1).

They live apart from ``hub.shared.uow`` on purpose. That module is the Django adapter, and
the architecture contract "Application does not depend on outer layers or frameworks" counts
indirect imports: a use case that imported ``UnitOfWork`` from the adapter would drag Django
and psycopg into the application layer with it.

The adapter imports these and re-exports them, so ``hub.shared.uow`` still answers to its old
names; new code should import the contract from here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from hub.shared.events import DomainEvent

__all__ = ["JobQueue", "KeyValueStore", "OutboxWriter", "PrincipalContext", "UnitOfWork"]


@dataclass(frozen=True)
class PrincipalContext:
    """Who the transaction acts for: a staff user, a customer, or neither (system work)."""

    user_id: UUID | None = None
    customer_id: UUID | None = None


class KeyValueStore(Protocol):
    """Short-lived, self-expiring facts (Redis): challenges, counters, cached token versions.

    Nothing here is authoritative. Every value has a TTL and every consumer treats a miss as
    "ask the database" (a token version) or "refuse" (a challenge), so losing Redis degrades
    the API rather than corrupting it.
    """

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None: ...

    def delete(self, key: str) -> None: ...

    def incr(self, key: str, *, ttl_seconds: int | None = None) -> int: ...

    def add_once(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """Set ``key`` only if absent; True when this call created it (SET NX EX)."""
        ...

    def pop(self, key: str) -> str | None:
        """Return the value and delete the key in one round trip (GETDEL)."""
        ...


class OutboxWriter(Protocol):
    """Stages an event in the caller's transaction (docs/03 2.1)."""

    def add(self, event: DomainEvent) -> None: ...


class JobQueue(Protocol):
    """Hands a job to whatever runs jobs later (slice 05 owns the consumer).

    ``dedupe_key`` is what makes a retried request one job instead of two: the same key must
    resolve to the same stored row, so a caller that retries after a timeout does not double
    the work. A job is durable before this returns — a 202 that the queue forgot is a lie.
    """

    def enqueue(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        dedupe_key: str | None = None,
    ) -> UUID: ...


class UnitOfWork(Protocol):
    """The transactional boundary of one use case (docs/03 2.1)."""

    outbox: OutboxWriter

    def __enter__(self) -> UnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def savepoint(self) -> AbstractContextManager[None]: ...

    def require_active(self) -> None: ...

    def on_commit(self, fn: Callable[[], None]) -> None: ...
