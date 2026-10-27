"""Calls into the SQL functions that own the hardest invariants (docs/04 3, ADR-0020).

The ledger, the wallet balance, the token saga and the erasure path live in ``hub``
functions written in ``db/schema.sql``. Python may only call them by name with parameters:
building the statement from a validated identifier is what keeps that boundary inspectable,
and the SQLSTATE mapping (``23505`` -> ``Duplicate``, ``P0001`` -> ``Conflict``) arrives
with slice 03, the first slice that calls a function which can fail that way.

The retry policy lives in :mod:`hub.shared.retry` and is re-exported here so callers that
already have this module keep working; that module is framework-free because the application
layer needs the policy and may not import Django.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, cast

from django.db import connections, transaction

from hub.shared.context import db_customer_id_var, db_user_id_var
from hub.shared.retry import (
    DEADLOCK_DETECTED,
    SERIALIZATION_FAILURE,
    TRANSIENT_SQLSTATES,
    retry_transient,
    sqlstate_of,
)

__all__ = [
    "DEADLOCK_DETECTED",
    "SERIALIZATION_FAILURE",
    "TRANSIENT_SQLSTATES",
    "announced",
    "call_function",
    "retry_transient",
    "sqlstate_of",
]

# A function name is a lowercase identifier: nothing else can reach an interpolated string.
_FUNCTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# A cast is a PostgreSQL type name, optionally an array; it is written into the statement, so
# it is validated the same way the function name is.
_TYPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\[\])?$")


@contextmanager
def announced(using: str = "default") -> Iterator[None]:
    """Run a read inside a transaction that announces the request's caller (D-22, docs/04 10.9).

    `hub.customers`, `hub.top_ups`, the wallet tables and `hub.ledger_accounts` are forced-RLS
    and their `hub_api` policies ask `hub.site_visible(...)` / `hub.current_customer_id()`, which
    read `app.user_id` and `app.customer_id`. Those settings are transaction-local on purpose
    (PgBouncer pools transactions, so a session local would leak across requests), which means a
    SELECT outside a transaction matches **no** policy and answers zero rows — a missing customer
    that exists, a wallet that looks empty, a top-up that 404s.

    So a read of one of those tables opens its own short transaction, exactly as a write already
    does through the unit of work. Inside an existing transaction nothing is set: the enclosing
    unit of work has already announced its caller, and re-announcing a different identity there
    would outlive the savepoint.
    """
    connection = connections[using]
    user_id = db_user_id_var.get() or ""
    customer_id = db_customer_id_var.get() or ""
    if connection.in_atomic_block or (not user_id and not customer_id):
        yield
        return
    with transaction.atomic(using=using):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.user_id', %s, true), "
                "set_config('app.customer_id', %s, true)",
                [user_id, customer_id],
            )
        yield


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
