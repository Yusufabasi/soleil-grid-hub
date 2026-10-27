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
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings

from hub.identity.application.approvals import ApprovalService, ExecutorRegistry
from hub.identity.application.customer_otp import CustomerOtpService, OtpLimits
from hub.identity.application.local_login import (
    EnrollmentService,
    LocalLogin,
    LoginThrottle,
    MfaService,
)
from hub.identity.application.oidc import OidcLogin
from hub.identity.application.proof import ProofService
from hub.identity.application.sessions import SessionLimits, SessionService
from hub.identity.application.step_up import StepUpService
from hub.identity.application.tokens import AccessTokenService
from hub.identity.application.user_admin import (
    CreateUserExecutor,
    GrantRoleExecutor,
    ResetMfaExecutor,
    UserAdminService,
)
from hub.identity.application.webauthn import WebauthnService
from hub.identity.contracts import AuditLog, CustomerLookup, Principal
from hub.identity.infrastructure.approvals import ApprovalRepository
from hub.identity.infrastructure.audit import HubAuditLog
from hub.identity.infrastructure.crypto import hmac_phone
from hub.identity.infrastructure.customer_otp import CustomerDirectory, OtpRepository
from hub.identity.infrastructure.notifications import LoggingNotifier
from hub.identity.infrastructure.repositories import SessionRepository, UserRepository
from hub.identity.infrastructure.user_admin import UserAdminRepository
from hub.shared import clock as kernel_clock
from hub.shared import registry
from hub.shared.clock import SystemClock
from hub.shared.contracts import KeyValueStore
from hub.shared.ids import Uuid7Generator
from hub.shared.ports import Clock
from hub.shared.redis import get_store
from hub.shared.uow import DjangoUnitOfWork, PrincipalContext, UnitOfWork

