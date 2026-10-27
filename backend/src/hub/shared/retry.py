"""Retrying the two PostgreSQL failures a retry actually fixes (docs/03 6.1).

No framework is imported here on purpose. ``hub.shared.db`` is the kernel's hand to the SQL
functions and pulls Django in with it, while the application layer may not import Django — yet
the webhook's use case is exactly what needs this retry. Keeping the policy in its own module
lets the use case follow the contract without the framework.

Only two SQLSTATEs are transient: PostgreSQL rolled the transaction back because it chose this
statement as the deadlock victim, or a serializable read could not be ordered. Everything else
(a unique violation, a refused check, a missing function) is a real answer, and retrying it
would only repeat the work.
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = [
    "DEADLOCK_DETECTED",
    "SERIALIZATION_FAILURE",
    "TRANSIENT_SQLSTATES",
    "retry_transient",
    "sqlstate_of",
]

DEADLOCK_DETECTED = "40P01"
SERIALIZATION_FAILURE = "40001"
TRANSIENT_SQLSTATES = frozenset({DEADLOCK_DETECTED, SERIALIZATION_FAILURE})


def sqlstate_of(exc: BaseException) -> str | None:
    """The PostgreSQL SQLSTATE behind a Django or psycopg error, walking the cause chain.

    Django wraps driver errors, so the ``sqlstate`` is never on the exception a caller catches;
    it is one or two ``__cause__`` links down. The walk stops on a cycle rather than looping.
    """
    seen: BaseException | None = exc
    visited: set[int] = set()
    while seen is not None and id(seen) not in visited:
        visited.add(id(seen))
        state = getattr(seen, "sqlstate", None)
        if isinstance(state, str):
            return state
        seen = seen.__cause__ if seen.__cause__ is not seen else None
    return None


def retry_transient[T](fn: Callable[[], T], *, attempts: int = 3) -> T:
    """Run ``fn``, retrying only a deadlock or a serialization failure (docs/03 6.1).

    The webhook's whole transaction runs through this: two deliveries of the same event can
    deadlock on the unique index, and the loser must try again rather than answer 500 for
    something PostgreSQL already knows how to fix. The caller's ``fn`` must therefore build its
    transaction inside the call — retrying a half-open unit of work would retry a rolled-back
    transaction's leftovers.
    """
    if attempts < 1:
        raise ValueError("retry_transient needs at least one attempt")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is transient
            if attempt >= attempts or sqlstate_of(exc) not in TRANSIENT_SQLSTATES:
                raise
    raise AssertionError("unreachable")  # pragma: no cover - the loop always returns or raises
