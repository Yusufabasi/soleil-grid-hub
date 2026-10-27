"""`/customers/{id}/wallet`, `/ledger/*` and `/accounting-periods` (FR-04).

Every handler is thin: validate the query or body, call one use case or one read, and shape one
document. The rules that matter — step-up, the role matrix, the maker-checker binding, the
period guard, the no-overdraft check — live in the permission class, the use cases and the
database, never here.

Two deliberate absence: there is no write endpoint for a ledger entry (postings come from the
modules that own the facts, through ``LedgerService``), and a customer can read exactly one
wallet, its own, with the customer id taken from the principal rather than from the path.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.identity.contracts import Principal
from hub.ledger import wiring
from hub.ledger.api.serializers import (
    AdjustmentCreateInput,
    PeriodLockInput,
    RefundCreateInput,
    account_document,
    decode_transaction_cursor,
    decode_uuid_cursor,
    encode_transaction_cursor,
    encode_uuid_cursor,
    transaction_document,
    wallet_document,
)
from hub.ledger.application.adjustments import AdjustmentCreate, RefundCreate
from hub.ledger.application.periods import PeriodLockCommand, period_document
from hub.ledger.domain.errors import LedgerNotFound
from hub.shared.api.idempotency import idempotent_response, replay_or_run, scope_for
from hub.shared.api.permissions import PermissionsYamlPermission, operation
from hub.shared.errors import InvalidRequest, Unauthenticated

__all__ = [
    "AccountingPeriodCollectionView",
    "AccountingPeriodLockView",
    "CustomerWalletView",
    "LedgerAccountCollectionView",
    "LedgerAccountDetailView",
    "LedgerAdjustmentView",
    "LedgerTransactionCollectionView",
    "LedgerTransactionDetailView",
    "RefundView",
]

ACCOUNT_KINDS = (
    "customer_wallet",
    "provider_clearing",
    "revenue_energy",
    "fees",
    "suspense",
    "chargeback_receivable",
)
TRANSACTION_KINDS = (
    "topup_received",
    "energy_purchase",
    "fee",
    "refund",
    "adjustment",
    "reconciliation_fix",
    "reversal",
)
CURRENCIES = ("USD", "CDF")

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def _principal(request: Request) -> Principal:
    principal = getattr(request, "principal", None)
    if not isinstance(principal, Principal):
        raise Unauthenticated()
    return principal


def _limit(request: Request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return DEFAULT_LIMIT
    try:
        return max(1, min(int(raw), MAX_LIMIT))
    except ValueError as exc:
        raise InvalidRequest() from exc


def _csv(request: Request, name: str, allowed: tuple[str, ...]) -> list[str]:
    """A comma-separated filter, validated against the contract's enumeration."""
    raw = request.query_params.get(name) or ""
    values = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [value for value in values if value not in allowed]
    if unknown:
        raise InvalidRequest(details={"parameter": name, "unknown": unknown})
    return values


def _uuid_param(request: Request, name: str) -> UUID | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise InvalidRequest(details={"parameter": name}) from exc


def _currency(request: Request) -> str | None:
    raw = request.query_params.get("currency")
    if raw is None:
        return None
    if raw not in CURRENCIES:
        raise InvalidRequest(details={"parameter": "currency"})
    return raw


def _page_meta(limit: int, next_cursor: str | None) -> dict[str, Any]:
    return {"has_next": next_cursor is not None, "next_cursor": next_cursor, "limit": limit}


class CustomerWalletView(APIView):
    """``getCustomerWallet``: the customer's own balance, or finance reading one customer."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getCustomerWallet")
    def get(self, request: Request, customer_id: Any) -> Response:
        principal = _principal(request)
        if principal.kind == "customer" and principal.customer_id != customer_id:
            # 404, not 403: another customer's wallet must not be distinguishable from absent.
            raise LedgerNotFound()
        currency = _currency(request) or "USD"
        wallet = wiring.reader().wallet(customer_id, currency)
        return Response({"data": wallet_document(wallet)})


class LedgerAccountCollectionView(APIView):
    """``listLedgerAccounts``: read-only, keyset paginated on the account id."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listLedgerAccounts")
    def get(self, request: Request) -> Response:
        kinds = _csv(request, "type", ACCOUNT_KINDS)
        currency = _currency(request)
        owner = _uuid_param(request, "owner_customer_id")
        limit = _limit(request)
        rows = wiring.reader().list_accounts(
            kinds=kinds,
            currency=currency,
            customer_id=owner,
            after=decode_uuid_cursor(request.query_params.get("cursor")),
            limit=limit + 1,
        )
        next_cursor = encode_uuid_cursor(rows[limit - 1].id) if len(rows) > limit else None
        return Response(
            {
                "data": [account_document(row) for row in rows[:limit]],
                "meta": _page_meta(limit, next_cursor),
            }
        )


class LedgerAccountDetailView(APIView):
    """``getLedgerAccount``: one account and its balance."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getLedgerAccount")
    def get(self, request: Request, account_id: Any) -> Response:
        account = wiring.reader().account(account_id)
        if account is None:
            raise LedgerNotFound()
        return Response({"data": account_document(account)})


class LedgerTransactionCollectionView(APIView):
    """``listLedgerTransactions``: newest effective date first, keyset on the pair."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listLedgerTransactions")
    def get(self, request: Request) -> Response:
        limit = _limit(request)
        kinds = _csv(request, "kind", TRANSACTION_KINDS)
        rows = wiring.reader().list_transactions(
            account_id=_uuid_param(request, "account_id"),
            kinds=kinds,
            source_id=_uuid_param(request, "source_id"),
            posting_key=request.query_params.get("posting_key") or None,
            effective_after=_instant(request, "effective_after"),
            effective_before=_instant(request, "effective_before"),
            after=decode_transaction_cursor(request.query_params.get("cursor")),
            limit=limit + 1,
        )
        next_cursor = (
            encode_transaction_cursor(rows[limit - 1].effective_at, rows[limit - 1].id)
            if len(rows) > limit
            else None
        )
        return Response(
            {
                "data": [transaction_document(row) for row in rows[:limit]],
                "meta": _page_meta(limit, next_cursor),
            }
        )


