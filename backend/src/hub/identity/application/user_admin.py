"""User administration: who exists, what they may do, and the two pairs of hands that change it.

Two kinds of operation live here and they are deliberately not the same shape (D-13, D-15):

* **Proposing** — creating an account, granting roles, changing assigned sites and resetting a
  second factor are all privileged. The administrator stores a proposal and a *different*
  administrator decides it. Nothing is written to ``hub.users`` before that decision, which is
  why ``POST /users`` answers 202 with an approval and ``GET /users`` does not show the account.
* **Updating** — a display name, a status or a locale is not a grant of power, so
  ``PATCH /users/{id}`` writes it directly and audits it. Suspending bumps ``token_version``,
  because a suspended account must lose its access tokens now rather than within ten minutes.

The two invariants the contract adds — at most three administrators, and the last one cannot be
demoted — are checked when the proposal is made, so the administrator finds out immediately
rather than after a colleague has approved something impossible.

The DTOs live here rather than in the adapter: the application layer owns the shape it reasons
about, and the SQL adapter imports them (never the other way round — the layer contract).
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from hub.identity.application.approvals import Approval, ApprovalService
from hub.identity.contracts import AuditLog, Principal
from hub.shared.contracts import UnitOfWork
from hub.shared.errors import Conflict, Duplicate, InvalidCursor, NotFound
from hub.shared.ports import Clock, IdGenerator

__all__ = [
    "CreateUserExecutor",
    "GrantRoleExecutor",
    "ResetMfaExecutor",
    "RoleRow",
    "UserAdminService",
    "UserPage",
    "UserRow",
]

# The contract's ceiling on administrators; above it a grant needs a design decision, not a click.
MAX_ADMINISTRATORS = 3

KNOWN_ATTRIBUTES = ("finance_lead", "dp_admin")


@dataclass(frozen=True, slots=True)
class UserRow:
    """One staff account as the API shows it: the row plus its roles, sites and factors."""

    id: UUID
    email: str
    display_name: str
    status: str
    preferred_language: str
    finance_lead: bool
    dp_admin: bool
    mfa_enrolled: bool
    webauthn_enrolled: bool
    last_login_at: datetime | None
    created_at: datetime
    roles: tuple[str, ...]
    site_ids: tuple[UUID, ...]

    def document(self) -> dict[str, Any]:
        """``api/openapi.yaml`` User."""
        attributes = [
            name
            for name, held in (("finance_lead", self.finance_lead), ("dp_admin", self.dp_admin))
            if held
        ]
        return {
            "id": str(self.id),
            "email": self.email,
            "full_name": self.display_name,
            "status": self.status,
            "roles": list(self.roles),
            "attributes": attributes,
            "site_ids": [str(site_id) for site_id in self.site_ids],
            "mfa_enrolled": self.mfa_enrolled,
            "webauthn_enrolled": self.webauthn_enrolled,
            "locale": "fr" if self.preferred_language == "fr" else "en",
            "last_login_at": _iso(self.last_login_at),
            "created_at": _iso(self.created_at) or "",
        }


@dataclass(frozen=True, slots=True)
class RoleRow:
    """One role with its permissions (``api/openapi.yaml`` Role)."""

    name: str
    description: str
    permissions: tuple[str, ...]

    def document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "permissions": list(self.permissions),
        }


@dataclass(frozen=True, slots=True)
class UserPage:
    users: Sequence[UserRow]
    next_cursor: str | None


def encode_cursor(row: UserRow) -> str:
    """A keyset cursor: the created instant and the id, base64url-encoded."""
    raw = f"{row.created_at.isoformat()}|{row.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """The reverse; anything that does not decode is the caller's ``InvalidCursor``."""
    padded = cursor + "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(padded.encode()).decode()
    instant, _, raw_id = raw.partition("|")
    return datetime.fromisoformat(instant), UUID(raw_id)


