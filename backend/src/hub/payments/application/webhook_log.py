"""The staff webhook log: read the deliveries, and ask for one to be replayed (FR-03, Task 04.10).

Two jobs, and both exist because something went wrong in production:

* **The log is what an engineer reads at 03:00.** It therefore never returns the stored envelope:
  ``hub_api`` holds a column grant without ``payload``, and returning it would put a payer's
  number and a provider's identifiers in front of a role that has no reason to see them.
* **Replay is a *request*, not a run.** The route answers 202 and the work happens in a worker:
  re-driving an event can take a provider pull, and holding the request open for it would make
  the console wait on a third party. The job is deduplicated by the event id, so a double click
  is one job.

A row that is already ``processed`` still answers 202 on replay: the job decides it has nothing
to do (``ReplayEvent``), and refusing here would ask the caller to know the row's status.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from hub.shared.contracts import JobQueue
from hub.shared.errors import NotFound

__all__ = ["REPLAY_JOB", "WebhookLogQuery", "WebhookLogService", "WebhookLogStore"]

# The event-driven job the replay route enqueues; `payments.wiring` registers the handler.
REPLAY_JOB = "payments.replay_event"


class WebhookLogStore(Protocol):
    """The reads the log makes; ``tuple`` keys are the columns ``hub_api`` may see."""

    def list_log(
        self,
        *,
        provider: str | None = None,
        statuses: Sequence[str] = (),
        event_id: str | None = None,
        received_after: datetime | None = None,
        after_id: UUID | None = None,
        limit: int = 25,
    ) -> list[Mapping[str, Any]]: ...

    def log_entry(self, event_id: UUID) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class WebhookLogQuery:
    """``listWebhookEvents`` filters: keyset on ``id``, newest first."""

    provider: str | None = None
    statuses: tuple[str, ...] = ()
    event_id: str | None = None
    received_after: datetime | None = None
    after_id: UUID | None = None
    limit: int = 25


class WebhookLogService:
    """``listWebhookEvents``, ``getWebhookEvent`` and ``replayWebhookEvent``."""

    def __init__(self, *, events: WebhookLogStore, queue: JobQueue) -> None:
        self._events = events
        self._queue = queue

    def list(self, query: WebhookLogQuery) -> tuple[list[Mapping[str, Any]], UUID | None]:
        """One page, and the cursor of the next one only when it exists."""
        rows = self._events.list_log(
            provider=query.provider,
            statuses=query.statuses,
            event_id=query.event_id,
            received_after=query.received_after,
            after_id=query.after_id,
            limit=query.limit + 1,
        )
        page = rows[: query.limit]
        next_cursor = page[-1]["id"] if len(rows) > query.limit and page else None
        return page, next_cursor

    def get(self, event_id: UUID) -> Mapping[str, Any]:
        """One row, or 404; a log entry is not something a caller may guess at."""
        row = self._events.log_entry(event_id)
        if row is None:
            raise NotFound(details={"webhook_event_id": str(event_id)})
        return row

    def request_replay(self, event_id: UUID) -> Mapping[str, Any]:
        """Enqueue the replay job and return the row the caller was looking at."""
        row = self.get(event_id)
        self._queue.enqueue(REPLAY_JOB, {"event_id": str(event_id)}, dedupe_key=str(event_id))
        return row
