"""Phone numbers arrive in every shape a person types them (Review focus 2).

The registry stores E.164 and nothing else (``ck_customers_phone``), and D-11 makes the
number the natural key, so normalisation has to happen *before* the uniqueness check: a
number stored in two shapes is two customers for one person, which no later query can fix.

The rules are deliberately narrow — the ones the contract can promise from a form:

* spaces, dots, dashes, parentheses and the leading ``+`` spacing are formatting;
* a leading ``00`` is the international prefix written the old way;
* a leading single ``0`` is a national number, completed with the deployment's default
  country prefix (``HUB_DEFAULT_PHONE_PREFIX``, ``+243`` for the DRC);
* everything else must already be ``+`` followed by 8 to 15 digits (E.164 allows 15).
"""

from __future__ import annotations

import re

from hub.customers.domain.errors import InvalidPhoneNumber

__all__ = ["normalise_e164"]

_FORMATTING = re.compile(r"[\s.\-()/]")
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def normalise_e164(raw: str, default_prefix: str = "+243") -> str:
    """Return the E.164 form of ``raw`` or raise ``InvalidPhoneNumber`` (422)."""
    candidate = _FORMATTING.sub("", str(raw or "")).strip()
    if not candidate:
        raise InvalidPhoneNumber()

    if candidate.startswith("+"):
        normalised = candidate
    elif candidate.startswith("00"):
        normalised = "+" + candidate[2:]
    elif candidate.startswith("0"):
        # A national number: drop the trunk zero, prepend the deployment's country.
        normalised = default_prefix + candidate[1:]
    else:
        normalised = candidate

    if not _E164.fullmatch(normalised):
        raise InvalidPhoneNumber()
    return normalised
