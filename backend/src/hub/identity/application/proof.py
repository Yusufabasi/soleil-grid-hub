"""Proof of work in front of the one-time-code endpoints (D-21).

The public OTP request is the one unauthenticated endpoint that causes a *cost* — an SMS to a
real phone — so it is guarded by a puzzle whose price is paid in CPU, not in a CAPTCHA a script
can outsource. The scheme is hashcash shaped:

1. ``GET /auth/proof-challenge`` mints a random 128-bit secret, remembers its difficulty in Redis
   for two minutes, and returns it with that difficulty;
2. the client finds a ``nonce`` such that ``sha256("<secret>.<nonce>")`` starts with at least
   *difficulty* zero bits — about ``2 ** difficulty`` attempts, feasible in a browser, costly per
   message at scale;
3. ``POST /auth/customer/otp/request`` presents ``"<secret>.<nonce>"``, and the server pops the
   secret before it checks anything else.

Popping before verifying is deliberate: the challenge is spent whether or not it was solved, so a
token cannot be banked and replayed, and a wrong solve cannot be retried against the same secret
without paying for a new one.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from hub.shared.contracts import KeyValueStore
from hub.shared.errors import ValidationFailed
from hub.shared.ports import Clock

__all__ = ["ProofChallenge", "ProofService"]

PREFIX = "pow:"
DEFAULT_TTL_SECONDS = 120


@dataclass(frozen=True, slots=True)
class ProofChallenge:
    """``api/openapi.yaml`` ProofChallenge."""

    kind: str
    challenge: str
    difficulty: int
    expires_at: datetime

    def document(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "challenge": self.challenge,
            "difficulty": self.difficulty,
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
        }


def leading_zero_bits(digest: bytes) -> int:
    """How many zero bits a digest starts with — the value the difficulty is compared to."""
    count = 0
    for byte in digest:
        if byte == 0:
            count += 8
            continue
        count += 8 - byte.bit_length()
        break
    return count


class ProofService:
    """Issue and spend proof-of-work challenges."""

    def __init__(
        self,
        *,
        store: KeyValueStore,
        clock: Clock,
        difficulty: int,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        kind: str = "pow",
    ) -> None:
        self._store = store
        self._clock = clock
        self._difficulty = difficulty
        self._ttl = ttl_seconds
        self._kind = kind

    def challenge(self) -> ProofChallenge:
        """A fresh secret, remembered with its difficulty for at most ``ttl_seconds``."""
        secret_value = secrets.token_urlsafe(18)
        self._store.set(f"{PREFIX}{secret_value}", str(self._difficulty), ttl_seconds=self._ttl)
        return ProofChallenge(
            kind=self._kind,
            challenge=secret_value,
            difficulty=self._difficulty,
            expires_at=self._clock.now() + timedelta(seconds=self._ttl),
        )

    def verify(self, token: str | None) -> None:
        """Spend the challenge and check the solve; ``ValidationFailed`` says ``proof_required``.

        A missing token, an expired one, a second attempt with the same secret and a digest that
        does not start with the required zero bits are all the same answer to the client: solve a
        new challenge. Distinguishing them would tell an attacker which part to fix.
        """
        if not token or "." not in token:
            raise _refused()
        secret_value, _, nonce = token.partition(".")
        stored = self._store.pop(f"{PREFIX}{secret_value}")
        if stored is None or not nonce:
            raise _refused()
        digest = hashlib.sha256(f"{secret_value}.{nonce}".encode()).digest()
        if leading_zero_bits(digest) < int(stored):
            raise _refused()


def _refused() -> ValidationFailed:
    return ValidationFailed(
        code="proof_required",
        details={
            "errors": [
                {
                    "field": "/proof_token",
                    "code": "proof_required",
                    "message": "Solve the challenge from GET /auth/proof-challenge.",
                }
            ]
        },
    )
