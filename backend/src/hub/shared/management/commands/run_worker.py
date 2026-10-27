"""Queue consumers (docs/03 2.1): a no-op loop until the slices that own events land.

The operational contract is what matters here and it does not change later: ``--once`` for
CI and one-shot runs, a loop otherwise, ``loop.started`` logged as JSON before any work, the
internal metrics port opened, and a SIGTERM that stops the loop and returns instead of
killing the process mid-iteration. The consumers, the ``--group`` membership and the
at-least-once acknowledgement arrive with the modules that publish events.
"""

from __future__ import annotations

import logging
import signal
import threading
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from hub.shared.logging import configure_logging
from hub.shared.metrics import start_metrics_server

logger = logging.getLogger(__name__)

# One iteration per second until the slices that do work get a real poll interval.
POLL_INTERVAL_SECONDS = 1.0


class LoopCommand(BaseCommand):
    """A run-until-stopped command; subclasses set ``service`` and the event they consume."""

    service = "hub-worker"
    help = "Run a background loop until SIGTERM (slice 00: no work yet)."

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Tests wait on `started` and stop through `stop()`: neither needs a signal.
        self.started = threading.Event()
        self._stop = threading.Event()

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one iteration and exit (CI and one-shot jobs).",
        )

    def stop(self) -> None:
        """Ask the loop to finish its iteration and return (the SIGTERM handler does this)."""
        self._stop.set()

    def run_once(self) -> None:
        """Do the command's one-shot work. A worker consumes a queue, so it has none."""

    def handle(self, *args: Any, **options: Any) -> None:
        configure_logging(service=self.service)
        start_metrics_server()
        restore = self._install_stop_signals()
        once = bool(options.get("once"))
        logger.info("loop.started", extra={"service": self.service, "once": once})
        self.started.set()
        try:
            if once:
                # One shot: the thing a CronJob runs. The base command has nothing to do;
                # `run_scheduler` overrides this to execute its registered jobs. It returns
                # rather than looping, so the process exits 0.
                self.run_once()
                return
            while not self._stop.is_set():
                self._stop.wait(POLL_INTERVAL_SECONDS)
        finally:
            restore()
            logger.info("loop.stopped", extra={"service": self.service})

    def _install_stop_signals(self) -> Any:  # returns a restore callable
        """Install SIGTERM/SIGINT handlers, unless not running in the main thread."""
        if threading.current_thread() is not threading.main_thread():
            return lambda: None

        previous: dict[int, Any] = {}

        def handler(signum: int, frame: Any) -> None:
            self.stop()

        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, handler)

        def restore() -> None:
            for signum, old in previous.items():
                signal.signal(signum, old)

        return restore


class Command(LoopCommand):
    """The worker process: one per consumer group in a later slice."""

    service = "hub-worker"
    help = "Run the consumer loop until SIGTERM (slice 00: no work yet)."

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--group",
            default=None,
            help="Consumer group to join (accepted now, used by the slice that adds consumers).",
        )
