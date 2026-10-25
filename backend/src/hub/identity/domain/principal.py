"""Who is calling, as every module sees them (docs/03 section 2.2).

The principal is the only thing a module may learn about the caller: the token's claims are
turned into this value once, by the authentication class, and everything else — permission
checks, site scoping, ownership, audit actors — reads it. Roles and attributes are loaded from
the database on every authentication, never trusted from the token (docs/03 2.2, D-17).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

__all__ = ["Principal", "PrincipalKind"]

# Machine principals are authentication schemes, not roles (docs/03 2.2).
PrincipalKind = Literal["staff", "customer", "provider", "gateway", "integrator", "public"]

Locale = Literal["en", "fr"]

# `acr` at least "mfa" is what step-up operations require (docs/05 section 4).
BASIC_ACR = "pwd"
MFA_ACR = "mfa"


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller: identity, roles, attributes and the session it came from."""

    subject_id: UUID
    kind: PrincipalKind
    roles: frozenset[str] = frozenset()
    attributes: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    site_ids: frozenset[UUID] = frozenset()
    customer_id: UUID | None = None
    session_id: UUID | None = None
    token_version: int = 0
    # When this session last proved identity: login, or the last step-up (D-16).
    auth_time: datetime | None = None
    acr: str = BASIC_ACR
    locale: Locale = "en"

    @property
    def stepped_up_at(self) -> datetime | None:
        """docs/03 2.2's name for the same instant as :attr:`auth_time`."""
        return self.auth_time

    @property
    def has_mfa(self) -> bool:
        """True when the session authenticated with a second factor (docs/05 section 4)."""
        return self.acr == MFA_ACR
