"""Cryptographic primitives in one place: keys, hashes, TOTP secrets, recovery codes.

Everything here is an adapter detail of `identity`, so it lives in ``infrastructure`` and is
reached through wiring. Four rules shape the module:

* **Passwords and codes are never stored, only their hashes.** A refresh token is 32 random
  bytes base64url and the row holds ``sha256(token)``; a recovery code is 10 base32
  characters and the row holds ``sha256(code)``. Arithmetic on those is ``hmac.compare_digest``.
* **A phone number is stored as an HMAC** (D-21), so rate limits and attempt counters work
  without the database ever holding the number.
* **A TOTP secret is encrypted, not hashed**: the server has to compute codes from it, so it
  needs the value back. ``Fernet`` with a key derived from ``HUB_SECRET_KEY`` keeps the
  ciphertext useless without the process's configuration.
* **Unknown accounts cost the same as known ones.** ``DUMMY_HASH`` is a real Argon2id hash of
  a throwaway password, so a login for an address that does not exist still pays a full
  verification before being refused.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

__all__ = [
    "DUMMY_HASH",
    "decrypt_secret",
    "digest_token",
    "encrypt_secret",
    "hash_password",
    "hash_recovery_code",
    "hmac_phone",
    "new_recovery_code",
    "new_refresh_token",
    "new_totp_secret",
    "verify_password",
]

# Argon2id with the library's RFC 9106 defaults: 64 MiB, 3 passes, one lane.
_hasher = PasswordHasher()

# A real hash of a value nobody knows, verified for an address that has no account (D-21).
# Not a credential: it is verified against no account, only to spend the same CPU time.
DUMMY_PASSWORD = "dummy-password-never-verified-for-a-real-account"  # noqa: S105
DUMMY_HASH = _hasher.hash(DUMMY_PASSWORD)

REFRESH_TOKEN_BYTES = 32
RECOVERY_CODE_LENGTH = 10


def hash_password(password: str) -> str:
    """Argon2id hash, self-describing (the parameters are inside the string)."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Verify against a stored hash, never raising on a malformed one.

    ``password_hash`` is ``None`` for an account that signs in through the identity provider;
    the caller then verifies against :data:`DUMMY_HASH` so the timing is the same.
    """
    digest = password_hash if password_hash else DUMMY_HASH
    try:
        return _hasher.verify(digest, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_refresh_token() -> str:
    """An opaque, URL-safe refresh token: 32 random bytes, 43 characters."""
    return base64.urlsafe_b64encode(secrets.token_bytes(REFRESH_TOKEN_BYTES)).decode().rstrip("=")


def digest_token(token: str) -> bytes:
    """The 32-byte value ``hub.refresh_tokens.token_hash`` stores."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def new_recovery_code() -> str:
    """One recovery code: ten base32 characters, unambiguous by construction."""
    return base64.b32encode(secrets.token_bytes(7)).decode().rstrip("=")[:RECOVERY_CODE_LENGTH]


def hash_recovery_code(code: str) -> bytes:
    """Recovery codes are compared by hash; normalised so lower case still works."""
    normalised = code.strip().replace(" ", "").upper()
    return hashlib.sha256(normalised.encode("utf-8")).digest()


def hmac_phone(e164: str) -> bytes:
    """The HMAC the OTP table keys on (D-21): 32 bytes, no way back to the number."""
    key = str(settings.HUB_PHONE_HMAC_KEY).encode("utf-8")
    return hmac.new(key, e164.encode("utf-8"), hashlib.sha256).digest()


def new_totp_secret() -> str:
    """A fresh base32 secret for TOTP (pyotp's own generator, so enrolment matches verify)."""
    return pyotp.random_base32()


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """One Fernet instance per process, keyed by a SHA-256 of the Django secret key."""
    material = hashlib.sha256(f"mfa:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def encrypt_secret(value: str) -> bytes:
    """Encrypt a TOTP secret for ``users.mfa_secret_enc``."""
    return _fernet().encrypt(value.encode("utf-8"))


def decrypt_secret(ciphertext: bytes | memoryview) -> str:
    """Decrypt ``users.mfa_secret_enc``; a rotated secret key surfaces as ValueError."""
    try:
        return _fernet().decrypt(bytes(ciphertext)).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("mfa secret cannot be decrypted with this configuration") from exc
