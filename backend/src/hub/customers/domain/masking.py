"""What a caller may see of a person (D-14, FR-16), computed and never stored.

Two pure functions, because both values are derived on read: a masked number must follow the
stored one when support fixes a typo, and a pseudonym must not need a column to exist (D-08's
"no write on read" applies to identity too). The stored ``customers.pseudonym`` wins when it is
set — erasure writes it — so the same customer keeps one stable pseudonym across a change of
key or algorithm.

The mask keeps the country code and the last two digits (the slice's default, Plan notes 3):
``+243810000042`` reads ``+243*******42``. Enough for a support call ("the number ending 42"), not
enough to dial. Every hidden digit stays a ``*``, so the length still matches what is printed on
a form and a mask cannot be mistaken for a shorter number.
"""

from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

__all__ = ["MASKED_COUNTRY_CODE_DIGITS", "mask_phone", "pseudonym"]

# DRC's calling code is three digits (+243); a deployment elsewhere changes this constant with
# its own numbering plan. Guessing it per number would need a country-code database.
MASKED_COUNTRY_CODE_DIGITS = 3

PSEUDONYM_PREFIX = "c-"
PSEUDONYM_LENGTH = 12


def mask_phone(phone: str | None) -> str | None:
    """``+243*******42``: country code and last two digits, every other digit hidden."""
    if not phone:
        return phone
    digits = phone.lstrip("+")
    keep = MASKED_COUNTRY_CODE_DIGITS + 2
    if len(digits) <= keep:
        # Nothing left to hide: masking less than two digits would reveal the number itself.
        return f"+{'*' * len(digits)}"
    hidden = len(digits) - keep
    return f"+{digits[:MASKED_COUNTRY_CODE_DIGITS]}{'*' * hidden}{digits[-2:]}"


def pseudonym(customer_id: UUID, key: str) -> str:
    """``c-`` plus the first 12 hex characters of ``HMAC-SHA256(key, id)``.

    Keyed, so the same uuid in another deployment (or after a key rotation) is a different
    string: an operations engineer can follow one customer's meters without being able to match
    the value against a customer id they can guess.
    """
    digest = hmac.new(key.encode("utf-8"), str(customer_id).encode("utf-8"), hashlib.sha256)
    return f"{PSEUDONYM_PREFIX}{digest.hexdigest()[:PSEUDONYM_LENGTH]}"
