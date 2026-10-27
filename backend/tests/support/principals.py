"""Test-only principals: a real ``hub.users`` row, and an authenticator that serves it.

Task 01.3 replaces both halves with the production ones (``UserRepository.load_principal``
and ``BearerJwtAuthentication``). Until then the integration tests still need a caller, and
seeded rows beat hand-built objects: the audit read path checks ``audit.read`` in the
database, so a principal that exists only in memory would be refused there.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

    def headers(self, *, auth_age_seconds: int = 0) -> dict[str, str]:
        """Django test-client headers that authenticate as this user.

        ``auth_age_seconds`` ages the session, which is the only way to prove a step-up window:
        every ledger write route requires one (docs/05 section 4, D-16).
        """
        headers = {"X-Test-User": str(self.user_id)}
        if auth_age_seconds:
            headers["X-Test-Auth-Age"] = str(auth_age_seconds)
        return headers


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
            # Read from the role matrix, as production reads it: a maker-checker executor
            # decides with `required_permission in principal.permissions`, so a principal
            # built without them could not propose anything.
            permissions=_permissions_of(user_id),
            session_id=uuid.uuid4(),
            token_version=1,
            auth_time=datetime.now(UTC),
            locale="en",
        )
    )


def _permissions_of(user_id: UUID) -> frozenset[str]:
    """The permission codes the user's roles grant (hub.role_permissions)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT coalesce(array_agg(DISTINCT rp.permission_code), '{}')
              FROM hub.user_roles ur
              JOIN hub.role_permissions rp ON rp.role_code = ur.role_code
             WHERE ur.user_id = %s
            """,
            [user_id],
        )
        row = cursor.fetchone()
    return frozenset(row[0]) if row else frozenset()


def _load_principal(subject: UUID, *, auth_age_seconds: int = 0) -> Principal:
    """Roles, attributes and permissions of an existing user, as production reads them.

    ``auth_age_seconds`` is how long ago the session proved identity: 0 is a fresh sign-in, and
    a larger value is what lets a test show that a step-up operation refuses an old session
    (the permission class compares it with the operation's ``x-step-up`` window).
    """
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
        permissions=_permissions_of(subject),
        session_id=uuid.uuid4(),
        token_version=int(token_version),
        auth_time=datetime.now(UTC) - timedelta(seconds=auth_age_seconds),
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
        try:
            # The step-up window is proved with this: 0 is fresh, anything above the operation's
            # `x-step-up.max_age_seconds` must be refused with 401 step_up_required.
            age = int(request.headers.get("X-Test-Auth-Age", "0"))
        except ValueError as exc:
            raise AuthenticationFailed("unauthenticated") from exc
        principal = _load_principal(subject, auth_age_seconds=age)
        # `setattr` because Django's request type declares no such attribute; the permission
        # class reads it, exactly as the production authentication class will set it.
        setattr(request, "principal", principal)  # noqa: B010
        return principal, None