class UserAdminStore(Protocol):
    """What the admin surface reads and writes directly."""

    def get(self, user_id: UUID) -> UserRow | None: ...

    def page(
        self,
        *,
        statuses: Sequence[str],
        role: str | None,
        query: str | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[UserRow]: ...

    def email_taken(self, email: str) -> bool: ...

    def known_sites(self, site_ids: Sequence[UUID]) -> frozenset[UUID]: ...

    def administrators(self) -> list[UUID]: ...

    def roles(self) -> list[RoleRow]: ...

    def insert(
        self,
        *,
        user_id: UUID,
        email: str,
        display_name: str,
        status: str,
        locale: str,
        roles: Sequence[str],
        attributes: Sequence[str],
        site_ids: Sequence[UUID],
        at: datetime,
    ) -> None: ...

    def replace_roles(
        self,
        user_id: UUID,
        *,
        roles: Sequence[str],
        attributes: Sequence[str],
        granted_by: UUID | None,
        at: datetime,
    ) -> None: ...

    def replace_sites(self, user_id: UUID, *, site_ids: Sequence[UUID]) -> None: ...

    def update_profile(
        self,
        user_id: UUID,
        *,
        display_name: str | None,
        status: str | None,
        locale: str | None,
        at: datetime,
    ) -> None: ...


class MfaStore(Protocol):
    """The two writes an MFA reset and a role grant need from the identity repository."""

    def clear_mfa(self, user_id: UUID) -> None: ...

    def bump_token_version(self, user_id: UUID) -> int: ...


class SessionRevoker(Protocol):
    def revoke_all_for_user(self, user_id: UUID, *, at: datetime) -> int: ...


class UserAdminService:
    """The eight operations of ``/users`` and ``/roles``."""

    def __init__(
        self,
        *,
        clock: Clock,
        approvals: ApprovalService,
        store: UserAdminStore,
        audit: AuditLog,
    ) -> None:
        self._clock = clock
        self._approvals = approvals
        self._store = store
        self._audit = audit

    # --- reads -------------------------------------------------------------------------
    def list(
        self,
        principal: Principal,
        *,
        statuses: Sequence[str] = (),
        role: str | None = None,
        query: str | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> UserPage:
        """One page of accounts, newest first; the cursor is the same keyset as approvals."""
        rows = self._store.page(
            statuses=statuses,
            role=role,
            query=query,
            after=_after(cursor),
            limit=limit + 1,
        )
        users = rows[:limit]
        next_cursor = encode_cursor(users[-1]) if len(rows) > limit and users else None
        return UserPage(users=users, next_cursor=next_cursor)

    def get(self, principal: Principal, user_id: UUID) -> UserRow:
        return self._require(user_id)

    def roles(self, principal: Principal) -> Sequence[RoleRow]:
        """The role catalogue and the permissions each role holds.

        ``Sequence`` rather than ``list``: the method called ``list`` above shadows the builtin
        inside this class body, and a catalogue does not need to be mutable.
        """
        return self._store.roles()

    # --- proposals ---------------------------------------------------------------------
    def create(
        self,
        principal: Principal,
        *,
        email: str,
        full_name: str,
        roles: Sequence[str],
        attributes: Sequence[str] = (),
        site_ids: Sequence[UUID] = (),
        locale: str = "fr",
        reason: str,
        ip: str | None = None,
    ) -> Approval:
        """Store the proposal; the account exists only when somebody else approves it."""
        if self._store.email_taken(email):
            raise Duplicate()
        self._require_sites(site_ids)
        return self._approvals.propose(
            principal,
            kind="user_creation",
            payload={
                "email": email.strip().lower(),
                "full_name": full_name,
                "roles": list(roles),
                "attributes": _attributes(attributes),
                "site_ids": [str(site_id) for site_id in site_ids],
                "locale": "fr" if locale == "fr" else "en",
                "reason": reason,
            },
            reason=reason,
            ip=ip,
        )

    def replace_roles(
        self,
        principal: Principal,
        user_id: UUID,
        *,
        roles: Sequence[str],
        attributes: Sequence[str] = (),
        reason: str,
        ip: str | None = None,
    ) -> Approval:
        """Propose the new role set, refusing a demotion that would strand the platform."""
        user = self._require(user_id)
        self._check_administrator_ceiling(user, roles)
        return self._approvals.propose(
            principal,
            kind="role_grant",
            payload={
                "change": "roles",
                "user_id": str(user_id),
                "roles": list(roles),
                "attributes": _attributes(attributes),
                "reason": reason,
            },
            reason=reason,
            ip=ip,
        )

    def replace_sites(
        self,
        principal: Principal,
        user_id: UUID,
        *,
        site_ids: Sequence[UUID],
        reason: str,
        ip: str | None = None,
    ) -> Approval:
        self._require(user_id)
        self._require_sites(site_ids)
        return self._approvals.propose(
            principal,
            kind="role_grant",
            payload={
                "change": "sites",
                "user_id": str(user_id),
                "site_ids": [str(site_id) for site_id in site_ids],
                "reason": reason,
            },
            reason=reason,
            ip=ip,
        )

    def reset_mfa(
        self, principal: Principal, user_id: UUID, *, reason: str, ip: str | None = None
    ) -> Approval:
        """Propose the reset; the factor keeps working until another administrator decides."""
        self._require(user_id)
        return self._approvals.propose(
            principal,
            kind="mfa_reset",
            payload={"user_id": str(user_id), "reason": reason},
            reason=reason,
            ip=ip,
        )

    # --- the direct write ---------------------------------------------------------------
    def update(
        self,
        principal: Principal,
        user_id: UUID,
        *,
        full_name: str | None = None,
        status: str | None = None,
        locale: str | None = None,
        ip: str | None = None,
    ) -> UserRow:
        """Change the three fields that are not a grant of authority, and audit it."""
        user = self._require(user_id)
        now = self._clock.now()
        self._store.update_profile(
            user_id, display_name=full_name, status=status, locale=locale, at=now
        )
        self._audit.record(
            stream="admin",
            actor=principal,
            action="user.update",
            entity_type="user",
            entity_id=user_id,
            site_id=None,
            details={
                "full_name": full_name,
                "status": status,
                "locale": locale,
                "previous_status": user.status,
            },
            ip=ip,
            subject_customer_id=None,
        )
        return self._require(user_id)

    # --- checks ------------------------------------------------------------------------
    def _require(self, user_id: UUID) -> UserRow:
        user = self._store.get(user_id)
        if user is None:
            raise NotFound()
        return user

    def _require_sites(self, site_ids: Sequence[UUID]) -> None:
        if set(site_ids) - self._store.known_sites(site_ids):
            raise NotFound()

    def _check_administrator_ceiling(self, user: UserRow, roles: Sequence[str]) -> None:
        """At most three administrators, and never fewer than one (the contract's rule)."""
        holders = set(self._store.administrators())
        was = "administrator" in user.roles
        will = "administrator" in roles
        if was and not will and user.id in holders and len(holders) <= 1:
            raise Conflict(details={"reason": "last_administrator"})
        if will and not was and len(holders) >= MAX_ADMINISTRATORS:
            raise Conflict(details={"reason": "administrator_limit"})


# --- executors ------------------------------------------------------------------------
# These run inside the decision's transaction: an exception anywhere rolls the decision,
# the executor's writes and the audit row back together (D-15).


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"approval payload is missing {key!r}")
    return value


def _texts(payload: Mapping[str, Any], key: str) -> list[str]:
    value = payload.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"approval payload field {key!r} is not a list")
    return [str(item) for item in value]


