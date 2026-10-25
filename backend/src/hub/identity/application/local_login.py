"""Break-glass staff login: password, then TOTP or a recovery code (D-16, docs/05 4).

This is the flow that has to work when the company identity provider does not. Four
properties are load-bearing and each is enforced here rather than hoped for:

* **Indistinguishable failures.** An address with no account is verified against a real
  Argon2id hash of a throwaway value and answered exactly like a wrong password, so neither
  the body nor the timing says whether the account exists.
* **Progressive lockout.** Failures are counted per account and per address in one sliding
  window; the fifth ends the window with a 429 and a ``Retry-After``.
* **One-time codes are one-time.** A TOTP counter is remembered after it is accepted, so the
  same six digits cannot be replayed inside their 30-second step; a recovery code is marked
  used in the same transaction that accepts it.
* **A challenge is a challenge.** The ``mfa_token`` is opaque, single use after success, lives
  five minutes, and carries no access rights — it can only be spent on the verify step that
  created it.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pyotp

from hub.identity.contracts import AuditLog
from hub.shared.contracts import KeyValueStore
from hub.shared.errors import RateLimited, Unauthenticated
from hub.shared.ports import Clock, IdGenerator
from hub.shared.security import (
    digest_token,
    hash_recovery_code,
    new_recovery_code,
    new_totp_secret,
    verify_password,
)

__all__ = [
    "Authenticated",
    "Enrollment",
    "LoginThrottle",
    "LocalLogin",
    "MfaChallenge",
    "EnrollmentService",
    "MfaService",
]

CHALLENGE_TTL_SECONDS = 300
ENROLLMENT_TTL_SECONDS = 300
RECOVERY_CODE_COUNT = 10
TOTP_STEP_SECONDS = 30
# docs/05: one step either side of now is accepted, two is not.
TOTP_VALID_WINDOW = 1
TOTP_REPLAY_TTL_SECONDS = 90
FAILURE_LIMIT = 5
FAILURE_WINDOW_SECONDS = 900


@dataclass(frozen=True, slots=True)
class MfaChallenge:
    """``MfaChallenge`` of the contract: what the first step answers."""

    mfa_token: str
    expires_in: int
    enrollment_required: bool
    methods: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Enrollment:
    """``MfaEnrollment`` of the contract: shown once, and never again."""

    otpauth_uri: str
    recovery_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Authenticated:
    """A completed second step: who it is, and the session that now exists."""

    user_id: UUID
    session_id: UUID
    auth_time: datetime
    acr: str
    refresh_token: str
    refresh_expires_in: int


class LoginThrottle:
    """Progressive lockout per account and per address, in one sliding window."""

    def __init__(
        self,
        store: KeyValueStore,
        *,
        limit: int = FAILURE_LIMIT,
        window: int = FAILURE_WINDOW_SECONDS,
    ) -> None:
        self._store = store
        self._limit = limit
        self._window = window

    def check(self, *keys: str) -> None:
        for key in keys:
            current = self._store.get(self._key(key))
            if current is not None and int(current) >= self._limit:
                raise RateLimited(
                    code="rate_limited",
                    details={"scope": "login"},
                    retry_after_seconds=self._window,
                )

    def fail(self, *keys: str) -> None:
        for key in keys:
            self._store.incr(self._key(key), ttl_seconds=self._window)

    def clear(self, *keys: str) -> None:
        for key in keys:
            self._store.delete(self._key(key))

    def _key(self, key: str) -> str:
        return f"login_fail:{key}"


class LocalLogin:
    """Step one: the password, the lockout and the challenge."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        store: KeyValueStore,
        users: Any,
        audit: AuditLog,
        throttle: LoginThrottle,
        webauthn_enforced: bool,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._store = store
        self._users = users
        self._audit = audit
        self._throttle = throttle
        self._webauthn_enforced = webauthn_enforced

    def start(self, email: str, password: str, *, ip: str | None = None) -> MfaChallenge:
        account_key = f"account:{email.strip().lower()}"
        address_key = f"ip:{ip or 'unknown'}"
        self._throttle.check(account_key, address_key)

        record = self._users.find_by_email(email)
        # Always verify: an unknown address must cost the same as a known one.
        matches = verify_password(record.password_hash if record else None, password)
        if record is None or not matches or record.status != "active":
            self._throttle.fail(account_key, address_key)
            self._audit.record(
                stream="auth",
                actor=None,
                action="auth.login",
                entity_type="user",
                entity_id=record.id if record is not None else None,
                site_id=None,
                details={"method": "password"},
                outcome="denied",
                ip=ip,
            )
            raise Unauthenticated(code="invalid_credentials")

        self._throttle.clear(account_key)
        mfa_token = f"mfa_{secrets.token_urlsafe(18)}"
        enrollment_required = record.mfa_secret_enc is None
        methods = self._methods(record, enrollment_required=enrollment_required)
        self._store.set(
            _challenge_key(mfa_token),
            json.dumps({"user_id": str(record.id)}),
            ttl_seconds=CHALLENGE_TTL_SECONDS,
        )
        self._audit.record(
            stream="auth",
            actor=None,
            action="auth.login",
            entity_type="user",
            entity_id=record.id,
            site_id=None,
            details={"method": "password", "step": "challenge_issued"},
            ip=ip,
        )
        return MfaChallenge(
            mfa_token=mfa_token,
            expires_in=CHALLENGE_TTL_SECONDS,
            enrollment_required=enrollment_required,
            methods=methods,
        )

    def _methods(self, record: Any, *, enrollment_required: bool) -> tuple[str, ...]:
        if enrollment_required:
            return ("totp",)
        methods = ["totp"]
        if self._users.has_webauthn_credential(record.id):
            methods.append("webauthn")
        if self._users.count_unused_recovery_codes(record.id) > 0:
            methods.append("recovery_code")
        if self._webauthn_enforced and methods == ["totp"]:
            # The enforced roles must enrol before they can sign in (docs/05 4).
            return ("totp",)
        return tuple(methods)


