"""Named ports one module publishes for another, through the kernel (docs/03 section 3.1).

The import contract lets a module see another's ``contracts`` and ``events`` — the vocabulary,
not the composition root. Where one module needs *services* from a module it may not import
(``ledger`` needs `identity`'s approvals, audit log, transaction and executor registry), the
provider publishes them here by name and the consumer asks for them by name. ``hub.shared`` sits
below both, so neither has to know the other exists at import time.

This is a service-locator on purpose, and a small one: it holds callables, never instances, so
ordering between ``AppConfig.ready`` hooks cannot freeze a stale object, and a test reset is a
dict clear. It is not a general container — `hub.bootstrap` still builds the adapters a
deployable needs; this only carries what a module cannot reach across the contract.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["lookup", "provide", "published", "require", "reset"]

_PORTS: dict[str, Callable[..., Any]] = {}


def provide(name: str, factory: Callable[..., Any]) -> None:
    """Publish ``factory`` under ``name``. A second publish replaces the first."""
    _PORTS[name] = factory


def lookup(name: str) -> Callable[..., Any] | None:
    """The factory published under ``name``, or ``None``."""
    return _PORTS.get(name)


def require(name: str) -> Callable[..., Any]:
    """The factory published under ``name``, or the error that names what is missing.

    The message is deliberately actionable: this raising means a module's ``ready()`` did not
    publish, which is a wiring bug, not a runtime condition a caller can recover from.
    """
    factory = _PORTS.get(name)
    if factory is None:
        raise RuntimeError(
            f"no port published as {name!r}: the module that owns it did not publish at start-up"
        )
    return factory


def published() -> frozenset[str]:
    """The names published so far (a test asserts `ledger` can reach its four)."""
    return frozenset(_PORTS)


def reset() -> None:
    """Forget every port; tests call it after resetting a module's wiring."""
    _PORTS.clear()