def _uuids(payload: Mapping[str, Any], key: str) -> list[UUID]:
    return [UUID(item) for item in _texts(payload, key)]


class CreateUserExecutor:
    """``user.create``: the account, its roles and its sites, once a second admin agrees."""

    def __init__(
        self, *, store: UserAdminStore, audit: AuditLog, clock: Clock, ids: IdGenerator
    ) -> None:
        self._store = store
        self._audit = audit
        self._clock = clock
        self._ids = ids

    def __call__(
        self, payload: Mapping[str, Any], principal: Principal, uow: UnitOfWork
    ) -> Mapping[str, Any]:
        email = _text(payload, "email")
        if self._store.email_taken(email):
            # The address was taken between proposal and decision: a conflict, not a crash.
            raise Duplicate()
        site_ids = _uuids(payload, "site_ids")
        if set(site_ids) - self._store.known_sites(site_ids):
            raise NotFound()
        roles = _texts(payload, "roles")
        user_id = self._ids.new_id()
        self._store.insert(
            user_id=user_id,
            email=email,
            display_name=_text(payload, "full_name"),
            status="invited",
            locale="fr" if payload.get("locale") == "fr" else "en",
            roles=roles,
            attributes=_texts(payload, "attributes"),
            site_ids=site_ids,
            at=self._clock.now(),
        )
        # No address in ``details``: the audit log is not allowed to carry personal data, and
        # ``entity_id`` already names the account the address belongs to.
        self._audit.record(
            stream="admin",
            actor=principal,
            action="user.create",
            entity_type="user",
            entity_id=user_id,
            site_id=None,
            details={"roles": roles},
            ip=None,
            subject_customer_id=None,
        )
        return {"user_id": str(user_id)}


