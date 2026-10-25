"""A seeded staff account that can actually sign in: password hash, TOTP secret, roles.

``seed_staff_user`` is enough for authorization tests, which never present a credential. These
flows do, so this helper writes the same rows the real enrolment writes — an Argon2id hash and
an encrypted base32 secret — and hands back the plain secret so a test can compute codes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from hub.identity.domain.principal import Principal
from hub.identity.infrastructure.crypto import encrypt_secret
from hub.identity.infrastructure.models import User, UserRole, UserSite
from hub.shared.security import hash_password, new_totp_secret

__all__ = ["LocalUser", "PASSWORD", "seed_local_user"]

PASSWORD = "correct horse battery staple"


@dataclass(frozen=True)
class LocalUser:
    """One seeded staff account, its plain TOTP secret and the principal its row produces."""

    user_id: UUID
    email: str
    secret: str
    principal: Principal

    def headers(self) -> dict[str, str]:
        return {"X-Test-User": str(self.user_id)}


def seed_local_user(
    *roles: str,
    password: str = PASSWORD,
    mfa: bool = True,
    status: str = "active",
    attributes: tuple[str, ...] = (),
    site_ids: tuple[UUID, ...] = (),
) -> LocalUser:
    user_id = uuid.uuid4()
    email = f"{user_id}@example.test"
    secret = new_totp_secret()
    User.objects.create(
        id=user_id,
        email=email,
        display_name=f"Test {roles[0] if roles else 'user'}",
        password_hash=hash_password(password),
        mfa_secret_enc=encrypt_secret(secret) if mfa else None,
        mfa_enrolled_at=datetime.now(UTC) if mfa else None,
        status=status,
        preferred_language="en",
        finance_lead="finance_lead" in attributes,
        dp_admin="dp_admin" in attributes,
        token_version=1,
    )
    for role in roles:
        UserRole.objects.create(user_id=user_id, role_code=role)
    for site_id in site_ids:
        UserSite.objects.create(user_id=user_id, site_id=site_id)
    return LocalUser(
        user_id=user_id,
        email=email,
        secret=secret,
        principal=Principal(
            subject_id=user_id,
            kind="staff",
            display_name=f"Test {roles[0] if roles else 'user'}",
            mfa_enrolled=mfa,
            roles=frozenset(roles),
            attributes=frozenset(attributes),
            site_ids=frozenset(site_ids),
            session_id=uuid.uuid4(),
            token_version=1,
            locale="en",
        ),
    )
