"""Outbox relay (docs/03 2.1, docs/04 5.4): a no-op loop until the relay slice.

The relay will claim rows with ``UPDATE ... RETURNING ... SKIP LOCKED``, publish them and
mark them published in the same transaction; slice 00 only fixes the process shape, so the
command shares the loop of :mod:`hub.shared.management.commands.run_worker`.
"""

from __future__ import annotations

from hub.shared.management.commands.run_worker import LoopCommand


class Command(LoopCommand):
    """Run the relay loop until SIGTERM (slice 00: no rows are claimed yet)."""

    service = "hub-relay"
    help = "Publish outbox rows until SIGTERM (slice 00: no work yet)."
