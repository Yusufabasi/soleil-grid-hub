"""Routes of the ledger module. The URL name is the ``operationId`` (docs/03 7.7).

`getCustomerWallet` lives under `/customers` because that is where the customer portal's client
expects it; the rest are the ledger's own namespace. Only operations `api/openapi.yaml` declares
are mounted (executor guide rule 4), which is also why there is no reopen route: the contract
has none, and a reopen goes through the approval queue.
"""

from typing import Any

from django.urls import path

from hub.ledger.api.views import (
    AccountingPeriodCollectionView,
    AccountingPeriodLockView,
    CustomerWalletView,
    LedgerAccountCollectionView,
    LedgerAccountDetailView,
    LedgerAdjustmentView,
    LedgerTransactionCollectionView,
    LedgerTransactionDetailView,
    RefundView,
)

__all__ = ["urlpatterns"]

urlpatterns: list[Any] = [
    path(
        "customers/<uuid:customer_id>/wallet",
        CustomerWalletView.as_view(),
        name="getCustomerWallet",
    ),
    path("ledger/accounts", LedgerAccountCollectionView.as_view(), name="listLedgerAccounts"),
    path(
        "ledger/accounts/<uuid:account_id>",
        LedgerAccountDetailView.as_view(),
        name="getLedgerAccount",
    ),
    path(
        "ledger/transactions",
        LedgerTransactionCollectionView.as_view(),
        name="listLedgerTransactions",
    ),
    path(
        "ledger/transactions/<uuid:transaction_id>",
        LedgerTransactionDetailView.as_view(),
        name="getLedgerTransaction",
    ),
    path(
        "ledger/adjustments",
        LedgerAdjustmentView.as_view(),
        name="createLedgerAdjustment",
    ),
    path("refunds", RefundView.as_view(), name="createRefund"),
    path(
        "accounting-periods",
        AccountingPeriodCollectionView.as_view(),
        name="listAccountingPeriods",
    ),
    path(
        "accounting-periods/<uuid:period_id>/lock",
        AccountingPeriodLockView.as_view(),
        name="lockAccountingPeriod",
    ),
]