__all__ = [
    "actor_for_email",
    "add_approval_executor",
    "approval_registry",
    "approvals",
    "audit_log",
    "clock",
    "customer_lookup",
    "customer_otp",
    "ids",
    "last_customer_sign_in",
    "enrollment",
    "local_login",
    "mfa",
    "oidc",
    "proof",
    "publish_ports",
    "reset",
    "sessions",
    "step_up",
    "store",
    "tokens",
    "unit_of_work",
    "use_customer_lookup",
    "user_admin",
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
_step_up: StepUpService | None = None
# Executors other modules contribute at start-up (``tariff.change`` today, the ledger's and
# reconciliation's later). Composition, not cached state: :func:`reset` keeps this table, so a
# registry rebuilt for any reason still knows every kind this process can execute. Without it a
# reset would quietly turn an approvable action into "forbidden", which reads like a bug in the
# permission layer instead of a missing registration.
_contributions: dict[str, tuple[str, str | None, Any]] = {}

_approvals: ApprovalService | None = None
_approval_registry: ExecutorRegistry | None = None
_user_admin: UserAdminService | None = None
_proof: ProofService | None = None
_customer_otp: CustomerOtpService | None = None
_notifier: LoggingNotifier | None = None
# The customer lookup is a *registration*, like the denial recorder: `hub.customers` owns the
# rows and contributes the adapter at start-up (identity may import customers' contracts, never
# the reverse). ``reset`` keeps it — composition, not cache — and the fallback reads the table
# identity already knows how to read, so identity still answers before a registration lands.
_customer_lookup: CustomerLookup | None = None


def reset() -> None:
    """Drop every cached adapter; tests use it after changing a setting."""
    global _clock, _ids, _store, _tokens, _users, _sessions, _audit_log  # noqa: PLW0603
    global _local_login, _mfa, _enrollment, _webauthn, _oidc, _step_up  # noqa: PLW0603
    global _approvals, _approval_registry, _user_admin  # noqa: PLW0603
    global _proof, _customer_otp, _notifier  # noqa: PLW0603
    with _lock:
        _clock = _ids = _store = _tokens = _users = _sessions = _audit_log = None
        _local_login = _mfa = _enrollment = _webauthn = _oidc = _step_up = None
        _approvals = _approval_registry = _user_admin = None
        _proof = _customer_otp = _notifier = None


def use_clock(clock: Clock | None) -> None:
    """Install a clock (or, with ``None``, go back to the system one) and drop its adapters.

    A test seam, and the only reason this module has one: idle and absolute session limits and
    the step-up boundary are seconds apart, so proving them needs a frozen clock rather than a
    sleeping test. Production wiring never calls it.
    """
    global _clock  # noqa: PLW0603
    reset()
    # The permission class judges a step-up before any use case runs, so the kernel's clock
    # has to move with this one or that boundary could not be tested at all.
    kernel_clock.use_clock(clock)
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


def actor_for_email(email: str) -> Principal:
    """The caller a management command acts as: an existing, active user, proven by nobody.

    A command has no request and no token, so the identity it writes to the audit trail has to
    be resolved from the address the operator typed (``--actor-email``). The principal is a
    real one — roles, sites and locale are read from the database — because the use case it
    drives checks them; ``acr`` is ``mfa`` so a step-up rule would not be satisfied by a script
    that nobody watched. An unknown or inactive address raises ``ImportRefused``: the command
    refuses to run rather than inventing an actor.
    """
    from hub.shared.imports import ImportRefused  # noqa: PLC0415

    record = users().find_by_email(email.strip().lower())
    if record is None or record.status != "active":
        raise ImportRefused(f"no active user with the address {email!r}")
    return users().build_principal(record.id, session_id=None, auth_time=clock().now(), acr="mfa")


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


def step_up() -> StepUpService:
    """The second-factor re-check, sharing the WebAuthn verifier with login."""
    global _step_up  # noqa: PLW0603
    if _step_up is None:
        with _lock:
            if _step_up is None:
                _step_up = StepUpService(
                    clock=clock(),
                    store=store(),
                    users=users(),
                    audit=audit_log(),
                    webauthn=webauthn(),
                )
    return _step_up


def approval_registry() -> ExecutorRegistry:
    """The kinds this deployment can execute, with the rights each approver must hold.

    The registry is filled here, once per process: an approval kind with no executor would be a
    stored proposal nobody can ever execute, so every kind this build understands is registered
    at composition time. A test may register its own executor on the returned registry to
    replace one; :func:`reset` drops the whole registry with everything else.
    """
    global _approval_registry  # noqa: PLW0603
    if _approval_registry is None:
        with _lock:
            if _approval_registry is None:
                _approval_registry = _build_registry()
    return _approval_registry


def add_approval_executor(
    kind: str,
    *,
    required_permission: str,
    required_attribute: str | None,
    execute: Any,
) -> None:
    """Contribute an executor for ``kind``; it survives a later :func:`reset`.

    Called by a module that owns an approvable action (``customers`` registers
    ``tariff.change``), because `identity` may not import that module's application layer.
    """
    with _lock:
        _contributions[kind] = (required_permission, required_attribute, execute)
        if _approval_registry is not None:
            _approval_registry.register(
                kind,
                required_permission=required_permission,
                required_attribute=required_attribute,
                execute=execute,
            )


def _build_registry() -> ExecutorRegistry:
    """The executors this build can run, each with the permission its approver must hold."""
    registry = ExecutorRegistry()
    user_store = UserAdminRepository()
    registry.register(
        "user_creation",
        required_permission="user.admin",
        required_attribute=None,
        execute=CreateUserExecutor(store=user_store, audit=audit_log(), clock=clock(), ids=ids()),
    )
    registry.register(
        "role_grant",
        required_permission="user.admin",
        required_attribute=None,
        execute=GrantRoleExecutor(
            store=user_store, tokens=users(), audit=audit_log(), clock=clock()
        ),
    )
    registry.register(
        "mfa_reset",
        required_permission="user.admin",
        required_attribute=None,
        execute=ResetMfaExecutor(
            mfa=users(), sessions=SessionRepository(), audit=audit_log(), clock=clock()
        ),
    )
    for kind, (permission, attribute, execute) in _contributions.items():
        registry.register(
            kind,
            required_permission=permission,
            required_attribute=attribute,
            execute=execute,
        )
    return registry


def proof() -> ProofService:
    """Proof of work in front of the one-time-code endpoint (D-21)."""
    global _proof  # noqa: PLW0603
    if _proof is None:
        with _lock:
            if _proof is None:
                _proof = ProofService(
                    store=store(),
                    clock=clock(),
                    difficulty=int(settings.HUB_POW_DIFFICULTY_BITS),
                )
    return _proof


def notifier() -> LoggingNotifier:
    """Where codes would be sent: a recording adapter until a gateway is configured."""
    global _notifier  # noqa: PLW0603
    if _notifier is None:
        with _lock:
            if _notifier is None:
                _notifier = LoggingNotifier()
    return _notifier


def use_customer_lookup(lookup: CustomerLookup | None) -> None:
    """Install the customer lookup; ``hub.customers`` calls this at app start-up.

    The service that holds it is dropped so a lookup installed after the service was built
    still takes effect (the OTP tests build the service, then register).
    """
    global _customer_lookup, _customer_otp  # noqa: PLW0603
    with _lock:
        _customer_lookup = lookup
        _customer_otp = None


def customer_lookup() -> CustomerLookup:
    """The registered lookup, or identity's own reader of ``hub.customers`` until one lands."""
    return _customer_lookup if _customer_lookup is not None else CustomerDirectory()


def last_customer_sign_in(customer_id: UUID) -> datetime | None:
    """A customer's most recent completed sign-in, for the directory `customers` builds.

    Exposed as a function rather than left to the caller because the session rows are this
    module's; ``customers.wiring`` calls it, so the registry never reads ``refresh_tokens``.
    """
    return SessionRepository().last_sign_in_at(customer_id)


def customer_otp() -> CustomerOtpService:
    """Customer sign-in by one-time code, with the three hourly ceilings."""
    global _customer_otp  # noqa: PLW0603
    if _customer_otp is None:
        with _lock:
            if _customer_otp is None:
                _customer_otp = CustomerOtpService(
                    clock=clock(),
                    ids=ids(),
                    store=store(),
                    proofs=proof(),
                    otp_store=OtpRepository(),
                    customers=customer_lookup(),
                    notifier=notifier(),
                    sessions=sessions(),
                    tokens=tokens(),
                    phone_hmac=hmac_phone,
                    limits=OtpLimits(
                        per_phone=int(settings.HUB_OTP_PER_PHONE_HOURLY),
                        per_ip=int(settings.HUB_OTP_PER_IP_HOURLY),
                        per_network=int(settings.HUB_OTP_PER_NETWORK_HOURLY),
                    ),
                    allowed_prefixes=tuple(settings.HUB_OTP_ALLOWED_PREFIXES),
                    ttl_seconds=int(settings.HUB_OTP_TTL_SECONDS),
                    dormant_days=int(settings.HUB_OTP_DORMANT_DAYS),
                )
    return _customer_otp


def user_admin() -> UserAdminService:
    """The administrator's surface: the catalogue, the accounts and the four proposals."""
    global _user_admin  # noqa: PLW0603
    if _user_admin is None:
        with _lock:
            if _user_admin is None:
                _user_admin = UserAdminService(
                    clock=clock(),
                    approvals=approvals(),
                    store=UserAdminRepository(),
                    audit=audit_log(),
                )
    return _user_admin


def publish_ports() -> None:
    """Publish the services a lower module may not import (hub.shared.registry, CR-002).

    ``ledger`` sits below this module in the dependency order, and the import contract lets it
    see ``identity.contracts`` and ``identity.events`` only. The four callables below are what
    it needs, published under names rather than reached through an import. Factories, not
    instances: whichever ``ready()`` runs first, the consumer resolves the current object.
    """
    registry.provide("identity.approvals", approvals)
    registry.provide("identity.audit", audit_log)
    registry.provide("identity.unit_of_work", unit_of_work)
    registry.provide("identity.register_approval_executor", add_approval_executor)


def approvals() -> ApprovalService:
    """Maker-checker: the queue, the decision and the executor that runs on approval."""
    global _approvals  # noqa: PLW0603
    if _approvals is None:
        with _lock:
            if _approvals is None:
                _approvals = ApprovalService(
                    clock=clock(),
                    ids=ids(),
                    repository=ApprovalRepository(),
                    registry=approval_registry(),
                    audit=audit_log(),
                    unit_of_work=unit_of_work,
                    ttl_hours=int(settings.HUB_APPROVAL_TTL_HOURS),
                )
    return _approvals


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
