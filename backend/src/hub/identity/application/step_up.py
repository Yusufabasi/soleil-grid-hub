"""Step-up: prove identity again for the operations that move money or grant power (D-16).

An access token stays valid for ten minutes, but a decision inside it may only be made on an
authentication that is at most five minutes old. When that age has passed the operation answers
401 with a challenge, and the client comes back here with a second factor:

* a TOTP code, through the same window and replay guard as login;
* a recovery code, spent once like any other;
* a WebAuthn assertion over a challenge issued for this signed-in account, which is the
  phishing-resistant option the finance roles must use.

What comes back is the assurance reached — ``acr`` — and the instant it was reached. The view
issues the token: this service proves things, it does not mint credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from hub.identity.application.totp import verify_totp_code
from hub.identity.application.webauthn import WebauthnService
from hub.identity.contracts import AuditLog, Principal
from hub.shared.contracts import KeyValueStore
from hub.shared.errors import Unauthenticated
from hub.shared.ports import Clock
from hub.shared.security import hash_recovery_code

__all__ = ["StepUpProof", "StepUpResult", "StepUpService"]

TOTP_METHOD = "totp"
RECOVERY_METHOD = "recovery_code"
WEBAUTHN_METHOD = "webauthn"


@dataclass(frozen=True, slots=True)
class StepUpProof:
    """One second factor presented for a step-up."""

    method: str
    totp_code: str | None = None
    recovery_code: str | None = None
    assertion: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class StepUpResult:
    """The assurance a step-up reached, and when: what the new token must carry."""

    acr: str
    auth_time: datetime


class StepUpService:
    """Verifies a second factor for an already-authenticated caller."""

    def __init__(
        self,
        *,
        clock: Clock,
        store: KeyValueStore,
        users: Any,
        audit: AuditLog,
        webauthn: WebauthnService,
    ) -> None:
        self._clock = clock
        self._store = store
        self._users = users
        self._audit = audit
        self._webauthn = webauthn

    def step_up(
        self, principal: Principal, proof: StepUpProof, *, ip: str | None = None
    ) -> StepUpResult:
        """Verify the proof and return the assurance it earns."""
        if principal.kind != "staff":
            # A customer session has one factor (a phone code) and nothing to step up with.
            raise Unauthenticated(code="step_up_required")

        acr = self._verify(principal.subject_id, proof, ip=ip)
        now = self._clock.now()
        self._audit.record(
            stream="auth",
            actor=principal,
            action="auth.step_up",
            entity_type="user",
            entity_id=principal.subject_id,
            site_id=None,
            details={"method": proof.method},
            ip=ip,
        )
        return StepUpResult(acr=acr, auth_time=now)

    def _verify(self, user_id: UUID, proof: StepUpProof, *, ip: str | None) -> str:
        if proof.method == WEBAUTHN_METHOD:
            self._verify_assertion(user_id, proof.assertion)
            return "phr"
        if proof.recovery_code:
            if not self._users.consume_recovery_code(
                user_id, hash_recovery_code(proof.recovery_code), at=self._clock.now()
            ):
                raise Unauthenticated(code="invalid_code")
            self._audit.record(
                stream="auth",
                actor=None,
                action="auth.mfa.recovery_code",
                entity_type="user",
                entity_id=user_id,
                site_id=None,
                details={
                    "remaining": self._users.count_unused_recovery_codes(user_id),
                    "purpose": "step_up",
                },
                ip=ip,
            )
            return "mfa"
        verify_totp_code(
            clock=self._clock,
            store=self._store,
            users=self._users,
            audit=self._audit,
            user_id=user_id,
            code=proof.totp_code or "",
            ip=ip,
        )
        return "mfa"

    def _verify_assertion(self, user_id: UUID, assertion: Mapping[str, Any] | None) -> None:
        """A step-up assertion names the challenge it answers and the credential that signed."""
        if not isinstance(assertion, Mapping):
            raise Unauthenticated(code="invalid_code")
        raw_challenge = assertion.get("challenge_id")
        credential = assertion.get("credential")
        if not isinstance(raw_challenge, str) or not isinstance(credential, Mapping):
            raise Unauthenticated(code="invalid_code")
        try:
            challenge_id = UUID(raw_challenge)
        except ValueError as exc:
            raise Unauthenticated(code="invalid_code") from exc
        self._webauthn.assert_for(user_id, challenge_id, dict(credential))
