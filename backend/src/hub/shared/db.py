"""Calls into the SQL functions that own the hardest invariants (docs/04 3, ADR-0020).

The ledger, the wallet balance, the token saga and the erasure path live in ``hub``
functions written in ``db/schema.sql``. Python may only call them by name with parameters:
building the statement from a validated identifier is what keeps that boundary inspectable,
and the SQLSTATE mapping (``23505`` -> ``Duplicate``, ``P0001`` -> ``Conflict``) arrives
with slice 03, the first slice that calls a function which can fail that way.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, cast

from django.db import connections

__all__ = ["call_function"]

# A function name is a lowercase identifier: nothing else can reach an interpolated string.
_FUNCTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# A cast is a PostgreSQL type name, optionally an array; it is written into the statement, so
# it is validated the same way the function name is.
_TYPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\[\])?$")


def call_function(
    name: str,
    *args: Any,
    using: str = "default",
    casts: Sequence[str] | None = None,
) -> Sequence[Any] | None:
    """Run ``SELECT hub.<name>(%s, ...)`` and return the first row.

    ``casts`` names the PostgreSQL type of each argument when psycopg's own adaptation would
    send the wrong one: a Python ``str`` goes out as ``text``, and Postgres will not coerce
    ``text`` to ``inet`` for a function call even though it would for an ``INSERT``.
    """
    if not _FUNCTION_NAME_RE.fullmatch(name):
        raise ValueError(f"not a hub function name: {name!r}")
    placeholders = _placeholders(len(args), casts)
    with connections[using].cursor() as cursor:
        cursor.execute(f"SELECT hub.{name}({placeholders})", list(args))
        return cast("Sequence[Any] | None", cursor.fetchone())


def _placeholders(count: int, casts: Sequence[str] | None) -> str:
    """``%s`` per argument, each with its ``::type`` when the caller named one."""
    if casts is None:
        return ", ".join(["%s"] * count)
    if len(casts) != count:
        raise ValueError(f"{count} arguments but {len(casts)} casts")
    for name_cast in casts:
        if not _TYPE_NAME_RE.fullmatch(name_cast):
            raise ValueError(f"not a PostgreSQL type name: {name_cast!r}")
    return ", ".join(f"%s::{name_cast}" for name_cast in casts)
