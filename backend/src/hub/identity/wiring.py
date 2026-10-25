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
from urllib.parse import urlsplit

from django.conf import settings

from hub.identity.application.local_login import (
    EnrollmentService,
    LocalLogin,
    LoginThrottle,
    MfaService,
)
from hub.identity.application.oidc import OidcLogin
from hub.identity.application.sessions import SessionLimits, SessionService
from hub.identity.application.tokens import AccessTokenService
from hub.identity.application.webauthn import WebauthnService
from hub.identity.contracts import AuditLog, Principal
from hub.identity.infrastructure.audit import HubAuditLog
from hub.identity.infrastructure.repositories import SessionRepository, UserRepository
from hub.shared.clock import SystemClock
from hub.shared.contracts import KeyValueStore
from hub.shared.ids import Uuid7Generator
from hub.shared.ports import Clock
from hub.shared.redis import get_store
from hub.shared.uow import DjangoUnitOfWork, PrincipalContext, UnitOfWork

__all__ = [
    "audit_log",
    "clock",
    "ids",
    "enrollment",
    "local_login",
    "mfa",
    "oidc",
    "reset",
    "sessions",
    "store",
    "tokens",
    "unit_of_work",
    "users",
    "webauthn",
]

_lock = threading.RLock()
_clock: Clock | None = None
_ids: Uuid7Generator | None = None
_store: KeyValueStore | None = None
_tokens: AccessTokenService | None = None
_users: UserRepository | None = None
_sessions: SessionService | None = None
_audit_log: AuditLog | None = None
_local_login: LocalLogin | None = None
_mfa: MfaService | None = None
_enrollment: EnrollmentService | None = None
_webauthn: WebauthnService | None = None
_oidc: OidcLogin | None = None


def reset() -> None:
    """Drop every cached adapter; tests use it after changing a setting."""
    global _clock, _ids, _store, _tokens, _users, _sessions, _audit_log  # noqa: PLW0603
    global _local_login, _mfa, _enrollment, _webauthn, _oidc  # noqa: PLW0603
    with _lock:
        _clock = _ids = _store = _tokens = _users = _sessions = _audit_log = None
        _local_login = _mfa = _enrollment = _webauthn = _oidc = None


def use_clock(clock: Clock) -> None:
    """Install a clock and drop the adapters that captured the previous one.

    A test seam, and the only reason this module has one: idle and absolute session limits and
    the step-up boundary are seconds apart, so proving them needs a frozen clock rather than a
    sleeping test. Production wiring never calls it.
    """
    global _clock  # noqa: PLW0603
    reset()
    with _lock:
        _clock = clock


def clock() -> Clock:
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


def sessions() -> SessionService:
    """The refresh-session service, with the idle and absolute limits from settings."""
    global _sessions  # noqa: PLW0603
    if _sessions is None:
        with _lock:
            if _sessions is None:
                _sessions = SessionService(
                    clock=clock(),
                    ids=ids(),
                    store=store(),
                    sessions=SessionRepository(),
                    audit=audit_log(),
                    staff_limits=SessionLimits(
                        idle_seconds=int(settings.HUB_STAFF_IDLE_SECONDS),
                        absolute_seconds=int(settings.HUB_STAFF_ABSOLUTE_SECONDS),
                    ),
                    customer_limits=SessionLimits(
                        idle_seconds=int(settings.HUB_CUSTOMER_IDLE_SECONDS),
                        absolute_seconds=int(settings.HUB_CUSTOMER_ABSOLUTE_SECONDS),
                    ),
                )
    return _sessions


def _throttle() -> LoginThrottle:
    return LoginThrottle(store())


def local_login() -> LocalLogin:
    """Step one of the break-glass flow, with the account and address lockout."""
    global _local_login  # noqa: PLW0603
    if _local_login is None:
        with _lock:
            if _local_login is None:
                _local_login = LocalLogin(
                    clock=clock(),
                    ids=ids(),
                    store=store(),
                    users=users(),
                    audit=audit_log(),
                    throttle=_throttle(),
                    webauthn_enforced=bool(settings.HUB_WEBAUTHN_ENFORCED),
                )
    return _local_login


def mfa() -> MfaService:
    """Step two: the TOTP or recovery code and the session it opens."""
    global _mfa  # noqa: PLW0603
    if _mfa is None:
        with _lock:
            if _mfa is None:
                _mfa = MfaService(
                    clock=clock(),
                    store=store(),
                    users=users(),
                    sessions=sessions(),
                    audit=audit_log(),
                    webauthn_enforced=bool(settings.HUB_WEBAUTHN_ENFORCED),
                )
    return _mfa


def enrollment() -> EnrollmentService:
    """TOTP enrolment and recovery-code rotation."""
    global _enrollment  # noqa: PLW0603
    if _enrollment is None:
        with _lock:
            if _enrollment is None:
                _enrollment = EnrollmentService(clock=clock(), store=store(), users=users())
    return _enrollment


def webauthn() -> WebauthnService:
    """WebAuthn registration and assertion, relying on this site's origin."""
    global _webauthn  # noqa: PLW0603
    if _webauthn is None:
        with _lock:
            if _webauthn is None:
                origin = str(settings.SITE_ORIGIN)
                _webauthn = WebauthnService(
                    clock=clock(),
                    ids=ids(),
                    store=store(),
                    users=users(),
                    rp_id=urlsplit(origin).hostname or "localhost",
                    rp_name=str(settings.HUB_WEBAUTHN_RP_NAME),
                    origin=origin,
                )
    return _webauthn


def oidc() -> OidcLogin:
    """The company identity provider client, configured from settings (D-47)."""
    global _oidc  # noqa: PLW0603
    if _oidc is None:
        with _lock:
            if _oidc is None:
                _oidc = OidcLogin(
                    clock=clock(),
                    store=store(),
                    users=users(),
                    sessions=sessions(),
                    audit=audit_log(),
                    issuer=str(settings.HUB_OIDC_ISSUER),
                    client_id=str(settings.HUB_OIDC_CLIENT_ID),
                    client_secret=str(settings.HUB_OIDC_CLIENT_SECRET),
                    redirect_uri=str(settings.HUB_OIDC_REDIRECT_URI),
                )
    return _oidc


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
