"""Maker-checker: nothing that moves money or grants power happens in one pair of hands (D-15).

An approval is a stored proposal, not a flag on a request. The row carries the exact payload,
and the database's guard trigger freezes the payload and its hash, so a decision is always a
decision about what was proposed — not about what an executor received later. Five checks stand
between a proposal and its execution, in this order:

1. the row is still pending and has not expired;
2. the approver is a different person *and* a different session from the maker (the schema
   enforces both, and the service checks them first so the client gets a code, not a crash);
3. the approver's executor permissions and attribute allow them to decide this kind;
4. the payload hash the client shows matches the stored one (409 when it does not);
5. neither the maker nor the approver is linked to the customer the decision concerns.

Only then is the row marked approved, the executor runs **inside the same transaction**, and the
row is marked consumed. An exception anywhere rolls all three back, which is why a failed
execution leaves the approval pending rather than half-applied.

The API's sixteen kinds do not all map to a database action yet: four (period lock, period
reopen, and the two draft kinds) are written by later slices and their schema CHECK gains them
then. A kind with no mapping or no registered executor is refused, never stored, so an approval
can always be executed by something.
"""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from hub.identity.contracts import AuditLog, Principal
from hub.shared.contracts import UnitOfWork
from hub.shared.errors import (
    Conflict,
    InvalidCursor,
    NotFound,
    PermissionDenied,
    Unauthenticated,
    ValidationFailed,
)
from hub.shared.events import DomainEvent
from hub.shared.ports import Clock, IdGenerator

__all__ = [
    "ACTION_TO_KIND",
    "Executor",
    "ExecutorRegistry",
    "Approval",
    "ApprovalService",
    "KIND_TO_ACTION",
]

# The contract's kinds, and the schema action each is stored as (the schema names win).
KIND_TO_ACTION: dict[str, str] = {
    "reconciliation_resolution": "recon.resolve",
    "manual_adjustment": "ledger.adjustment",
    "refund": "ledger.refund",
    "tariff_change": "tariff.change",
    "role_grant": "role.grant",
    "user_creation": "user.create",
    "mfa_reset": "mfa.reset",
    "api_key_creation": "apikey.create",
    "erasure": "erasure.approve",
    "data_export": "data.export",
    "kb_publish": "kb.publish",
}
ACTION_TO_KIND: dict[str, str] = {action: kind for kind, action in KIND_TO_ACTION.items()}

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
CONSUMED = "consumed"
EXECUTED = "executed"

# The database's status vocabulary, mapped to the API's. `approved` only exists between the
# decision and the end of the transaction that executes it, so a reader never sees it.
DB_TO_API_STATUS = {
    PENDING: PENDING,
    APPROVED: EXECUTED,
    REJECTED: REJECTED,
    EXPIRED: EXPIRED,
    CONSUMED: EXECUTED,
}


class Executor(Protocol):
    """What one kind of approval does when it is approved."""

    kind: str
    required_permission: str
    required_attribute: str | None

    def execute(
        self, payload: Mapping[str, Any], principal: Principal, uow: UnitOfWork
    ) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class RegisteredExecutor:
    """An executor plus the two rights its approver must hold."""

    kind: str
    required_permission: str
    required_attribute: str | None
    execute: Callable[[Mapping[str, Any], Principal, UnitOfWork], Mapping[str, Any] | None]

    def may_propose(self, principal: Principal) -> bool:
        """Proposing needs the permission; the attribute gates deciding, not asking."""
        return self.required_permission in principal.permissions

    def allowed_for(self, principal: Principal) -> bool:
        """True when this approver may decide this kind."""
        if not self.may_propose(principal):
            return False
        return self.required_attribute is None or self.required_attribute in principal.attributes


