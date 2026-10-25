"""A software authenticator: real attestation and assertion objects, real P-256 signatures.

py_webauthn verifies what a browser would send, so a fake that skips the cryptography would
prove nothing. This builds the two CBOR structures the library parses — a ``none`` attestation
and a signed assertion — from a real EC key, which means the flow under test is the production
one: challenge, origin, RP id, user-verification flag and signature are all checked for real.

The only thing it cannot simulate is a user: ``user_verified=False`` builds the same object with
the UV bit clear, which is exactly what the server must refuse.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

__all__ = ["SoftwareAuthenticator"]

FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED_CREDENTIAL_DATA = 0x40


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@dataclass
class SoftwareAuthenticator:
    """One registered credential, held in memory for the length of a test."""

    origin: str
    rp_id: str
    user_id: UUID
    credential_id: bytes = field(default_factory=lambda: os.urandom(16))
    sign_count: int = 0
    _key: ec.EllipticCurvePrivateKey = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())

    # --- registration -----------------------------------------------------------------
    def registration(self, challenge: bytes, *, user_verified: bool = True) -> dict[str, Any]:
        """The JSON a browser posts to the registration endpoint."""
        client_data = json.dumps(
            {
                "type": "webauthn.create",
                "challenge": b64url(challenge),
                "origin": self.origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode()
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([self._flags(user_verified) | FLAG_ATTESTED_CREDENTIAL_DATA])
            + self.sign_count.to_bytes(4, "big")
            + b"\x00" * 16  # aaguid: no attesting authority, which is what `none` means
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose_public_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    # --- assertion ----------------------------------------------------------------------
    def assertion(
        self, challenge: bytes, *, user_verified: bool = True, sign_count: int | None = None
    ) -> dict[str, Any]:
        """The JSON a browser posts to the assertion endpoint, signed with the real key."""
        self.sign_count = self.sign_count + 1 if sign_count is None else sign_count
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": b64url(challenge),
                "origin": self.origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode()
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([self._flags(user_verified)])
            + self.sign_count.to_bytes(4, "big")
        )
        digest = hashlib.sha256(client_data).digest()
        # CTAP2 hands ES256 assertions back as an ASN.1 DER signature, not raw (r || s),
        # which is why the library can verify it with cryptography's ECDSA primitive.
        signature = self._key.sign(auth_data + digest, ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(self.user_id.bytes),
            },
            "clientExtensionResults": {},
        }

    # --- internals ----------------------------------------------------------------------
    def _flags(self, user_verified: bool) -> int:
        flags = FLAG_USER_PRESENT
        if user_verified:
            flags |= FLAG_USER_VERIFIED
        return flags

    def _cose_public_key(self) -> bytes:
        numbers = self._key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )
