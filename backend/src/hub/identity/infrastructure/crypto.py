"""Settings-dependent cryptographic helpers of `identity`: the phone HMAC and the MFA key.

The pure primitives — refresh-token digests, recovery codes, password hashes, TOTP secrets —
live in ``hub.shared.security`` so a use case can use them without importing Django. What
stays here is what needs this deployment's configuration:

* ``hmac_phone`` keys on ``HUB_PHONE_HMAC_KEY`` (D-21): the number is stored as an HMAC so rate
  limits and attempt counters work without the database ever holding it.
* ``encrypt_secret``/``decrypt_secret`` wrap a TOTP secret in ``Fernet`` under a key derived
  from ``HUB_SECRET_KEY``. A secret has to be readable again to compute codes, so hashing is
  not an option; deriving the key from the deployment's own configuration means a database
  dump alone cannot decrypt it.

Both raise rather than falling back when the configuration is missing, because a silent
fallback here is a security regression that nobody would notice.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from hub.shared.security import (
    DUMMY_HASH,
    compare,
    digest_token,
    hash_password,
    hash_recovery_code,
    new_recovery_code,
    new_refresh_token,
    new_totp_secret,
    verify_password,
)

__all__ = [
    "DUMMY_HASH",
    "compare",
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


def hmac_phone(e164: str) -> bytes:
    """The HMAC the OTP table keys on (D-21): 32 bytes, no way back to the number."""
    key = str(settings.HUB_PHONE_HMAC_KEY).encode("utf-8")
    return hmac.new(key, e164.encode("utf-8"), hashlib.sha256).digest()


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
