"""WebAuthn: a phishing-resistant second factor for the roles that move money (D-16).

The server never sees a private key and never trusts a browser's word for the challenge: every
options call stores a fresh random challenge in Redis under an opaque id, and the verification
call consumes it exactly once. The library then proves the answer with four checks that this
module does not re-implement:

* the challenge is the one this server issued;
* the origin and the relying-party id are this site's (so a look-alike domain cannot relay it);
* user verification was performed (a PIN or a biometric, not just a touch);
* the signature is over the authenticator data plus the client data hash.

``sign_count`` is the one piece of state: it must increase, and a counter that goes backwards is
the classic cloned-authenticator signal, so it is refused rather than repaired.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import webauthn
from webauthn.helpers import base64url_to_bytes, options_to_json
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from hub.identity.contracts import WebauthnCredentialRow
from hub.shared.contracts import KeyValueStore
from hub.shared.errors import Unauthenticated, ValidationFailed
from hub.shared.ports import Clock, IdGenerator
from hub.shared.security import digest_token

__all__ = ["WebauthnChallenge", "WebauthnService"]

CHALLENGE_TTL_SECONDS = 300


@dataclass(frozen=True, slots=True)
class WebauthnChallenge:
    """One issued challenge: the options a browser needs and the id that names it."""

    options: dict[str, Any]
    challenge_id: UUID


class WebauthnService:
    """Registration, assertion and the challenge store behind both."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        store: KeyValueStore,
        users: Any,
        rp_id: str,
        rp_name: str,
        origin: str,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._store = store
        self._users = users
        self._rp_id = rp_id
        self._rp_name = rp_name
        self._origin = origin

    # --- registration -------------------------------------------------------------------
    def registration_options(self, user_id: UUID) -> WebauthnChallenge:
        record = self._users.get(user_id)
        if record is None:
            raise Unauthenticated()
        descriptors = [
            PublicKeyCredentialDescriptor(id=bytes(credential.credential_id))
            for credential in self._users.webauthn_credentials(user_id)
        ]
        options = webauthn.generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_id=user_id.bytes,
            user_name=record.email,
            user_display_name=record.display_name,
            exclude_credentials=descriptors,
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.REQUIRED,
                resident_key=ResidentKeyRequirement.PREFERRED,
            ),
        )
        challenge_id = self._remember(options.challenge, purpose="registration", user_id=user_id)
        return WebauthnChallenge(
            options=json.loads(options_to_json(options)), challenge_id=challenge_id
        )

    def register(
        self,
        user_id: UUID,
        challenge_id: UUID,
        credential: dict[str, Any],
        *,
        name: str,
    ) -> WebauthnCredentialRow:
        challenge = self._consume(challenge_id, purpose="registration", user_id=user_id)
        try:
            verified = webauthn.verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                require_user_presence=True,
                require_user_verification=True,
            )
        except InvalidRegistrationResponse as exc:
            raise ValidationFailed(
                details={
                    "errors": [
                        {"field": "/credential", "code": "invalid_credential", "message": str(exc)}
                    ]
                }
            ) from exc
        now = self._clock.now()
        stored: WebauthnCredentialRow = self._users.insert_webauthn_credential(
            user_id,
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=int(verified.sign_count),
            transports=_transports(credential),
            name=name or "security key",
            at=now,
        )
        return stored

    # --- assertion ----------------------------------------------------------------------
    def assertion_options(self, mfa_token: str) -> WebauthnChallenge:
        user_id = self._challenge_user(mfa_token)
        credentials = self._users.webauthn_credentials(user_id)
        if not credentials:
            raise ValidationFailed(
                details={
                    "errors": [
                        {
                            "field": "/mfa_token",
                            "code": "webauthn_not_enrolled",
                            "message": "This account holds no security key.",
                        }
                    ]
                }
            )
        options = webauthn.generate_authentication_options(
            rp_id=self._rp_id,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=bytes(credential.credential_id))
                for credential in credentials
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        challenge_id = self._remember(options.challenge, purpose="assertion", user_id=user_id)
        return WebauthnChallenge(
            options=json.loads(options_to_json(options)), challenge_id=challenge_id
        )

    def assert_(self, mfa_token: str, challenge_id: UUID, credential: dict[str, Any]) -> UUID:
        user_id = self._challenge_user(mfa_token)
        challenge = self._consume(challenge_id, purpose="assertion", user_id=user_id)
        raw_id = str(credential.get("rawId") or credential.get("id") or "")
        stored = self._users.find_webauthn_credential(base64url_to_bytes(raw_id))
        if stored is None:
            raise Unauthenticated(code="invalid_code")
        try:
            verified = webauthn.verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=bytes(stored.public_key),
                credential_current_sign_count=stored.sign_count,
                require_user_verification=True,
            )
        except InvalidAuthenticationResponse as exc:
            raise Unauthenticated(code="invalid_code") from exc
        self._users.touch_webauthn_credential(
            stored.id, sign_count=int(verified.new_sign_count), at=self._clock.now()
        )
        return user_id

    # --- challenge storage --------------------------------------------------------------
    def _remember(self, challenge: bytes, *, purpose: str, user_id: UUID) -> UUID:
        challenge_id = self._ids.new_id()
        self._store.set(
            f"webauthn_challenge:{challenge_id}",
            json.dumps(
                {
                    "purpose": purpose,
                    "user_id": str(user_id),
                    "challenge": base64_url(challenge),
                }
            ),
            ttl_seconds=CHALLENGE_TTL_SECONDS,
        )
        return challenge_id

    def _consume(self, challenge_id: UUID, *, purpose: str, user_id: UUID) -> bytes:
        raw = self._store.pop(f"webauthn_challenge:{challenge_id}")
        if raw is None:
            raise Unauthenticated(code="invalid_code")
        try:
            value: dict[str, Any] = json.loads(raw)
            if value.get("purpose") != purpose or value.get("user_id") != str(user_id):
                raise Unauthenticated(code="invalid_code")
            return base64url_to_bytes(str(value["challenge"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise Unauthenticated(code="invalid_code") from exc

    def _challenge_user(self, mfa_token: str) -> UUID:
        raw = self._store.get(f"mfa_challenge:{digest_token(mfa_token).hex()}")
        if raw is None:
            raise Unauthenticated(code="invalid_code")
        try:
            return UUID(str(json.loads(raw)["user_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise Unauthenticated(code="invalid_code") from exc


def base64_url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _transports(credential: dict[str, Any]) -> list[str]:
    response = credential.get("response")
    if not isinstance(response, dict):
        return []
    transports = response.get("transports")
    if not isinstance(transports, list):
        return []
    return [str(item) for item in transports][:8]
