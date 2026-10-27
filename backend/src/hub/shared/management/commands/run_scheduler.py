"""Scheduler (docs/03 2.1, docs/09 3.4): run the registered cron jobs, once or on a loop.

Job registration is a contribution: a module registers at ``AppConfig.ready`` through
:func:`hub.shared.jobs.register_cron`, so this command only asks the registry for names and
never imports a business module (docs/03 section 0). The deployment drives the schedule:

    manage.py run_scheduler --once ledger.snapshot_balances

runs that one job and exits, which is what a Kubernetes CronJob wants. Without names, ``--once``
runs every registered job in name order; without ``--once`` the process keeps the slice-00
loop shape and announces itself, which the tests of that slice pin.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import CommandParser

from hub.bootstrap import get_container
from hub.shared.jobs import get_registry, run_job
from hub.shared.management.commands.run_worker import LoopCommand

__all__ = ["Command"]


class Command(LoopCommand):
    """Run the registered cron jobs until SIGTERM, or one round with ``--once``."""

    service = "hub-scheduler"
    help = "Run scheduled jobs until SIGTERM; --once runs them one round and exits."

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "jobs",
            nargs="*",
            metavar="name",
            help="Job names to run (default: every registered job).",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._names: tuple[str, ...] = ()

    def handle(self, *args: Any, **options: Any) -> None:
        # Remembered before the loop starts: `run_once` is called by the parent's handle().
        self._names = tuple(options.get("jobs") or ())
        super().handle(*args, **options)

    def run_once(self) -> None:
        """Run the selected jobs, in the order the registry lists them."""
        registry = get_registry()
        names = self._names or registry.names()
        unknown = [name for name in names if registry.get(name) is None]
        if unknown:
            raise KeyError(f"no job registered as {unknown[0]!r}")
        container = get_container()
        for name in names:
            run_job(name, now=container.clock.now(), ids=container.ids)
