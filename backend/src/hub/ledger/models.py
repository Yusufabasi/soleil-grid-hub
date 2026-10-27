"""Django's model discovery for the `ledger` app: the unmanaged shims, and nothing else.

Django imports ``<app>.models`` when it loads an app, so this one-line shim is what makes the
read-only models visible to the ORM without putting them at the app's root (docs/03 1.2 keeps
the layers in their packages). They are ``managed = False``: ``makemigrations --check`` has
nothing to propose, because ``db/schema.sql`` is the authority (ADR-0020).
"""

from hub.ledger.infrastructure.models import (
    AccountingPeriod,
    LedgerAccount,
    LedgerBalanceSnapshot,
    LedgerEntry,
    LedgerTransaction,
    WalletBalance,
)

__all__ = [
    "AccountingPeriod",
    "LedgerAccount",
    "LedgerBalanceSnapshot",
    "LedgerEntry",
    "LedgerTransaction",
    "WalletBalance",
]
