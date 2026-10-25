"""Rotating refresh sessions with reuse detection, idle and absolute limits (D-17).

A refresh token is opaque: 32 random bytes, never a JWT, stored as ``sha256``. One login is one
*family*; every rotation spends the presented row and inserts its successor in the same
family. Three rules make replay useless:

* **Single use, no grace.** A row with ``rotated_at`` set that is presented again means the
  token leaked (the legitimate client already exchanged it), so the whole family is revoked
  and the caller gets ``refresh_token_reused``. There is no one-second window in which both
  the old and the new token work — that window is exactly what an attacker needs.
* **Idle limit** measured from the newest row's ``issued_at``; **absolute limit** measured
  from the family's first row. Both are per account kind (staff 30 min / 8 h, customer 5 min /
  12 h) and are injected, because the application layer may not read Django settings.
* **The session keeps its authentication instant.** A refreshed token must not silently reset
  the step-up clock, so ``auth_time`` and ``acr`` live beside the session id in the key/value
  store. If that entry is lost, the family's first row is the conservative fallback: an older
  instant, which can only make a step-up *more* likely to be demanded.

A customer session names a customer and no user; a staff session the other way round. The
service returns the ids and lets its caller build the ``Principal`` — that is where the two
account kinds diverge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from hub.identity.contracts import AuditLog, SessionRow, SessionStore
from hub.shared.contracts import KeyValueStore
from hub.shared.errors import Unauthenticated
from hub.shared.ports import Clock, IdGenerator
from hub.shared.security import digest_token, new_refresh_token

__all__ = ["Rotation", "SessionIssued", "SessionService", "SessionLimits"]

SESSION_AUTH_KEY = "session_auth:{session_id}"


@dataclass(frozen=True, slots=True)
class SessionLimits:
    """Idle and absolute lifetime of one account kind, in seconds."""

    idle_seconds: int
    absolute_seconds: int


@dataclass(frozen=True, slots=True)
class SessionIssued:
    """A freshly issued session: the token itself plus the row it was stored as."""

    token: str
    session_id: UUID
    family_id: UUID
    expires_in: int
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class Rotation:
    """The result of spending one refresh token: a new token and the session it continues."""

    token: str
    session_id: UUID
    family_id: UUID
    user_id: UUID | None
    customer_id: UUID | None
    auth_time: datetime
    acr: str
    expires_in: int


class SessionService:
    """Issues, rotates and revokes refresh sessions."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        store: KeyValueStore,
        sessions: SessionStore,
        audit: AuditLog,
        staff_limits: SessionLimits,
        customer_limits: SessionLimits,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._store = store
        self._sessions = sessions
        self._audit = audit
        self._limits = {"staff": staff_limits, "customer": customer_limits}

    def limits_for(self, kind: str) -> SessionLimits:
        return self._limits.get(kind, self._limits["staff"])

    # --- issuing -----------------------------------------------------------------------
    def issue(
        self,
        *,
        user_id: UUID | None,
        customer_id: UUID | None,
        auth_time: datetime,
        acr: str,
        ip: str | None = None,
        user_agent: str | None = None,
        family_id: UUID | None = None,
        issued_at: datetime | None = None,
    ) -> SessionIssued:
        """Open a session (or continue a family, for rotation)."""
        if (user_id is None) == (customer_id is None):
            raise ValueError("a session belongs to exactly one of a user or a customer")
        now = issued_at or self._clock.now()
        kind = "staff" if user_id is not None else "customer"
        limits = self.limits_for(kind)
        session_id = self._ids.new_id()
        token = new_refresh_token()
        row = SessionRow(
            id=session_id,
            user_id=user_id,
            customer_id=customer_id,
            family_id=family_id if family_id is not None else self._ids.new_id(),
            issued_at=now,
            expires_at=now + timedelta(seconds=limits.absolute_seconds),
            rotated_at=None,
            revoked_at=None,
        )
        self._sessions.insert(row, digest_token(token), ip=ip, user_agent=user_agent)
        self._remember(
            session_id, auth_time=auth_time, acr=acr, ttl_seconds=limits.absolute_seconds
        )
        return SessionIssued(
            token=token,
            session_id=session_id,
            family_id=row.family_id,
            expires_in=limits.absolute_seconds,
            issued_at=now,
        )

    # --- rotation -----------------------------------------------------------------------
    def rotate(
        self,
        presented: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> Rotation:
        """Spend ``presented`` and return its successor, or refuse the family."""
        session = self._sessions.find_by_hash(digest_token(presented), for_update=True)
        if session is None:
            raise Unauthenticated()

        now = self._clock.now()
        if session.rotated_at is not None or session.revoked_at is not None:
            self._revoke(session, action="auth.refresh.reuse", ip=ip)
            raise Unauthenticated(code="refresh_token_reused")
        if now >= session.expires_at:
            self._sessions.revoke(session.id, at=now)
            raise Unauthenticated(code="session_expired")

        limits = self.limits_for(session.kind)
        family_start = self._sessions.family_first_issued_at(session.family_id) or session.issued_at
        if (now - session.issued_at) > timedelta(seconds=limits.idle_seconds):
            self._revoke(session, action="auth.session.idle_expired", ip=ip)
            raise Unauthenticated(code="session_expired")
        if (now - family_start) > timedelta(seconds=limits.absolute_seconds):
            self._revoke(session, action="auth.session.absolute_expired", ip=ip)
            raise Unauthenticated(code="session_expired")

        auth_time, acr = self._recall(session, fallback=family_start)
        issued = self.issue(
            user_id=session.user_id,
            customer_id=session.customer_id,
            auth_time=auth_time,
            acr=acr,
            ip=ip,
            user_agent=user_agent,
            family_id=session.family_id,
            issued_at=now,
        )
        self._sessions.mark_rotated(session.id, at=now, replaced_by=issued.session_id)
        return Rotation(
            token=issued.token,
            session_id=issued.session_id,
            family_id=session.family_id,
            user_id=session.user_id,
            customer_id=session.customer_id,
            auth_time=auth_time,
            acr=acr,
            expires_in=issued.expires_in,
        )

    # --- ending -------------------------------------------------------------------------
    def logout(self, presented: str | None, *, ip: str | None = None) -> None:
        """Revoke the family of the presented token, if it names one this process can find."""
        if not presented:
            return
        session = self._sessions.find_by_hash(digest_token(presented))
        if session is None:
            return
        self._revoke(session, action="auth.logout", ip=ip)

    def revoke_family(self, family_id: UUID) -> int:
        """Revoke every row of a family (a role change, an MFA reset, an erasure)."""
        return self._sessions.revoke_family(family_id, at=self._clock.now())

    def remember_authentication(self, session_id: UUID, *, auth_time: datetime, acr: str) -> None:
        """Record a step-up (or a fresh login) against the session that performed it."""
        self._remember(
            session_id,
            auth_time=auth_time,
            acr=acr,
            ttl_seconds=self.limits_for("staff").absolute_seconds,
        )

    # --- internals ----------------------------------------------------------------------
    def _revoke(self, session: SessionRow, *, action: str, ip: str | None) -> None:
        self._sessions.revoke_family(session.family_id, at=self._clock.now())
        self._store.delete(SESSION_AUTH_KEY.format(session_id=session.id))
        self._audit.record(
            stream="auth",
            actor=None,
            action=action,
            entity_type="session",
            entity_id=session.id,
            site_id=None,
            details={"family_id": str(session.family_id)},
            outcome="denied",
            ip=ip,
            subject_customer_id=session.customer_id,
        )

    def _remember(
        self, session_id: UUID, *, auth_time: datetime, acr: str, ttl_seconds: int
    ) -> None:
        self._store.set(
            SESSION_AUTH_KEY.format(session_id=session_id),
            json.dumps({"auth_time": auth_time.isoformat(), "acr": acr}),
            ttl_seconds=ttl_seconds,
        )

    def _recall(self, session: SessionRow, *, fallback: datetime) -> tuple[datetime, str]:
        raw = self._store.get(SESSION_AUTH_KEY.format(session_id=session.id))
        if raw is None:
            # The store lost the entry: the family's start is the conservative answer, because
            # an earlier auth_time can only make a step-up more likely, never less.
            return fallback, "pwd"
        try:
            value: dict[str, Any] = json.loads(raw)
            return datetime.fromisoformat(str(value["auth_time"])), str(value["acr"])
        except (KeyError, ValueError, TypeError):
            return fallback, "pwd"