class LedgerTransactionDetailView(APIView):
    """``getLedgerTransaction``: a transaction and every line of it."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getLedgerTransaction")
    def get(self, request: Request, transaction_id: Any) -> Response:
        return Response(
            {"data": transaction_document(wiring.service().transaction(transaction_id))}
        )


class LedgerAdjustmentView(APIView):
    """``createLedgerAdjustment``: propose; a second finance lead's approval posts it."""

    permission_classes = [PermissionsYamlPermission]

    @operation("createLedgerAdjustment")
    def post(self, request: Request) -> Response:
        principal = _principal(request)
        payload = AdjustmentCreateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        command = AdjustmentCreate(
            customer_id=data["customer_id"],
            direction=data["direction"],
            amount_minor=int(data["amount"]["amount_minor"]),
            currency=str(data["amount"]["currency"]),
            reason=str(data["reason"]),
            ticket_id=data.get("ticket_id"),
        )
        outcome = replay_or_run(
            request,
            scope=scope_for(request, "ledger/adjustments"),
            run=lambda: (
                202,
                {"data": dict(wiring.adjustments()(principal, command, ip=_ip(request)).document)},
            ),
            subject_customer_id=command.customer_id,
        )
        response = idempotent_response(outcome)
        response["Location"] = f"/api/v1/approvals/{outcome.body['data']['id']}"
        return response


class RefundView(APIView):
    """``createRefund``: propose a reversal of a received top-up."""

    permission_classes = [PermissionsYamlPermission]

    @operation("createRefund")
    def post(self, request: Request) -> Response:
        principal = _principal(request)
        payload = RefundCreateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        command = RefundCreate(
            top_up_id=data["top_up_id"],
            amount_minor=int(data["amount"]["amount_minor"]),
            currency=str(data["amount"]["currency"]),
            reason=str(data["reason"]),
        )
        outcome = replay_or_run(
            request,
            scope=scope_for(request, "refunds"),
            run=lambda: (
                202,
                {"data": dict(wiring.refunds()(principal, command, ip=_ip(request)).document)},
            ),
        )
        response = idempotent_response(outcome)
        response["Location"] = f"/api/v1/approvals/{outcome.body['data']['id']}"
        return response


class AccountingPeriodCollectionView(APIView):
    """``listAccountingPeriods``: the months, newest first, with their open-item count."""

    permission_classes = [PermissionsYamlPermission]

    @operation("listAccountingPeriods")
    def get(self, request: Request) -> Response:
        limit = _limit(request)
        statuses = _csv(request, "status", ("open", "locked"))
        rows = wiring.reader().list_periods(
            statuses=statuses,
            after=decode_uuid_cursor(request.query_params.get("cursor")),
            limit=limit + 1,
        )
        counter = wiring.open_exception_counter()
        documents = [
            period_document(
                row,
                open_exceptions=counter.open_count(row.period_start, row.period_end),
            )
            for row in rows[:limit]
        ]
        next_cursor = encode_uuid_cursor(rows[limit - 1].id) if len(rows) > limit else None
        return Response({"data": documents, "meta": _page_meta(limit, next_cursor)})


class AccountingPeriodLockView(APIView):
    """``lockAccountingPeriod``: propose the lock; the period changes when a peer approves."""

    permission_classes = [PermissionsYamlPermission]

    @operation("lockAccountingPeriod")
    def post(self, request: Request, period_id: Any) -> Response:
        principal = _principal(request)
        payload = PeriodLockInput(data=request.data)
        payload.is_valid(raise_exception=True)
        command = PeriodLockCommand(
            period_id=period_id, note=str(payload.validated_data.get("note") or "")
        )

        def propose() -> tuple[int, dict[str, Any]]:
            receipt = wiring.period_locks()(principal, command, ip=_ip(request))
            period = wiring.reader().period(period_id)
            if period is None:  # pragma: no cover - the use case already checked it
                raise LedgerNotFound()
            return 200, {
                "data": period_document(
                    period,
                    open_exceptions=wiring.open_exception_counter().open_count(
                        period.period_start, period.period_end
                    ),
                ),
                "approval_id": str(receipt.id),
            }

        outcome = replay_or_run(
            request,
            scope=scope_for(request, f"accounting-periods/{period_id}/lock"),
            run=propose,
        )
        body = dict(outcome.body)
        approval_id = body.pop("approval_id", None)
        response = idempotent_response(replace(outcome, body=body))
        if approval_id is not None:
            response["Location"] = f"/api/v1/approvals/{approval_id}"
        return response


def _instant(request: Request, name: str) -> Any:
    raw = request.query_params.get(name)
    if not raw:
        return None
    from django.utils.dateparse import parse_datetime  # noqa: PLC0415

    parsed = parse_datetime(raw)
    if parsed is None:
        raise InvalidRequest(details={"parameter": name})
    return parsed


def _ip(request: Request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return str(forwarded).split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR") or None
