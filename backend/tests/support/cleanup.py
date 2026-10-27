"""Deleting what a `transaction=True` test committed, in the order the schema demands.

Most payments tests run inside pytest-django's transaction and roll back for free. The two that
cannot — the concurrency suite (its threads need real commits) and the sweeper's `SKIP LOCKED`
case (a lock must be visible to a second connection) — leave rows behind, and Django's ``flush``
does not know about this schema's unmanaged tables. A leftover ``provider_events`` row would make
the next module's ``len(event_rows()) == 1`` fail for a reason that has nothing to do with it, so
the cleanup is explicit here rather than hoped for.

**The ledger is append-only by design**, and its triggers are ``ENABLE ALWAYS`` so even
``session_replication_role = replica`` does not bypass them (S-9). A test database is not a
ledger, though: leaving a committed credit behind would leave its top-up unremovable (the
transaction references it) and poison every later count. So this helper — and only this helper,
only in a throwaway database — disables the four immutability triggers for the length of the
delete and puts them back in a ``finally``. Nothing in the application can do this: the role it
connects as is not the table owner and does not hold ``ALTER``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import connection, transaction

from support.payments import World

__all__ = ["IMMUTABLE_TABLES", "purge_world"]

# The tables whose statements the append-only triggers guard, and the triggers themselves.
IMMUTABLE_TABLES = ("ledger_entries", "ledger_transactions")
TRIGGERS = {
    "ledger_entries": ("trg_ledger_entries_immutable", "trg_ledger_entries_no_truncate"),
    "ledger_transactions": (
        "trg_ledger_transactions_immutable",
        "trg_ledger_transactions_no_truncate",
    ),
}


def purge_world(world: World, *, top_up_ids: list[UUID] | None = None) -> None:
    """Remove everything a committed test created for ``world`` and every log row it wrote."""
    del top_up_ids
    with transaction.atomic():
        # A top-up references its ledger transaction and that transaction references the
        # top-up (``ledger_transaction_id`` versus ``source_top_up_id``): neither row can be
        # deleted first while the foreign keys are enforced. Disabling constraint triggers for
        # the length of the delete is what makes the removal possible at all; the four
        # append-only triggers are ``ENABLE ALWAYS`` and so need disabling by name.
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role = replica")
        _set_triggers(enabled=False)
        try:
            _delete(world)
        finally:
            _set_triggers(enabled=True)


def _set_triggers(*, enabled: bool) -> None:
    action = "ENABLE" if enabled else "DISABLE"
    with connection.cursor() as cursor:
        for table, triggers in TRIGGERS.items():
            for trigger in triggers:
                cursor.execute(f"ALTER TABLE hub.{table} {action} TRIGGER {trigger}")


def _delete(world: World) -> None:
    """Every row the test wrote, in an order that would also be safe with the keys enforced.

    ``session_replication_role = replica`` suspends the foreign-key triggers, so the order here
    is documentation rather than a requirement: top-ups, their ledger, their wallet, then the
    logs, then the registry.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM hub.ledger_entries WHERE transaction_id IN ("
            " SELECT id FROM hub.ledger_transactions WHERE source_top_up_id IN ("
            "  SELECT id FROM hub.top_ups WHERE customer_id = %s))",
            [world.customer_id],
        )
        cursor.execute(
            "DELETE FROM hub.ledger_transactions WHERE source_top_up_id IN ("
            " SELECT id FROM hub.top_ups WHERE customer_id = %s)",
            [world.customer_id],
        )
        cursor.execute(
            "DELETE FROM hub.wallet_balances WHERE account_id IN ("
            " SELECT id FROM hub.ledger_accounts WHERE customer_id = %s)",
            [world.customer_id],
        )
        cursor.execute(
            "DELETE FROM hub.ledger_accounts WHERE customer_id = %s", [world.customer_id]
        )
        cursor.execute("DELETE FROM hub.top_ups WHERE customer_id = %s", [world.customer_id])
        # The four log tables belong to the committed test while it runs: a narrower predicate
        # would have to reconstruct which rows were theirs, and this is a throwaway database.
        for table in (
            "hub.provider_events",
            "hub.webhook_quarantine",
            "hub.outbox",
            "hub.idempotency_keys",
        ):
            cursor.execute(f"DELETE FROM {table}")  # noqa: S608 - literal table name
        cursor.execute(
            "DELETE FROM hub.meter_assignments WHERE customer_id = %s", [world.customer_id]
        )
        cursor.execute("DELETE FROM hub.meters WHERE customer_id = %s", [world.customer_id])
        _purge_registry(cursor, world)


def _purge_registry(cursor: Any, world: World) -> None:
    """The site, plan, tariff, approval and two finance users ``make_world`` inserted."""
    cursor.execute("SELECT approval_id FROM hub.plan_tariffs WHERE id = %s", [world.tariff_id])
    found = cursor.fetchone()
    approval_id = found[0] if found else None
    users: list[UUID] = []
    if approval_id is not None:
        cursor.execute(
            "SELECT maker_user_id, approver_user_id FROM hub.approvals WHERE id = %s",
            [approval_id],
        )
        pair = cursor.fetchone()
        if pair is not None:
            users = [value for value in pair if value is not None]

    cursor.execute("DELETE FROM hub.customers WHERE id = %s", [world.customer_id])
    cursor.execute("DELETE FROM hub.plan_tariffs WHERE id = %s", [world.tariff_id])
    cursor.execute("DELETE FROM hub.plans WHERE id = %s", [world.plan_id])
    if approval_id is not None:
        cursor.execute("DELETE FROM hub.approvals WHERE id = %s", [approval_id])
    cursor.execute("DELETE FROM hub.sites WHERE id = %s", [world.site_id])
    for user_id in users:
        cursor.execute("DELETE FROM hub.users WHERE id = %s", [user_id])
