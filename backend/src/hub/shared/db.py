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


def call_function(
    name: str,
    *args: Any,
    using: str = "default",
) -> Sequence[Any] | None:
    """Run ``SELECT hub.<name>(%s, ...)`` and return the first row."""
    if not _FUNCTION_NAME_RE.fullmatch(name):
        raise ValueError(f"not a hub function name: {name!r}")
    placeholders = ", ".join(["%s"] * len(args))
    with connections[using].cursor() as cursor:
        cursor.execute(f"SELECT hub.{name}({placeholders})", list(args))
        return cast("Sequence[Any] | None", cursor.fetchone())
