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

from collections.abc import Callable, Sequence
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
    "registered_jobs",
    "reset",
    "run_job",
]

JobHandler = Callable[["JobContext"], Any]


@dataclass(frozen=True, slots=True)
class JobContext:
    """What a handler is given: its own name, the instant it started, and the id generator."""

    name: str
    now: datetime
    ids: IdGenerator


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


def run_job(name: str, *, now: datetime, ids: IdGenerator) -> Any:
    """Run one registered job by name; an unknown name is a programming error, not a skip."""
    job = _registry.get(name)
    if job is None:
        raise KeyError(f"no job registered as {name!r}")
    return job.handler(JobContext(name=job.name, now=now, ids=ids))


def reset() -> None:
    """Forget every registration. Tests only: composition is redone by ``ready``."""
    _registry.clear()
