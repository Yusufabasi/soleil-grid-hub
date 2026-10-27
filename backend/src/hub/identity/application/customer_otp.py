"""Customer sign-in with a one-time code, and the abuse controls around it (D-21, FR-15).

The endpoint that starts this flow is public and costs real money per call, so three rules shape
everything below:

* **A request always looks the same.** ``request`` answers 202 with a challenge for a known
  number and for an unknown one, writes a row in both cases, and sends an SMS only in the first.
  The row for an unknown number is a decoy: ``max_attempts = 0``, a random ``code_hash``, and no
  customer, so a verification against it fails exactly like a wrong code does.
* **The number is never stored.** ``hub.customer_otp.phone_hmac`` is an HMAC under
  ``HUB_PHONE_HMAC_KEY``, and the code itself is stored as a SHA-256 over that HMAC — so the
  table is useless without the key and the customer row.
* **Every layer refuses before it reads.** The proof of work is spent first (422
  ``proof_required``), then the prefix allow-list (422), then the per-phone, per-address and
  per-network hourly counters (429) — none of which touches ``hub.customer_otp``.

Verification counts failures per challenge: the fifth wrong code kills it, and a sixth attempt
with the *right* code is still refused, because a challenge that has been guessed at five times
is not a challenge any more. A customer dormant for ``HUB_OTP_DORMANT_DAYS`` is issued a
``reverify`` challenge instead of a ``login`` one, and a ``login`` challenge will not sign a
dormant customer in.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from hub.identity.application.proof import ProofService
from hub.identity.application.sessions import SessionService
from hub.identity.application.tokens import AccessTokenService
from hub.identity.contracts import CustomerLookup, CustomerRecord, Principal
from hub.identity.domain.principal import Locale
from hub.shared.contracts import KeyValueStore
from hub.shared.errors import RateLimited, Unauthenticated, ValidationFailed
from hub.shared.ports import Clock, IdGenerator

__all__ = [
    "CustomerOtpService",
    "CustomerRecord",
    "Notifier",
    "OtpChallengeDocument",
    "OtpLimits",
    "OtpRow",
    "OtpSignIn",
]

LOGIN = "login"
REVERIFY = "reverify"


@dataclass(frozen=True, slots=True)
class OtpLimits:
    """The three hourly ceilings of D-21."""

    per_phone: int
    per_ip: int
    per_network: int


@dataclass(frozen=True, slots=True)
class OtpRow:
    """One row of ``hub.customer_otp``."""

    id: UUID
    phone_hmac: bytes
    code_hash: bytes
    purpose: str
    attempts: int
    max_attempts: int
    expires_at: datetime
    consumed_at: datetime | None


@dataclass(frozen=True, slots=True)
class OtpChallengeDocument:
    """``api/openapi.yaml`` OtpChallenge."""

    challenge_id: UUID
    expires_in: int
    resend_after: int

    def document(self) -> dict[str, object]:
        return {
            "challenge_id": str(self.challenge_id),
            "expires_in": self.expires_in,
            "resend_after": self.resend_after,
        }


@dataclass(frozen=True, slots=True)
class OtpSignIn:
    """The result of a successful verification: a session and a token for it."""

    principal: Principal
    access_token: str
    refresh_token: str
    refresh_expires_in: int


class Notifier(Protocol):
    """The SMS side: an adapter in production, a recording fake in tests."""

    def send_otp(self, *, phone: str, code: str, locale: str) -> None: ...


class OtpStore(Protocol):
    def insert(
        self,
        *,
        challenge_id: UUID,
        phone_hmac: bytes,
        code_hash: bytes,
        purpose: str,
        request_ip: str | None,
        created_at: datetime,
        expires_at: datetime,
        max_attempts: int,
    ) -> None: ...

    def get(self, challenge_id: UUID, *, for_update: bool = False) -> OtpRow | None: ...

    def record_attempt(self, challenge_id: UUID) -> int: ...

    def consume(self, challenge_id: UUID, *, at: datetime) -> None: ...


class CustomerOtpService:
    """Request, deliver and verify one-time codes for customers."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        store: KeyValueStore,
        proofs: ProofService,
        otp_store: OtpStore,
        customers: CustomerLookup,
        notifier: Notifier,
        sessions: SessionService,
        tokens: AccessTokenService,
        phone_hmac: Callable[[str], bytes],
        limits: OtpLimits,
        allowed_prefixes: tuple[str, ...],
        ttl_seconds: int,
        resend_after_seconds: int = 60,
        dormant_days: int = 90,
        max_attempts: int = 5,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._store = store
        self._proofs = proofs
        self._otp_store = otp_store
        self._customers = customers
        self._notifier = notifier
        self._sessions = sessions
        self._tokens = tokens
        self._phone_hmac = phone_hmac
        self._limits = limits
        self._prefixes = allowed_prefixes
        self._ttl = ttl_seconds
        self._resend_after = resend_after_seconds
        self._dormant = timedelta(days=dormant_days)
        self._max_attempts = max_attempts

    # --- requesting --------------------------------------------------------------------
    def request(
        self,
        *,
        phone: str,
        locale: str | None,
        proof_token: str | None,
        ip: str | None,
    ) -> OtpChallengeDocument:
        """Always the same 202, whether or not the number belongs to anybody (D-21)."""
        self._proofs.verify(proof_token)
        self._require_allowed_prefix(phone)
        phone_hmac = self._phone_hmac(phone)
        self._enforce_limits(phone_hmac, ip)

        now = self._clock.now()
        customer = self._customers.find_active_by_phone(phone)
        challenge_id = self._ids.new_id()
        purpose = self._purpose_for(customer, now)
        sent: str | None = None
        if customer is None:
            # The decoy: a row that can never verify, so the response shape stays identical.
            code_hash = secrets.token_bytes(32)
            max_attempts = 0
        else:
            sent = f"{secrets.randbelow(1_000_000):06d}"
            code_hash = self._hash_code(sent, phone_hmac)
            max_attempts = self._max_attempts

        self._otp_store.insert(
            challenge_id=challenge_id,
            phone_hmac=phone_hmac,
            code_hash=code_hash,
            purpose=purpose,
            request_ip=ip,
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl),
            max_attempts=max_attempts,
        )
        if customer is not None and sent is not None:
            # The decoy row has no customer behind it, and the row has no column for one: the
            # link lives in Redis for exactly as long as the challenge does (D-21).
            self._store.set(_customer_key(challenge_id), str(customer.id), ttl_seconds=self._ttl)
            self._notifier.send_otp(
                phone=phone, code=sent, locale=_locale(locale or customer.locale)
            )
        return OtpChallengeDocument(
            challenge_id=challenge_id, expires_in=self._ttl, resend_after=self._resend_after
        )

    # --- verifying ---------------------------------------------------------------------
    def verify(
        self,
        *,
        challenge_id: UUID,
        code: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> OtpSignIn:
        """Spend the challenge and open a customer session, or refuse with ``otp_invalid``."""
        row = self._otp_store.get(challenge_id, for_update=True)
        now = self._clock.now()
        if row is None:
            raise _invalid()
        if row.consumed_at is not None or row.expires_at <= now or row.attempts >= row.max_attempts:
            raise _invalid()

        presented = self._hash_code(code, row.phone_hmac)
        if not secrets.compare_digest(presented, row.code_hash):
            attempts = self._otp_store.record_attempt(challenge_id)
            if attempts >= row.max_attempts:
                # The challenge is dead even for the right code; keep the row as evidence.
                self._otp_store.consume(challenge_id, at=now)
            raise _invalid()

        linked = self._store.get(_customer_key(challenge_id))
        customer = self._customers.by_id(UUID(linked)) if linked else None
        if customer is None:
            # The number stopped being an active customer between request and verify.
            self._otp_store.consume(challenge_id, at=now)
            raise _invalid()
        if row.purpose == LOGIN and self._is_dormant(customer, now):
            # Dormant customers must go through the reverify challenge (D-21).
            self._otp_store.consume(challenge_id, at=now)
            raise _invalid()

        self._otp_store.consume(challenge_id, at=now)
        self._store.delete(_customer_key(challenge_id))
        # The session this opens is the customer's last sign-in: dormancy is read from it, so
        # there is no separate `last_login_at` to keep in step.
        session = self._sessions.issue(
            user_id=None,
            customer_id=customer.id,
            auth_time=now,
            acr="otp",
            ip=ip,
            user_agent=user_agent,
        )
        principal = self.principal_for(
            customer.id, session_id=session.session_id, auth_time=now, acr="otp"
        )
        access = self._tokens.issue(
            principal,
            session_id=session.session_id,
            auth_time=now,
            acr="otp",
            amr=("otp",),
        )
        return OtpSignIn(
            principal=principal,
            access_token=access,
            refresh_token=session.token,
            refresh_expires_in=session.expires_in,
        )

    def principal_for(
        self,
        customer_id: UUID,
        *,
        session_id: UUID,
        auth_time: datetime,
        acr: str,
    ) -> Principal:
        """The principal a customer session belongs to, on login and after every rotation."""
        customer = self._customers.by_id(customer_id)
        if customer is None:
            raise Unauthenticated()
        return Principal(
            subject_id=customer.id,
            kind="customer",
            customer_id=customer.id,
            session_id=session_id,
            auth_time=auth_time,
            acr=acr,
            locale=_locale(customer.locale),
        )

    # --- the guards --------------------------------------------------------------------
    def _require_allowed_prefix(self, phone: str) -> None:
        if not any(phone.startswith(prefix) for prefix in self._prefixes):
            raise ValidationFailed(
                details={
                    "errors": [
                        {
                            "field": "/phone_e164",
                            "code": "prefix_not_allowed",
                            "message": "This country is not served yet.",
                        }
                    ]
                }
            )

    def _enforce_limits(self, phone_hmac: bytes, ip: str | None) -> None:
        """Three sliding-hour counters: the number, the address and the /24 it sits in."""
        window = 3600
        checks: list[tuple[str, int]] = [
            (f"otp:phone:{phone_hmac.hex()}", self._limits.per_phone),
        ]
        if ip:
            checks.append((f"otp:ip:{ip}", self._limits.per_ip))
            checks.append((f"otp:net:{_network(ip)}", self._limits.per_network))
        for key, ceiling in checks:
            if self._store.incr(key, ttl_seconds=window) > ceiling:
                raise RateLimited(retry_after_seconds=window)

    def _purpose_for(self, customer: CustomerRecord | None, now: datetime) -> str:
        if customer is not None and self._is_dormant(customer, now):
            return REVERIFY
        return LOGIN

    def _is_dormant(self, customer: CustomerRecord, now: datetime) -> bool:
        if customer.last_login_at is None:
            return False
        return (now - customer.last_login_at) > self._dormant

    @staticmethod
    def _hash_code(code: str, phone_hmac: bytes) -> bytes:
        """The code is stored salted with the number's HMAC: a dump cannot be replayed."""
        return hashlib.sha256(f"{code}:{phone_hmac.hex()}".encode()).digest()


def _customer_key(challenge_id: UUID) -> str:
    """Which customer a challenge was issued for, for the length of the challenge only."""
    return f"otp:customer:{challenge_id}"


def _network(ip: str) -> str:
    """The /24 of an IPv4 address, or the address itself for anything else."""
    parts = ip.split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else ip


def _locale(value: str) -> Locale:
    return "fr" if value == "fr" else "en"


def _invalid() -> Unauthenticated:
    return Unauthenticated(
        code="otp_invalid",
        details={
            "errors": [
                {
                    "field": "/code",
                    "code": "otp_invalid",
                    "message": "The code is wrong, expired or already used.",
                }
            ]
        },
    )
