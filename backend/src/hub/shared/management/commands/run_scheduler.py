"""Scheduler (docs/03 2.1, docs/09 3.4): a no-op loop until the jobs slice.

When jobs arrive each one runs under a Postgres advisory lock, so two scheduler versions
never execute the same singleton twice during a rolling deploy; slice 00 only fixes the
process shape, so the command shares the loop of
:mod:`hub.shared.management.commands.run_worker`.
"""

from __future__ import annotations

from hub.shared.management.commands.run_worker import LoopCommand


class Command(LoopCommand):
    """Run the scheduler loop until SIGTERM (slice 00: no jobs registered yet)."""

    service = "hub-scheduler"
    help = "Run scheduled jobs until SIGTERM (slice 00: no work yet)."
