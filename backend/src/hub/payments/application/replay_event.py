"""``payments.replay_event``: re-drive a stored delivery through the same exactly-once path.

The staff operation ``replayWebhookEvent`` exists for one situation: an event that was recorded
and then ignored — an unknown reference, a top-up that had not been inserted yet — and somebody
has since fixed the reason. It must not become a second way to credit money, so the job is a
*narrow* re-entry point:

* **Only an event that did not finish is replayed.** A row already ``processed`` is a no-op:
  its effect (or its suspense posting) exists, and re-routing a settled confirmation would
  classify it as ``already_confirmed`` and park a second suspense receipt for money already
  allocated. ``ignored`` and ``failed`` rows are the ones a person is asking about.
* **The row is the verification.** The signature was checked when the delivery arrived and the
  envelope was stored; replaying re-uses that envelope, so the job never invents a payload.
* **Nothing here decides anything.** It decodes the stored envelope and calls
  ``ConfirmPayment.replay``, which locks the top-up, routes with the same rules and marks the
  event — the same code the live delivery runs, so a replay cannot drift from it.

The deduplication is the posting key (``topup:<id>:confirm``) and the top-up's status: a replay
of an ignored event after the top-up appeared credits once, and a second replay credits nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from uuid import UUID

from hub.payments.application.ports import VerifiedEvent
from hub.payments.contracts import ProviderEventRow
from hub.shared.errors import NotFound

__all__ = ["ReplayEvent", "ReplaySource"]

logger = logging.getLogger(__name__)

# Statuses a replay may act on. `processed` is settled; `received` never finished routing;
# `ignored` was deliberately not routed and is the one a person usually wants back.
REPLAYABLE = frozenset({"received", "ignored", "failed"})

EnvelopeDecoder = Callable[[Mapping[str, Any]], VerifiedEvent]


class ReplaySource(Protocol):
    """The two reads the job makes of ``hub.provider_events``."""

    def get(self, event_id: UUID) -> ProviderEventRow | None: ...

    def payload_of(self, event_id: UUID) -> Mapping[str, Any] | None: ...


class ReplayRunner(Protocol):
    """``ConfirmPayment.replay``: route a stored, already-verified event."""

    def replay(self, event_row_id: UUID, data: VerifiedEvent, provider: str) -> str | None: ...


class ReplayEvent:
    """Re-run a stored delivery by id; a settled event is left exactly as it is."""

    def __init__(
        self, *, events: ReplaySource, confirm: ReplayRunner, decode: EnvelopeDecoder
    ) -> None:
        self._events = events
        self._confirm = confirm
        self._decode = decode

    def __call__(self, event_id: UUID) -> str | None:
        """Replay ``event_id``; returns the reason stored when it was ignored, if any."""
        row = self._events.get(event_id)
        if row is None:
            # The job names a row that no longer exists (deleted, or the wrong cluster): a
            # retry cannot fix that, so it fails loudly rather than silently succeeding.
            raise NotFound(details={"event_id": str(event_id)})
        if row.status not in REPLAYABLE:
            logger.info(
                "payments.replay.skipped",
                extra={"event_id": str(event_id), "status": row.status},
            )
            return None
        envelope = self._events.payload_of(event_id)
        if envelope is None:  # pragma: no cover - `get` found the row a statement ago
            raise NotFound(details={"event_id": str(event_id)})
        data = self._decode(envelope)
        return self._confirm.replay(event_id, data, row.provider)