class MfaService:
    """Step two: the TOTP or recovery code, and the session it opens."""

    def __init__(
        self,
        *,
        clock: Clock,
        store: KeyValueStore,
        users: Any,
        sessions: Any,
        audit: AuditLog,
    ) -> None:
        self._clock = clock
        self._store = store
        self._users = users
        self._sessions = sessions
        self._audit = audit

    def verify(
        self,
        mfa_token: str,
        *,
        totp_code: str | None = None,
        recovery_code: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> Authenticated:
        record_id = _challenge_user(self._store, mfa_token)
        record = self._users.get(record_id)
        if record is None or record.status != "active":
            raise Unauthenticated(code="invalid_code")

        if recovery_code:
            self._verify_recovery(record, recovery_code, ip=ip)
            method = "recovery_code"
        else:
            self._verify_totp(record, totp_code or "", ip=ip)
            method = "totp"

        now = self._clock.now()
        issued = self._sessions.issue(
            user_id=record.id,
            customer_id=None,
            auth_time=now,
            acr="mfa",
            ip=ip,
            user_agent=user_agent,
        )
        self._store.delete(_challenge_key(mfa_token))
        self._users.mark_login(record.id, now)
        self._audit.record(
            stream="auth",
            actor=None,
            action="auth.login",
            entity_type="user",
            entity_id=record.id,
            site_id=None,
            details={"method": method},
            ip=ip,
        )
        return Authenticated(
            user_id=record.id,
            session_id=issued.session_id,
            auth_time=now,
            acr="mfa",
            refresh_token=issued.token,
            refresh_expires_in=issued.expires_in,
        )

    # --- internals ----------------------------------------------------------------------
    def _verify_totp(self, record: Any, code: str, *, ip: str | None) -> None:
        if record.mfa_secret_enc is None:
            raise Unauthenticated(code="invalid_code")
        secret = self._users.totp_secret(record.id)
        now = self._clock.now()
        totp = pyotp.TOTP(secret)
        # `verify` with a window returns the counter that matched only via the timestamp we
        # pass, so each candidate step is tried explicitly and its counter remembered.
        for offset in range(-TOTP_VALID_WINDOW, TOTP_VALID_WINDOW + 1):
            instant = now + timedelta(seconds=offset * TOTP_STEP_SECONDS)
            if not pyotp.utils.strings_equal(str(totp.at(instant)), code):
                continue
            counter = _counter_of(totp, instant)
            if not self._store.add_once(
                f"totp:used:{record.id}:{counter}",
                "1",
                ttl_seconds=TOTP_REPLAY_TTL_SECONDS,
            ):
                self._audit.record(
                    stream="auth",
                    actor=None,
                    action="auth.mfa.replay",
                    entity_type="user",
                    entity_id=record.id,
                    site_id=None,
                    details={"method": "totp"},
                    outcome="denied",
                    ip=ip,
                )
                raise Unauthenticated(code="invalid_code")
            return
        raise Unauthenticated(code="invalid_code")

    def _verify_recovery(self, record: Any, code: str, *, ip: str | None) -> None:
        consumed = self._users.consume_recovery_code(
            record.id, hash_recovery_code(code), at=self._clock.now()
        )
        if not consumed:
            raise Unauthenticated(code="invalid_code")
        self._audit.record(
            stream="auth",
            actor=None,
            action="auth.mfa.recovery_code",
            entity_type="user",
            entity_id=record.id,
            site_id=None,
            details={"remaining": self._users.count_unused_recovery_codes(record.id)},
            ip=ip,
        )


class EnrollmentService:
    """TOTP enrolment: a pending secret in the store, then one confirming code."""

    def __init__(self, *, clock: Clock, store: KeyValueStore, users: Any) -> None:
        self._clock = clock
        self._store = store
        self._users = users

    def start(self, mfa_token: str, *, issuer: str) -> Enrollment:
        record_id = _challenge_user(self._store, mfa_token)
        secret = new_totp_secret()
        codes = tuple(new_recovery_code() for _ in range(RECOVERY_CODE_COUNT))
        self._store.set(
            _enrollment_key(mfa_token),
            json.dumps({"user_id": str(record_id), "secret": secret, "codes": list(codes)}),
            ttl_seconds=ENROLLMENT_TTL_SECONDS,
        )
        uri = pyotp.TOTP(secret).provisioning_uri(name=str(record_id), issuer_name=issuer)
        return Enrollment(otpauth_uri=uri, recovery_codes=codes)

    def confirm(self, mfa_token: str, totp_code: str) -> UUID:
        raw = self._store.pop(_enrollment_key(mfa_token))
        if raw is None:
            raise Unauthenticated(code="invalid_code")
        try:
            pending: dict[str, Any] = json.loads(raw)
            record_id = UUID(str(pending["user_id"]))
            secret = str(pending["secret"])
            codes = [str(code) for code in pending["codes"]]
        except (KeyError, ValueError, TypeError) as exc:
            raise Unauthenticated(code="invalid_code") from exc
        totp = pyotp.TOTP(secret)
        now = self._clock.now()
        # Judged against the injected clock, not pyotp's wall clock: the enrolment and its
        # confirming code are seconds apart in production and arbitrary in a test.
        matches = any(
            pyotp.utils.strings_equal(
                str(totp.at(now + timedelta(seconds=offset * TOTP_STEP_SECONDS))), totp_code
            )
            for offset in range(-TOTP_VALID_WINDOW, TOTP_VALID_WINDOW + 1)
        )
        if not matches:
            # A wrong code burns the pending secret: the next attempt starts again.
            raise Unauthenticated(code="invalid_code")
        self._users.store_mfa_enrollment(
            record_id,
            secret=secret,
            recovery_hashes=[hash_recovery_code(code) for code in codes],
            at=self._clock.now(),
        )
        self._store.delete(_challenge_key(mfa_token))
        return record_id

    def regenerate(self, user_id: UUID) -> tuple[str, ...]:
        codes = tuple(new_recovery_code() for _ in range(RECOVERY_CODE_COUNT))
        self._users.replace_recovery_codes(user_id, [hash_recovery_code(code) for code in codes])
        return codes


def _challenge_user(store: KeyValueStore, mfa_token: str) -> UUID:
    raw = store.get(_challenge_key(mfa_token))
    if raw is None:
        raise Unauthenticated(code="invalid_code")
    try:
        return UUID(str(json.loads(raw)["user_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise Unauthenticated(code="invalid_code") from exc


def _challenge_key(mfa_token: str) -> str:
    return f"mfa_challenge:{digest_token(mfa_token).hex()}"


def _enrollment_key(mfa_token: str) -> str:
    return f"mfa_enrollment:{digest_token(mfa_token).hex()}"


def _counter_of(totp: pyotp.TOTP, instant: datetime) -> int:
    return int(instant.timestamp()) // int(totp.interval or TOTP_STEP_SECONDS)
