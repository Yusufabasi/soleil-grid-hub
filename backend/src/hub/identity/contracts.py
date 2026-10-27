"""Public surface of `identity`: Protocols, DTOs and domain errors other modules
may use. Nothing else in this package is importable from outside (docs/03 section 0).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from hub.identity.domain.principal import Principal, PrincipalKind

__all__ = [
    "AuditEntry",
    "AuditLog",
    "AuditOutcome",
    "CustomerLookup",
    "CustomerRecord",
    "Principal",
    "PrincipalKind",
    "SessionRow",
    "SessionStore",
    "TokenClaims",
    "WebauthnCredentialRow",
]

# The three endings of an audited action (api/openapi.yaml, ``AuditEntry.outcome``).
AuditOutcome = Literal["success", "denied", "error"]


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """What a verified access token says: identity and session, never power."""

    subject_id: UUID
    session_id: UUID
    kind: str
    token_version: int
    locale: str
    auth_time: datetime
    acr: str
    amr: tuple[str, ...]

    @property
    def has_mfa(self) -> bool:
        return self.acr in {"mfa", "phr"}


@dataclass(frozen=True, slots=True)
class CustomerRecord:
    """The little a customer sign-in needs to know about a customer (D-21).

    ``last_login_at`` is a session fact, not a column: it is derived from the customer's
    refresh sessions, so a dormant account is recognised without a second field to maintain.
    """

    id: UUID
    locale: str
    last_login_at: datetime | None


class CustomerLookup(Protocol):
    """Finds an active customer for a sign-in (slice 02 binds this to the registry).

    ``identity`` owns the one-time-code flow; ``hub.customers`` owns the rows, so the lookup
    is contributed at start-up rather than imported (the module DAG lets ``customers`` see
    ``identity``, never the reverse).
    """

    def find_active_by_phone(self, phone_e164: str) -> CustomerRecord | None: ...

    def by_id(self, customer_id: UUID) -> CustomerRecord | None: ...


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One row of a stream, as the read path returns it (``api/openapi.yaml`` AuditEntry).

    ``hash`` and ``prev_hash`` are hex so a client can display the chain; the first row of a
    stream has no predecessor and carries an empty ``prev_hash``.
    """

    id: UUID
    stream: str
    seq: int
    occurred_at: datetime
    actor_type: str
    actor_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID | None
    outcome: str
    request_id: str | None
    details: Mapping[str, Any]
    hash: str
    prev_hash: str


@dataclass(frozen=True, slots=True)
class SessionRow:
    """One row of ``hub.refresh_tokens``, as the session service reasons about it.

    A row is one refresh token: its family is the login, and the first row of the family pins
    the absolute limit. ``rotated_at``/``revoked_at`` are what make a replay detectable.
    """

    id: UUID
    user_id: UUID | None
    customer_id: UUID | None
    family_id: UUID
    issued_at: datetime
    expires_at: datetime
    rotated_at: datetime | None
    revoked_at: datetime | None

    @property
    def kind(self) -> str:
        """The account kind the session belongs to: staff sessions name a user, customer ones a customer."""
        return "staff" if self.user_id is not None else "customer"


class SessionStore(Protocol):
    """The rows of ``hub.refresh_tokens`` (docs/03 2.2, D-17).

    ``find_by_hash(..., for_update=True)`` locks the row: rotation is a compare-and-set and two
    tabs refreshing at once must not both succeed.
    """

    def insert(
        self,
        row: SessionRow,
        token_hash: bytes,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> None: ...

    def find_by_hash(self, token_hash: bytes, *, for_update: bool = False) -> SessionRow | None: ...

    def family_first_issued_at(self, family_id: UUID) -> datetime | None: ...

    def mark_rotated(self, session_id: UUID, *, at: datetime, replaced_by: UUID) -> None: ...

    def revoke_family(self, family_id: UUID, *, at: datetime) -> int: ...

    def revoke(self, session_id: UUID, *, at: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class WebauthnCredentialRow:
    """One row of ``hub.webauthn_credentials``, as registration and assertion see it."""

    id: UUID
    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: tuple[str, ...]
    name: str
    created_at: datetime
    last_used_at: datetime | None

    def document(self) -> dict[str, Any]:
        """``WebAuthnCredential`` of the contract."""
        return {
            "id": str(self.id),
            "name": self.name,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "last_used_at": (
                self.last_used_at.isoformat().replace("+00:00", "Z")
                if self.last_used_at is not None
                else None
            ),
        }


class AuditLog(Protocol):
    """The append-only, hash-chained audit trail (D-22, docs/03 section 2.2).

    ``record`` writes through ``hub.audit_append`` and must be the last call in a unit of
    work: the advisory lock it takes lives until the transaction commits, which is what makes
    each stream's ``seq`` dense and its chain gap-free. ``page`` reads one stream forward
    through ``hub.audit_page``.
    """

    def record(
        self,
        *,
        stream: str,
        actor: Principal | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        site_id: UUID | None,
        details: Mapping[str, Any],
        outcome: AuditOutcome = "success",
        ip: str | None = None,
        subject_customer_id: UUID | None = None,
    ) -> UUID:
        """Append one row and return its id."""
        ...

    def page(self, stream: str, *, after_seq: int, limit: int) -> Sequence[AuditEntry]:
        """Rows of ``stream`` with ``seq > after_seq``, oldest first."""
        ...
