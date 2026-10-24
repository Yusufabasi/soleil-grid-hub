"""Composition root: the single place that knows the concrete adapters (docs/03 section 0).

``hub.shared.apps.SharedConfig.ready()`` calls :func:`build_container` once per process, so
management commands, WSGI and ASGI entrypoints all see the same container. Business code
only ever reaches the adapters through the ports it needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hub.shared.clock import SystemClock
from hub.shared.ids import Uuid7Generator
from hub.shared.ports import Clock, IdGenerator

__all__ = ["Container", "build_container", "get_container"]


@dataclass(frozen=True, slots=True)
class Container:
    """The wired adapters. Later slices add providers, caches and gateways here."""

    clock: Clock
    ids: IdGenerator


_container: Container | None = None


def build_container(settings: Any = None) -> Container:
    """Wire the adapters and remember them; a second call returns the same container.

    ``settings`` is the Django settings module (or None): only wiring may read it, the
    business layers never do (docs/03 section 3.3, contract ``wiring-only``).
    """
    global _container  # noqa: PLW0603 - the composition root is deliberately one object per process

    container = _container
    if container is None:
        clock = SystemClock()
        container = Container(clock=clock, ids=Uuid7Generator(clock=clock))
        _container = container
    return container


def get_container() -> Container:
    """The process container, built on first use (``ready()`` normally gets there first)."""
    container = _container
    if container is None:
        container = build_container()
    return container