class GrantRoleExecutor:
    """``role.grant``: one action, two payload shapes — roles and attributes, or sites.

    ``replaceUserSites`` is stored as ``role.grant`` too (the schema has no separate action), so
    the payload says which half changed and only that half is written.
    """

    def __init__(self, *, store: UserAdminStore, tokens: MfaStore, audit: AuditLog, clock: Clock):
        self._store = store
        self._tokens = tokens
        self._audit = audit
        self._clock = clock

    def __call__(
        self, payload: Mapping[str, Any], principal: Principal, uow: UnitOfWork
    ) -> Mapping[str, Any]:
        user_id = UUID(_text(payload, "user_id"))
        change = _text(payload, "change")
        if change == "sites":
            site_ids = _uuids(payload, "site_ids")
            if set(site_ids) - self._store.known_sites(site_ids):
                raise NotFound()
            self._store.replace_sites(user_id, site_ids=site_ids)
            action = "user.sites"
        else:
            self._store.replace_roles(
                user_id,
                roles=_texts(payload, "roles"),
                attributes=_texts(payload, "attributes"),
                granted_by=principal.subject_id,
                at=self._clock.now(),
            )
            action = "user.roles"
        # Authority changed: every access token this user holds must die (D-17).
        self._tokens.bump_token_version(user_id)
        self._audit.record(
            stream="admin",
            actor=principal,
            action=action,
            entity_type="user",
            entity_id=user_id,
            site_id=None,
            details={"change": change},
            ip=None,
            subject_customer_id=None,
        )
        return {"user_id": str(user_id), "change": change}


class ResetMfaExecutor:
    """``mfa.reset``: forget the factor, kill the sessions, force re-enrolment (TC-FR-15-11)."""

    def __init__(
        self, *, mfa: MfaStore, sessions: SessionRevoker, audit: AuditLog, clock: Clock
    ) -> None:
        self._mfa = mfa
        self._sessions = sessions
        self._audit = audit
        self._clock = clock

    def __call__(
        self, payload: Mapping[str, Any], principal: Principal, uow: UnitOfWork
    ) -> Mapping[str, Any]:
        user_id = UUID(_text(payload, "user_id"))
        self._mfa.clear_mfa(user_id)
        revoked = self._sessions.revoke_all_for_user(user_id, at=self._clock.now())
        self._mfa.bump_token_version(user_id)
        self._audit.record(
            stream="admin",
            actor=principal,
            action="user.mfa_reset",
            entity_type="user",
            entity_id=user_id,
            site_id=None,
            details={"sessions_revoked": revoked},
            ip=None,
            subject_customer_id=None,
        )
        return {"user_id": str(user_id), "sessions_revoked": revoked}


def _attributes(attributes: Sequence[str]) -> list[str]:
    return [name for name in attributes if name in KNOWN_ATTRIBUTES]


def _after(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        return decode_cursor(cursor)
    except (ValueError, TypeError) as exc:
        raise InvalidCursor() from exc


def _iso(instant: datetime | None) -> str | None:
    if instant is None:
        return None
    return instant.isoformat().replace("+00:00", "Z")
