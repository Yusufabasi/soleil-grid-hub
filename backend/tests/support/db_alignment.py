"""Compare unmanaged Django models with the tables ``db/schema.sql`` creates.

The schema is the authority (ADR-0020): a model is a read/write shim over a table that already
exists, so a column the model invents or one it forgets is drift the ORM would hide until a
query failed in production. This walks each model's fields and the catalog's columns and
reports both directions.
"""

from __future__ import annotations

from typing import Any

from django.db import connection

__all__ = ["check_db_alignment"]


def _columns(table: str) -> dict[str, bool]:
    """``{column: nullable}`` for one ``hub.`` table, from the live catalog."""
    schema, _, name = table.partition(".")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            [schema, name],
        )
        return {column: nullable == "YES" for column, nullable in cursor.fetchall()}


def check_db_alignment(*models: type[Any]) -> None:
    """Fail when a model's table is missing, or its columns differ in name or nullability."""
    for model in models:
        table = str(model._meta.db_table)
        columns = _columns(table)
        assert columns, f"{table} does not exist"

        declared = {field.column: bool(field.null) for field in model._meta.local_fields}
        missing_from_model = sorted(set(columns) - set(declared))
        not_in_database = sorted(set(declared) - set(columns))
        assert not missing_from_model, f"{table}: columns with no model field: {missing_from_model}"
        assert not not_in_database, f"{table}: model fields with no column: {not_in_database}"

        mismatch = {
            column: (declared[column], columns[column])
            for column in declared
            if declared[column] != columns[column]
        }
        assert not mismatch, f"{table}: (model null, database nullable) disagrees: {mismatch}"
