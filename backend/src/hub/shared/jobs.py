"""The job framework: a registry of cron jobs, and the one place that runs them (docs/03 2.1).

Slice 00 fixed the process shape of ``run_scheduler``; this module gives it something to run.
A module that owns a periodic fact (the ledger's balance snapshot and its two integrity
checks) registers a *contribution* at ``AppConfig.ready``: ``register_cron`` stores the name,
the schedule and a handler that takes a :class:`JobContext`. The scheduler asks the registry
by name, so the command never imports a business module (the kernel rule of docs/03 section 0).

Scheduling itself is the deployment's concern (a Kubernetes CronJob hits
``manage.py run_scheduler --once <name>``), which is why a cron expression here is metadata,
not a loop: a process that sleeps until 00:30 is a process that misses the run when the pod is
rescheduled. ``singleton`` is metadata too, until a job needs the advisory lock that enforces
it; every job this slice registers is idempotent on its own arguments, so a double run costs
time and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hub.shared.ports import IdGenerator

__all__ = [
    "CronJob",
    "JobContext",
    "JobHandler",
    "JobRegistry",
    "get_registry",
    "register_cron",
    "register_handler",
    "registered_handlers",
    "registered_jobs",
    "reset",
    "resolve_handler",
    "run_job",
]

JobHandler = Callable[["JobContext"], Any]


@dataclass(frozen=True, slots=True)
class JobContext:
    """What a handler is given: its name, the instant it started, the ids and its arguments.

    ``payload`` is how an *event-driven* job receives what it was asked to do ("replay event X"):
    a cron job has none, and a handler that needs one refuses to run without it rather than
    guessing which row was meant.
    """

    name: str
    now: datetime
    ids: IdGenerator
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CronJob:
    """One registered job: when it should run, and what it does."""

    name: str
    cron: str
    handler: JobHandler = field(repr=False)
    singleton: bool = True


class JobRegistry:
    """The process's jobs, registered by name so the scheduler can be asked for one."""

    def __init__(self) -> None:
        self._jobs: dict[str, CronJob] = {}

    def register_cron(
        self,
        *,
        name: str,
        cron: str,
        handler: JobHandler,
        singleton: bool = True,
    ) -> None:
        """Add (or replace) a job. A duplicate name is the same job re-registered, not two."""
        if not name or "." not in name:
            # `ledger.snapshot_balances`: the module prefix is what makes the name unambiguous.
            raise ValueError(f"job name must be '<module>.<job>', got {name!r}")
        self._jobs[name] = CronJob(name=name, cron=cron, handler=handler, singleton=singleton)

    def get(self, name: str) -> CronJob | None:
        return self._jobs.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._jobs))

    def all(self) -> Sequence[CronJob]:
        return tuple(self._jobs[name] for name in sorted(self._jobs))

    def clear(self) -> None:
        """Drop every registration; a test seam, never a production call."""
        self._jobs.clear()


_registry = JobRegistry()
# Event-driven handlers, addressable by name but never run by the scheduler: an `--once` pass
# would otherwise replay whatever the last request enqueued, on a timer.
_handlers: dict[str, JobHandler] = {}


def get_registry() -> JobRegistry:
    """The process registry the composition roots register into."""
    return _registry


def registered_jobs() -> tuple[str, ...]:
    return _registry.names()


def register_cron(
    *,
    name: str,
    cron: str,
    handler: JobHandler,
    singleton: bool = True,
) -> None:
    """Contribute a job to the process registry (called from a module's ``ready``)."""
    _registry.register_cron(name=name, cron=cron, handler=handler, singleton=singleton)


def register_handler(*, name: str, handler: JobHandler) -> None:
    """Contribute an event-driven handler (``payments.replay_event`` and friends)."""
    if not name or "." not in name:
        raise ValueError(f"job name must be '<module>.<job>', got {name!r}")
    _handlers[name] = handler


def registered_handlers() -> tuple[str, ...]:
    return tuple(sorted(_handlers))


def resolve_handler(name: str) -> JobHandler | None:
    """The handler for ``name``: a cron job's, an event-driven one's, or ``None``."""
    job = _registry.get(name)
    if job is not None:
        return job.handler
    return _handlers.get(name)


def run_job(
    name: str,
    *,
    now: datetime,
    ids: IdGenerator,
    payload: Mapping[str, Any] | None = None,
) -> Any:
    """Run one registered job by name; an unknown name is a programming error, not a skip."""
    handler = resolve_handler(name)
    if handler is None:
        raise KeyError(f"no job registered as {name!r}")
    return handler(JobContext(name=name, now=now, ids=ids, payload=payload))


def reset() -> None:
    """Forget every registration. Tests only: composition is redone by ``ready``."""
    _registry.clear()
    _handlers.clear()
