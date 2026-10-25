"""Composition of the identity module (docs/03 section 3.3): the only place that names adapters.

The api and application layers depend on the Protocols in ``identity.contracts`` and never
import ``hub.identity.infrastructure``; this module is the seam that turns those Protocols
into the concrete adapters. It is a package-root module on purpose: the layer contract
(``.importlinter``, contract "Layers inside each module") covers ``api``, ``application`` and
``domain``, and wiring sits outside them, exactly like ``bootstrap`` does for the process.

The services are cached per process because they are stateless; the clock and the id
generator are too. Tests that need a different clock or store call :func:`reset`.
"""

from __future__ import annotations

import threading

from django.conf import settings

from hub.identity.application.tokens import AccessTokenService
from hub.identity.contracts import AuditLog, Principal
from hub.identity.infrastructure.audit import HubAuditLog
from hub.identity.infrastructure.repositories import UserRepository
from hub.shared.clock import SystemClock
from hub.shared.contracts import KeyValueStore
from hub.shared.ids import Uuid7Generator
from hub.shared.redis import get_store
from hub.shared.uow import DjangoUnitOfWork, PrincipalContext, UnitOfWork

__all__ = [
    "audit_log",
    "clock",
    "ids",
    "reset",
    "store",
    "tokens",
    "unit_of_work",
    "users",
]

_lock = threading.RLock()
_clock: SystemClock | None = None
_ids: Uuid7Generator | None = None
_store: KeyValueStore | None = None
_tokens: AccessTokenService | None = None
_users: UserRepository | None = None
_audit_log: AuditLog | None = None


def reset() -> None:
    """Drop every cached adapter; tests use it after changing a setting."""
    global _clock, _ids, _store, _tokens, _users, _audit_log  # noqa: PLW0603
    with _lock:
        _clock = _ids = _store = _tokens = _users = _audit_log = None


def clock() -> SystemClock:
    """The process clock: UTC, tz-aware (docs/03 2.1)."""
    global _clock  # noqa: PLW0603
    if _clock is None:
        with _lock:
            if _clock is None:
                _clock = SystemClock()
    return _clock


def ids() -> Uuid7Generator:
    """UUIDv7 ids taken from the clock, so a frozen clock reproduces them."""
    global _ids  # noqa: PLW0603
    if _ids is None:
        with _lock:
            if _ids is None:
                _ids = Uuid7Generator(clock())
    return _ids


def store() -> KeyValueStore:
    """Redis, through the kernel's port."""
    global _store  # noqa: PLW0603
    if _store is None:
        with _lock:
            if _store is None:
                _store = get_store()
    return _store


def tokens() -> AccessTokenService:
    """The access-token service, keyed by the settings' Ed25519 pair."""
    global _tokens  # noqa: PLW0603
    if _tokens is None:
        with _lock:
            if _tokens is None:
                _tokens = AccessTokenService(
                    clock=clock(),
                    ids=ids(),
                    private_key=str(settings.HUB_JWT_PRIVATE_KEY),
                    public_key=str(settings.HUB_JWT_PUBLIC_KEY),
                    issuer=str(settings.HUB_JWT_ISSUER),
                    audience=str(settings.HUB_JWT_AUDIENCE),
                    ttl_seconds=int(settings.HUB_ACCESS_TTL_SECONDS),
                    leeway_seconds=int(settings.HUB_CLOCK_LEEWAY_SECONDS),
                )
    return _tokens


def users() -> UserRepository:
    """The user repository over the shared store."""
    global _users  # noqa: PLW0603
    if _users is None:
        with _lock:
            if _users is None:
                _users = UserRepository(store())
    return _users


def audit_log() -> AuditLog:
    """The process-wide audit adapter: stateless, so one instance serves every request."""
    global _audit_log  # noqa: PLW0603
    if _audit_log is None:
        with _lock:
            if _audit_log is None:
                _audit_log = HubAuditLog()
    return _audit_log


def unit_of_work(principal: Principal | None = None) -> UnitOfWork:
    """A transaction that announces the caller to the database (``app.user_id``, D-22).

    The setting is transaction-local, so the policies and ``hub.audit_page`` read this
    request's identity and never the previous one on a pooled connection (docs/04 10.9).
    """
    if principal is None:
        return DjangoUnitOfWork()
    user_id = principal.subject_id if principal.kind == "staff" else None
    return DjangoUnitOfWork(
        principal=PrincipalContext(user_id=user_id, customer_id=principal.customer_id)
    )
