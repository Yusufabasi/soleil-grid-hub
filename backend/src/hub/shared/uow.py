"""The unit of work and the outbox writer (docs/03 2.1, docs/04 10.9).

A use case opens ``with uow:`` exactly once per transaction boundary and never calls
``transaction.atomic`` itself. The outbox is staged in memory by ``uow.outbox.add`` and
written as one parameterised statement *inside the still-open transaction* at a clean exit
of the outermost block, so the business row and its event are committed together or not at
all. ``savepoint()`` gives a use case a partial-failure boundary whose staged events are
discarded with the savepoint.

The principal is set with ``set_config(..., true)``: transaction-local, which is what makes
it safe under PgBouncer's transaction pooling (docs/04 10.9). A second statement on the same
pooled server connection cannot see the previous request's identity.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from django.db import connections, transaction
from django.db.transaction import Atomic
from psycopg.types.json import Jsonb

from hub.shared.events import DomainEvent

__all__ = [
    "DjangoUnitOfWork",
    "OutboxWriter",
    "PrincipalContext",
    "UnitOfWork",
]


@dataclass(frozen=True)
class PrincipalContext:
    """Who the transaction acts for: a staff user, a customer, or neither (system work)."""

    user_id: UUID | None = None
    customer_id: UUID | None = None


class OutboxWriter(Protocol):
    """Stages an event in the caller's transaction (docs/03 2.1)."""

    def add(self, event: DomainEvent) -> None: ...


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


class _Outbox:
    """The ``uow.outbox`` attribute: staging only, never a write of its own."""

    def __init__(self, uow: DjangoUnitOfWork) -> None:
        self._uow = uow

    def add(self, event: DomainEvent) -> None:
        self._uow.stage(event)


class DjangoUnitOfWork:
    """One ``transaction.atomic`` block plus the events staged inside it."""

    def __init__(
        self, *, using: str = "default", principal: PrincipalContext | None = None
    ) -> None:
        self._using = using
        self._principal = principal
        self._atomic: Atomic | None = None
        # One frame per open savepoint; the first frame belongs to the transaction itself.
        self._frames: list[list[DomainEvent]] = []
        self.outbox: OutboxWriter = _Outbox(self)

    # --- lifecycle ----------------------------------------------------------------------
    def __enter__(self) -> DjangoUnitOfWork:
        if self._atomic is not None:
            raise RuntimeError("nested units of work use savepoint(), not a second with block")
        atomic = transaction.atomic(using=self._using)
        atomic.__enter__()
        self._atomic = atomic
        self._frames = [[]]
        try:
            self._set_principal()
        except BaseException as exc:
            self._atomic = None
            self._frames = []
            atomic.__exit__(type(exc), exc, exc.__traceback__)
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        atomic = self._atomic
        if atomic is None:
            raise RuntimeError("unit of work exited without being entered")
        frames = self._frames
        self._atomic = None
        self._frames = []
        try:
            if exc_type is None:
                self._flush(frames[0])
        except BaseException as failure:
            atomic.__exit__(type(failure), failure, failure.__traceback__)
            raise
        atomic.__exit__(exc_type, exc, tb)

    # --- API ----------------------------------------------------------------------------
    def require_active(self) -> None:
        """Raise unless a transaction is open: repositories call it before locking rows."""
        if self._atomic is None:
            raise RuntimeError("no active unit of work: wrap the use case in `with uow:`")

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """A nested boundary: staged events of a failed block are discarded with it."""
        self.require_active()
        frame: list[DomainEvent] = []
        self._frames.append(frame)
        try:
            with transaction.atomic(using=self._using):
                yield
        except BaseException:
            raise
        else:
            self._frames[-2].extend(frame)
        finally:
            self._frames.pop()

    def on_commit(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` after this transaction commits — never after a rollback."""
        self.require_active()
        transaction.on_commit(fn, using=self._using)

    def stage(self, event: DomainEvent) -> None:
        """Append an event to the innermost open frame (what ``uow.outbox.add`` does)."""
        self.require_active()
        self._frames[-1].append(event)

    # --- internals ----------------------------------------------------------------------
    def _set_principal(self) -> None:
        if self._principal is None:
            return
        principal = self._principal
        user_id = str(principal.user_id) if principal.user_id is not None else ""
        customer_id = str(principal.customer_id) if principal.customer_id is not None else ""
        with connections[self._using].cursor() as cursor:
            # `true` = transaction-local, so the identity cannot outlive this transaction
            # on a pooled connection (docs/04 10.9).
            cursor.execute(
                "SELECT set_config('app.user_id', %s, true), "
                "set_config('app.customer_id', %s, true)",
                [user_id, customer_id],
            )

    def _flush(self, events: list[DomainEvent]) -> None:
        """One statement for every staged event, still inside the caller's transaction."""
        if not events:
            return
        rows = [event.to_row() for event in events]
        params: list[Sequence[Any]] = [
            (row[0], row[1], row[2], row[3], Jsonb(row[4])) for row in rows
        ]
        with connections[self._using].cursor() as cursor:
            cursor.executemany(
                "INSERT INTO hub.outbox "
                "(id, aggregate_type, aggregate_id, event_type, payload) "
                "VALUES (%s, %s, %s, %s, %s)",
                params,
            )