class ExecutorRegistry:
    """The kinds this deployment can execute, each with its approver rights."""

    def __init__(self) -> None:
        self._executors: dict[str, RegisteredExecutor] = {}

    def register(
        self,
        kind: str,
        *,
        required_permission: str,
        required_attribute: str | None,
        execute: Callable[[Mapping[str, Any], Principal, UnitOfWork], Mapping[str, Any] | None],
    ) -> None:
        if kind not in KIND_TO_ACTION:
            raise ValueError(f"kind {kind!r} has no database action yet")
        self._executors[kind] = RegisteredExecutor(
            kind=kind,
            required_permission=required_permission,
            required_attribute=required_attribute,
            execute=execute,
        )

    def get(self, kind: str) -> RegisteredExecutor | None:
        return self._executors.get(kind)

    def kinds(self) -> frozenset[str]:
        return frozenset(self._executors)

    def decidable_by(self, principal: Principal) -> frozenset[str]:
        """The kinds this caller may approve — the only ones their list shows."""
        return frozenset(
            kind for kind, executor in self._executors.items() if executor.allowed_for(principal)
        )


@dataclass(frozen=True, slots=True)
class Approval:
    """One row of ``hub.approvals`` as the API shows it."""

    id: UUID
    kind: str
    status: str
    subject_type: str
    subject_id: UUID | None
    payload: Mapping[str, Any]
    payload_hash: str
    maker_id: UUID
    approver_id: UUID | None
    reason: str
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None
    expires_at: datetime
    result: Mapping[str, Any] | None = None

    def document(self) -> dict[str, Any]:
        """``Approval`` of the contract."""
        return {
            "id": str(self.id),
            "kind": self.kind,
            "status": self.status,
            "subject_type": self.subject_type,
            "subject_id": str(self.subject_id) if self.subject_id is not None else None,
            "payload": dict(self.payload),
            "payload_hash": self.payload_hash,
            "maker_id": str(self.maker_id),
            "approver_id": str(self.approver_id) if self.approver_id is not None else None,
            "reason": self.reason,
            "decision_note": self.decision_note,
            "created_at": _iso(self.created_at),
            "decided_at": _iso(self.decided_at) if self.decided_at is not None else None,
            "expires_at": _iso(self.expires_at),
            "result": dict(self.result) if self.result is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ApprovalPage:
    """One page of the approval queue."""

    approvals: Sequence[Approval]
    next_cursor: str | None


class ApprovalService:
    """Propose, list, read, approve and reject — the five contract operations."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        repository: Any,
        registry: ExecutorRegistry,
        audit: AuditLog,
        unit_of_work: Callable[[Principal], UnitOfWork],
        ttl_hours: int,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._repository = repository
        self._registry = registry
        self._audit = audit
        self._unit_of_work = unit_of_work
        self._ttl = timedelta(hours=ttl_hours)

    # --- proposing ----------------------------------------------------------------------
    def propose(
        self,
        principal: Principal,
        *,
        kind: str,
        payload: Mapping[str, Any],
        reason: str,
        subject_id: UUID | None = None,
        ip: str | None = None,
    ) -> Approval:
        """Store the proposal; nothing happens until somebody else approves it."""
        executor = self._registry.get(kind)
        if executor is None or not executor.may_propose(principal):
            # The caller either may not propose this kind, or this build cannot execute it yet.
            raise PermissionDenied(code="forbidden")
        if payload.get("reason") is None:
            raise ValidationFailed(
                details={
                    "errors": [
                        {
                            "field": "/payload/reason",
                            "code": "required",
                            "message": "The payload must carry the reason the action is proposed.",
                        }
                    ]
                }
            )

        now = self._clock.now()
        approval_id = self._ids.new_id()
        with self._unit_of_work(principal) as uow:
            row = self._repository.insert(
                approval_id=approval_id,
                action=KIND_TO_ACTION[kind],
                subject_customer_id=subject_id,
                maker_user_id=principal.subject_id,
                maker_session_id=self._session_of(principal),
                payload=dict(payload),
                expires_at=now + self._ttl,
                created_at=now,
            )
            self._publish(uow, "approval.requested", row, principal, now)
            self._audit.record(
                stream="admin",
                actor=principal,
                action="approval.requested",
                entity_type="approval",
                entity_id=approval_id,
                site_id=None,
                details={"kind": kind, "subject_id": str(subject_id) if subject_id else None},
                ip=ip,
                subject_customer_id=subject_id,
            )
        return self._document(row, now)

    # --- reading ------------------------------------------------------------------------
    def get(self, principal: Principal, approval_id: UUID) -> Approval:
        """One approval, if this caller may see its kind."""
        row = self._repository.get(approval_id)
        if row is None:
            raise NotFound()
        kind = ACTION_TO_KIND.get(str(row["action"]))
        if kind is None or kind not in self._registry.decidable_by(principal):
            raise NotFound()
        return self._document(row, self._clock.now())

    def list(
        self,
        principal: Principal,
        *,
        statuses: Sequence[str] = (),
        kinds: Sequence[str] = (),
        maker_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> ApprovalPage:
        """The queue this caller may decide, newest first."""
        allowed = self._registry.decidable_by(principal)
        wanted = frozenset(kinds) & allowed if kinds else allowed
        if not wanted:
            return ApprovalPage(approvals=[], next_cursor=None)
        after = _parse_cursor(cursor)
        rows = self._repository.page(
            actions=[KIND_TO_ACTION[kind] for kind in sorted(wanted)],
            statuses=[_db_status(status) for status in statuses],
            maker_id=maker_id,
            after=after,
            limit=limit + 1,
        )
        now = self._clock.now()
        approvals = [self._document(row, now) for row in rows[:limit]]
        next_cursor = _encode(rows[limit - 1]) if len(rows) > limit else None
        return ApprovalPage(approvals=approvals, next_cursor=next_cursor)

    # --- deciding -----------------------------------------------------------------------
    def approve(
        self,
        principal: Principal,
        approval_id: UUID,
        *,
        payload_hash: str | None = None,
        note: str | None = None,
        ip: str | None = None,
    ) -> Approval:
        """Decide, execute and consume — or roll the whole thing back."""
        with self._unit_of_work(principal) as uow:
            row = self._pending(approval_id, principal)
            kind = ACTION_TO_KIND[str(row["action"])]
            executor = self._decide_check(principal, kind)
            self._verify_hash(row, payload_hash)
            self._refuse_conflict(row, principal)

            decided_at = self._clock.now()
            self._repository.decide(
                approval_id,
                status=APPROVED,
                approver_user_id=principal.subject_id,
                approver_session_id=self._session_of(principal),
                decided_at=decided_at,
            )
            result = executor.execute(row["payload"], principal, uow)
            self._repository.mark_consumed(approval_id, at=decided_at)
            executed = self._repository.get(approval_id)
            if executed is None:  # pragma: no cover - locked in this transaction
                raise NotFound()
            approval = replace(
                self._document(executed, decided_at), result=dict(result) if result else None
            )
            self._publish(uow, "approval.granted", executed, principal, decided_at)
            self._audit.record(
                stream="admin",
                actor=principal,
                action="approval.granted",
                entity_type="approval",
                entity_id=approval_id,
                site_id=None,
                details={"kind": kind, "note": note},
                ip=ip,
                subject_customer_id=executed["subject_customer_id"],
            )
        return approval

    def reject(
        self,
        principal: Principal,
        approval_id: UUID,
        *,
        note: str | None = None,
        ip: str | None = None,
    ) -> Approval:
        """Refuse the proposal; the maker must start again with a new one."""
        with self._unit_of_work(principal) as uow:
            row = self._pending(approval_id, principal)
            kind = ACTION_TO_KIND[str(row["action"])]
            self._decide_check(principal, kind)

            decided_at = self._clock.now()
            self._repository.decide(
                approval_id,
                status=REJECTED,
                approver_user_id=principal.subject_id,
                approver_session_id=self._session_of(principal),
                decided_at=decided_at,
            )
            rejected = self._repository.get(approval_id)
            if rejected is None:  # pragma: no cover - locked in this transaction
                raise NotFound()
            self._publish(uow, "approval.rejected", rejected, principal, decided_at)
            self._audit.record(
                stream="admin",
                actor=principal,
                action="approval.rejected",
                entity_type="approval",
                entity_id=approval_id,
                site_id=None,
                details={"kind": kind, "note": note},
                ip=ip,
                subject_customer_id=rejected["subject_customer_id"],
            )
        return self._document(rejected, decided_at)

    # --- the five checks ----------------------------------------------------------------
    def _pending(self, approval_id: UUID, principal: Principal) -> Mapping[str, Any]:
        row = self._repository.get(approval_id, for_update=True)
        if row is None:
            raise NotFound()
        now = self._clock.now()
        if str(row["status"]) != PENDING or row["expires_at"] <= now:
            raise Conflict(code="approval_not_pending")
        if row["maker_user_id"] == principal.subject_id:
            raise PermissionDenied(code="self_approval_forbidden")
        if str(row["maker_session_id"]) == str(self._session_of(principal)):
            raise PermissionDenied(code="self_approval_forbidden")
        return row  # type: ignore[no-any-return]

    def _decide_check(self, principal: Principal, kind: str) -> RegisteredExecutor:
        executor = self._registry.get(kind)
        if executor is None or not executor.allowed_for(principal):
            raise PermissionDenied(code="forbidden")
        return executor

    @staticmethod
    def _verify_hash(row: Mapping[str, Any], presented: str | None) -> None:
        if presented is None:
            return
        if not secrets.compare_digest(str(presented), str(row["payload_hash"])):
            raise Conflict(code="approval_payload_mismatch")

    def _refuse_conflict(self, row: Mapping[str, Any], principal: Principal) -> None:
        customer_id = row["subject_customer_id"]
        if customer_id is None:
            return
        if self._repository.is_linked(
            principal.subject_id, customer_id
        ) or self._repository.is_linked(row["maker_user_id"], customer_id):
            raise PermissionDenied(code="conflict_of_interest")

    # --- helpers ------------------------------------------------------------------------
    def _document(self, row: Mapping[str, Any], now: datetime) -> Approval:
        kind = ACTION_TO_KIND[str(row["action"])]
        status = str(row["status"])
        if status == PENDING and row["expires_at"] <= now:
            status = EXPIRED
        payload = _payload(row["payload"])
        return Approval(
            id=row["id"],
            kind=kind,
            status=DB_TO_API_STATUS.get(status, status),
            subject_type="customer" if row["subject_customer_id"] else "platform",
            subject_id=row["subject_customer_id"],
            payload=payload,
            payload_hash=str(row["payload_hash"]),
            maker_id=row["maker_user_id"],
            approver_id=row["approver_user_id"],
            reason=str(payload.get("reason") or ""),
            decision_note=None,
            created_at=row["created_at"],
            decided_at=row["decided_at"],
            expires_at=row["expires_at"],
        )

    @staticmethod
    def _session_of(principal: Principal) -> UUID:
        if principal.session_id is None:
            # An approval is bound to a session, so a token without one cannot propose.
            raise Unauthenticated()
        return principal.session_id

    def _publish(
        self,
        uow: UnitOfWork,
        name: str,
        row: Mapping[str, Any],
        principal: Principal,
        now: datetime,
    ) -> None:
        uow.outbox.add(
            DomainEvent(
                event_id=self._ids.new_id(),
                name=name,
                version=1,
                occurred_at=now,
                correlation_id=self._ids.new_id(),
                causation_id=None,
                aggregate_type="approval",
                aggregate_id=row["id"],
                payload={
                    "approval_id": str(row["id"]),
                    "kind": ACTION_TO_KIND.get(str(row["action"]), "unknown"),
                    "status": DB_TO_API_STATUS.get(str(row["status"]), str(row["status"])),
                    "maker_id": str(principal.subject_id),
                },
            )
        )


def _payload(value: Any) -> dict[str, Any]:
    """A jsonb column arrives as text through this driver; a dict passes through."""
    if isinstance(value, Mapping):
        return dict(value)
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValidationFailed()
    return parsed


def _db_status(api_status: str) -> str:
    return CONSUMED if api_status == EXECUTED else api_status


def _iso(instant: datetime) -> str:
    return instant.isoformat().replace("+00:00", "Z")


def _encode(row: Mapping[str, Any]) -> str:
    """A keyset cursor: the created instant and the id, base64url-encoded."""
    raw = f"{row['created_at'].isoformat()}|{row['id']}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _parse_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        instant, _, raw_id = raw.partition("|")
        return datetime.fromisoformat(instant), UUID(raw_id)
    except (ValueError, TypeError) as exc:
        raise InvalidCursor() from exc
