"""Enqueueing work for a worker (`hub.shared.contracts.JobQueue`).

The queue is the outbox, not a second table. A job is a fact ("replay event X") that must survive
the request that asked for it and must not be enqueued twice by a retried request, and
``hub.outbox`` already gives both: the row commits with the caller's transaction, and its primary
key can be derived from the request's dedupe key so a second ``INSERT`` is a no-op.

The consumer is the relay plus a worker (slice 05 owns it); until then a ``job.requested`` row is
durable, visible and idempotent, which is what the API promised the caller with its 202. This
module is the kernel adapter — the Django side of the contract — so a business module enqueues
through the Protocol and never through this file.

The derived id is a ``uuid5`` of the dedupe key: deterministic, so a retry produces the same row
instead of a second job, and namespaced by the job name so two jobs never collide on one key.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from django.db import connections
from psycopg.types.json import Jsonb

from hub.shared.ports import Clock, IdGenerator

__all__ = ["OutboxJobQueue", "JOB_REQUESTED"]

JOB_REQUESTED = "job.requested"
# A fixed namespace, so the same dedupe key always mints the same id — across processes and
# across restarts, which is what makes the `ON CONFLICT` a real guard rather than a local one.
_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


class OutboxJobQueue:
    """``JobQueue`` over ``hub.outbox``: one durable row per request, at most one per key."""

    def __init__(self, *, clock: Clock, ids: IdGenerator, using: str = "default") -> None:
        self._clock = clock
        self._ids = ids
        self._using = using

    def enqueue(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        dedupe_key: str | None = None,
    ) -> uuid.UUID:
        """Record a job request and return its id; a repeated key returns the same id."""
        if not name or "." not in name:
            raise ValueError(f"job name must be '<module>.<job>', got {name!r}")
        job_id = (
            uuid.uuid5(_NAMESPACE, f"{name}:{dedupe_key}")
            if dedupe_key is not None
            else self._ids.new_id()
        )
        document = {
            "name": name,
            "requested_at": self._clock.now().isoformat(),
            "payload": dict(payload),
        }
        params: list[Any] = [job_id, job_id, JOB_REQUESTED, Jsonb(document)]
        with connections[self._using].cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.outbox (id, aggregate_type, aggregate_id, event_type, payload)"
                " VALUES (%s, 'job', %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                params,
            )
        return job_id
