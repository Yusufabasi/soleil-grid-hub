"""Test-only principals: a real ``hub.users`` row, and an authenticator that serves it.

Task 01.3 replaces both halves with the production ones (``UserRepository.load_principal``
and ``BearerJwtAuthentication``). Until then the integration tests still need a caller, and
seeded rows beat hand-built objects: the audit read path checks ``audit.read`` in the
database, so a principal that exists only in memory would be refused there.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from django.db import connection
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from hub.identity.contracts import Principal
from hub.identity.infrastructure.models import User, UserRole

__all__ = ["SeededPrincipalAuthentication", "SeededUser", "seed_staff_user"]


@dataclass(frozen=True)
class SeededUser:
    """One seeded staff user and the principal its row produces."""

    principal: Principal

    @property
    def user_id(self) -> UUID:
        return self.principal.subject_id

    def headers(self) -> dict[str, str]:
        """Django test-client headers that authenticate as this user."""
        return {"X-Test-User": str(self.user_id)}


def seed_staff_user(
    *roles: str,
    attributes: tuple[str, ...] = (),
    status: str = "active",
    all_sites: bool = False,
) -> SeededUser:
    """Insert an active staff user holding ``roles`` and return its principal."""
    user_id = uuid.uuid4()
    User.objects.create(
        id=user_id,
        email=f"{user_id}@example.test",
        display_name=f"Test {roles[0] if roles else 'user'}",
        status=status,
        all_sites=all_sites,
        finance_lead="finance_lead" in attributes,
        dp_admin="dp_admin" in attributes,
        token_version=1,
    )
    for role in roles:
        UserRole.objects.create(user_id=user_id, role_code=role)
    return SeededUser(
        principal=Principal(
            subject_id=user_id,
            kind="staff",
            roles=frozenset(roles),
            attributes=frozenset(attributes),
            session_id=uuid.uuid4(),
            token_version=1,
            locale="en",
        )
    )


def _load_principal(subject: UUID) -> Principal:
    """Roles and attributes of an existing user, read exactly as production will read them."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.status, u.token_version, u.preferred_language, u.all_sites,
                   u.finance_lead, u.dp_admin,
                   coalesce(array_agg(ur.role_code) FILTER (WHERE ur.role_code IS NOT NULL), '{}')
              FROM hub.users u
              LEFT JOIN hub.user_roles ur ON ur.user_id = u.id
             WHERE u.id = %s AND u.deleted_at IS NULL
             GROUP BY u.id
            """,
            [subject],
        )
        row = cursor.fetchone()
    if row is None:
        raise AuthenticationFailed("unauthenticated")
    status, token_version, language, _all_sites, finance_lead, dp_admin, roles = row
    if status != "active":
        raise AuthenticationFailed("token_revoked")

    attributes = {
        name for name, held in (("finance_lead", finance_lead), ("dp_admin", dp_admin)) if held
    }
    return Principal(
        subject_id=subject,
        kind="staff",
        roles=frozenset(roles),
        attributes=frozenset(attributes),
        session_id=uuid.uuid4(),
        token_version=int(token_version),
        locale="fr" if language == "fr" else "en",
    )


class SeededPrincipalAuthentication(BaseAuthentication):
    """Test-only scheme: ``X-Test-User`` names a ``hub.users`` id, roles come from the database."""

    def authenticate(self, request: Request) -> tuple[Principal, None] | None:
        raw = request.headers.get("X-Test-User")
        if not raw:
            return None
        try:
            subject = UUID(raw)
        except ValueError as exc:
            raise AuthenticationFailed("unauthenticated") from exc
        principal = _load_principal(subject)
        # `setattr` because Django's request type declares no such attribute; the permission
        # class reads it, exactly as the production authentication class will set it.
        setattr(request, "principal", principal)  # noqa: B010
        return principal, None
