"""Django-free cryptographic primitives: token digests, recovery codes, password hashing.

The application layer owns the *rules* of authentication (single use, rotation, lockout) and
must be able to hash and compare without importing Django, which the import-linter contract
forbids. Everything here is pure Python plus a library that has no framework in it, so a use
case can depend on it directly; the settings-dependent half (the phone HMAC key, the MFA
encryption key) lives in ``hub.identity.infrastructure.crypto``.

Four rules shape the module:

* **Nothing is stored in the clear.** A refresh token is 32 random bytes base64url and the row
  holds ``sha256(token)``; a recovery code is 10 base32 characters and the row holds its
  SHA-256. Comparisons are ``hmac.compare_digest``.
* **Unknown accounts cost the same as known ones.** :data:`DUMMY_HASH` is a real Argon2id hash
  of a throwaway password, so a login for an address that does not exist still pays a full
  verification before being refused.
* **Codes are normalised before hashing**, so a user typing lower case or a space still works
  and the database never sees the typed form.
* **Argon2id at the library's RFC 9106 defaults**: 64 MiB, three passes, one lane.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

__all__ = [
    "DUMMY_HASH",
    "compare",
    "digest_token",
    "hash_password",
    "hash_recovery_code",
    "new_recovery_code",
    "new_refresh_token",
    "new_totp_secret",
    "verify_password",
]

_hasher = PasswordHasher()

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


def new_totp_secret() -> str:
    """A fresh base32 secret for TOTP (pyotp's own generator, so enrolment matches verify)."""
    return pyotp.random_base32()


def compare(left: bytes, right: bytes) -> bool:
    """Constant-time comparison of two digests."""
    return hmac.compare_digest(left, right)
