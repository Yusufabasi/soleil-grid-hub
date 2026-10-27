"""Unmanaged models over the ledger tables (ADR-0020, docs/04 section 4).

The schema owns every rule: the balance triggers, the immutability triggers (``ENABLE ALWAYS``,
so even a ``session_replication_role = replica`` session cannot edit an entry), the deferred
zero-sum check, the period guard and the grants that leave an application role with EXECUTE on
the definer functions and no INSERT or UPDATE on these tables. These classes therefore carry
columns and ``managed = False``; a constraint or a save path written here would be a second,
silent source of truth.

There is no write method anywhere on purpose. ``LedgerWriter`` is the only caller of
``hub.post_ledger_transaction`` and friends, and ``tests/architecture/test_ledger_models.py``
pins both halves of that: no ``save``, no ``objects.create`` in the module.

``db_table`` is bare (not ``hub.ledger_entries``): Django quotes its argument as one identifier,
and the connection's ``search_path`` is what resolves the schema (D-43).
"""

from __future__ import annotations

from django.db import models
from django.db.backends.base.base import BaseDatabaseWrapper

__all__ = [
    "AccountingPeriod",
    "LedgerAccount",
    "LedgerBalanceSnapshot",
    "LedgerEntry",
    "LedgerTransaction",
    "WalletBalance",
]


# Django's `Field` is not generic at runtime, so the base class cannot be subscripted even
# though the stubs declare type parameters.
class Xid8Field(models.Field):  # type: ignore[type-arg]
    """``xid8``: a 64-bit transaction id, which Django has no field for.

    Only ``ledger_transactions.created_xid`` uses it, and only to answer "did this row come
    from the transaction that is asking?" (S-9). Python never writes or interprets the value,
    so the field exists to keep the shim aligned with the table.
    """

    def db_type(self, connection: BaseDatabaseWrapper) -> str:
        return "xid8"


class UnmanagedShim(models.Model):
    """Base of the shims: no table of its own, no migration, nothing the ORM may write."""

    class Meta:
        abstract = True
        managed = False

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.pk})"


class LedgerAccount(UnmanagedShim):
    """``hub.ledger_accounts``: one wallet per customer and currency, plus the system accounts."""

    id = models.UUIDField(primary_key=True)
    code = models.TextField()
    kind = models.TextField()
    currency = models.CharField(max_length=3)
    customer_id = models.UUIDField(null=True)
    site_id = models.UUIDField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "ledger_accounts"


class AccountingPeriod(UnmanagedShim):
    """``hub.accounting_periods``: a month, open or locked (D-23)."""

    id = models.UUIDField(primary_key=True)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.TextField(default="open")
    locked_by = models.UUIDField(null=True)
    locked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "accounting_periods"


class LedgerTransaction(UnmanagedShim):
    """``hub.ledger_transactions``: one balanced posting, immutable once written."""

    id = models.UUIDField(primary_key=True)
    posting_key = models.TextField()
    kind = models.TextField()
    description = models.TextField(null=True)  # noqa: DJ001 - NULL and "" differ in the schema
    effective_at = models.DateTimeField()
    created_at = models.DateTimeField()
    created_by_user_id = models.UUIDField(null=True)
    approval_id = models.UUIDField(null=True)
    source_top_up_id = models.UUIDField(null=True)
    reverses_transaction_id = models.UUIDField(null=True)
    created_xid = Xid8Field()

    class Meta:
        managed = False
        db_table = "ledger_transactions"


class LedgerEntry(UnmanagedShim):
    """``hub.ledger_entries``: one side of a posting; insert-only."""

    id = models.UUIDField(primary_key=True)
    transaction_id = models.UUIDField()
    account_id = models.UUIDField()
    currency = models.CharField(max_length=3)
    amount_minor = models.BigIntegerField()
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "ledger_entries"


class WalletBalance(UnmanagedShim):
    """``hub.wallet_balances``: the maintained, credit-normal balance of one wallet.

    Written by ``hub.apply_wallet_balance`` in the same transaction as the entry. Application
    roles hold no UPDATE on it (S-3), which is why nothing here writes it either.
    """

    account_id = models.UUIDField(primary_key=True)
    currency = models.CharField(max_length=3)
    balance_minor = models.BigIntegerField()
    entry_count = models.BigIntegerField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "wallet_balances"


class LedgerBalanceSnapshot(UnmanagedShim):
    """``hub.ledger_balance_snapshots``: a system account's balance at the end of one day.

    The table's key is the pair ``(account_id, snapshot_date)``, which the ORM cannot express;
    ``account_id`` carries ``primary_key`` the way ``hub.user_roles`` does, and the queries
    address both columns explicitly (ADR-0020: the schema owns the constraint).
    """

    account_id = models.UUIDField(primary_key=True)
    snapshot_date = models.DateField()
    balance_minor = models.BigIntegerField()
    entry_count = models.BigIntegerField()
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "ledger_balance_snapshots"
