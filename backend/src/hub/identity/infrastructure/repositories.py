"""Repositories over the identity tables (ADR-0020: plain SQL, no ORM writes).

Every class here is an adapter: it speaks SQL, returns DTOs and raises kernel errors, and the
application layer sees it through a Protocol in ``identity.contracts``. Reads of the identity
tables are plain ``SELECT``; the one write that changes authority (``bump_token_version``)
goes through the database as an ``UPDATE`` of a single column, which is the only thing a role
change needs to invalidate every access token a user holds (D-17).

The token version is cached for sixty seconds because it is read on *every* request: without
it each call would cost a query, and with it a revocation still takes effect within a minute.
``bump_token_version`` deletes the key, so a change made by this process is immediate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from django.db import connection

from hub.identity.contracts import SessionRow, TokenClaims, WebauthnCredentialRow
from hub.identity.domain.principal import Locale, Principal
from hub.identity.infrastructure.crypto import decrypt_secret, encrypt_secret
from hub.shared.contracts import KeyValueStore
from hub.shared.errors import Unauthenticated
from hub.shared.ids import uuid7

__all__ = ["TOKEN_VERSION_TTL_SECONDS", "SessionRepository", "UserRecord", "UserRepository"]

# How long a token version may be stale; a bump deletes the key, so this bounds only the
# window in which another process's bump is not yet visible.
TOKEN_VERSION_TTL_SECONDS = 60

# The column list is a literal and the predicates below are constants, so the only values a
# statement carries are bound parameters (executor guide rule 7).
_USER_SELECT = (
    "SELECT u.id, u.email, u.display_name, u.password_hash, u.idp_subject, u.mfa_secret_enc, "
    "u.mfa_enrolled_at, u.status, u.preferred_language, u.all_sites, u.finance_lead, "
    "u.dp_admin, u.token_version, u.last_login_at FROM hub.users u"
)


@dataclass(frozen=True, slots=True)
class UserRecord:
    """The row a login flow needs, without the ORM and without surprises."""

    id: UUID
    email: str
    display_name: str
    password_hash: str | None
    idp_subject: str | None
    mfa_secret_enc: bytes | None
    mfa_enrolled_at: datetime | None
    status: str
    preferred_language: str
    all_sites: bool
    finance_lead: bool
    dp_admin: bool
    token_version: int
    last_login_at: datetime | None

    @property
    def attributes(self) -> frozenset[str]:
        held = {
            name
            for name, value in (
                ("finance_lead", self.finance_lead),
                ("dp_admin", self.dp_admin),
            )
            if value
        }
        return frozenset(held)

    @property
    def locale(self) -> str:
        return "fr" if self.preferred_language == "fr" else "en"

    @property
    def mfa_enrolled(self) -> bool:
        return self.mfa_secret_enc is not None or self.mfa_enrolled_at is not None


def _record(row: Any) -> UserRecord:
    values = tuple(row)
    return UserRecord(
        id=cast("UUID", values[0]),
        email=str(values[1]),
        display_name=str(values[2]),
        password_hash=cast("str | None", values[3]),
        idp_subject=cast("str | None", values[4]),
        mfa_secret_enc=bytes(values[5]) if values[5] is not None else None,
        mfa_enrolled_at=cast("datetime | None", values[6]),
        status=str(values[7]),
        preferred_language=str(values[8]),
        all_sites=bool(values[9]),
        finance_lead=bool(values[10]),
        dp_admin=bool(values[11]),
        token_version=int(values[12]),
        last_login_at=cast("datetime | None", values[13]),
    )


class UserRepository:
    """Reads of ``hub.users`` and the small writes the session flows need."""

    def __init__(self, store: KeyValueStore) -> None:
        self._store = store

    # --- reads -------------------------------------------------------------------------
    def get(self, user_id: UUID) -> UserRecord | None:
        with connection.cursor() as cursor:
            cursor.execute(_USER_SELECT + " WHERE u.id = %s AND u.deleted_at IS NULL", [user_id])
            row = cursor.fetchone()
        return _record(row) if row is not None else None

    def find_by_email(self, email: str) -> UserRecord | None:
        """Case-insensitive lookup; ``ck_users_email_lower`` guarantees one row at most."""
        with connection.cursor() as cursor:
            cursor.execute(
                _USER_SELECT + " WHERE u.email = lower(%s) AND u.deleted_at IS NULL",
                [email.strip()],
            )
            row = cursor.fetchone()
        return _record(row) if row is not None else None

    def find_by_idp_subject(self, subject: str) -> UserRecord | None:
        with connection.cursor() as cursor:
            cursor.execute(
                _USER_SELECT + " WHERE u.idp_subject = %s AND u.deleted_at IS NULL",
                [subject],
            )
            row = cursor.fetchone()
        return _record(row) if row is not None else None

    def roles_of(self, user_id: UUID) -> frozenset[str]:
        with connection.cursor() as cursor:
            cursor.execute("SELECT role_code FROM hub.user_roles WHERE user_id = %s", [user_id])
            return frozenset(str(row[0]) for row in cursor.fetchall())

    def site_ids_of(self, user_id: UUID) -> frozenset[UUID]:
        with connection.cursor() as cursor:
            cursor.execute("SELECT site_id FROM hub.user_sites WHERE user_id = %s", [user_id])
            return frozenset(cast("UUID", row[0]) for row in cursor.fetchall())

    def permissions_of(self, user_id: UUID) -> frozenset[str]:
        """The role permissions of the user, for the authorizer (docs/03 2.2)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT rp.permission_code FROM hub.user_roles ur "
                "JOIN hub.role_permissions rp ON rp.role_code = ur.role_code "
                "WHERE ur.user_id = %s",
                [user_id],
            )
            return frozenset(str(row[0]) for row in cursor.fetchall())

    def token_version(self, user_id: UUID) -> int | None:
        """The account's token version, cached for a minute (``None``: no such account)."""
        key = self._version_key(user_id)
        cached = self._store.get(key)
        if cached is not None:
            return int(cached)
        record = self.get(user_id)
        if record is None:
            return None
        self._store.set(key, str(record.token_version), ttl_seconds=TOKEN_VERSION_TTL_SECONDS)
        return record.token_version

    # --- writes ------------------------------------------------------------------------
    def bump_token_version(self, user_id: UUID) -> int:
        """Invalidate every access token of this user and drop the cache immediately."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.users SET token_version = token_version + 1 WHERE id = %s "
                "RETURNING token_version",
                [user_id],
            )
            row = cursor.fetchone()
        if row is None:
            raise Unauthenticated()
        self._store.delete(self._version_key(user_id))
        return int(row[0])

    def webauthn_credentials(self, user_id: UUID) -> list[WebauthnCredentialRow]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, credential_id, public_key, sign_count, transports, name, created_at, "
                "last_used_at FROM hub.webauthn_credentials WHERE user_id = %s ORDER BY created_at",
                [user_id],
            )
            return [_credential(row) for row in cursor.fetchall()]

    def find_webauthn_credential(self, credential_id: bytes) -> WebauthnCredentialRow | None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, credential_id, public_key, sign_count, transports, name, created_at, "
                "last_used_at FROM hub.webauthn_credentials WHERE credential_id = %s",
                [credential_id],
            )
            row = cursor.fetchone()
        return _credential(row) if row is not None else None

    def insert_webauthn_credential(
        self,
        user_id: UUID,
        *,
        credential_id: bytes,
        public_key: bytes,
        sign_count: int,
        transports: list[str],
        name: str,
        at: datetime,
    ) -> WebauthnCredentialRow:
        credential_row_id = uuid7()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.webauthn_credentials "
                "(id, user_id, credential_id, public_key, sign_count, transports, name, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, credential_id, public_key, "
                "sign_count, transports, name, created_at, last_used_at",
                [
                    credential_row_id,
                    user_id,
                    credential_id,
                    public_key,
                    sign_count,
                    transports,
                    name,
                    at,
                ],
            )
            row = cursor.fetchone()
        if row is None:  # pragma: no cover - RETURNING always answers on a successful insert
            raise RuntimeError("webauthn credential insert returned no row")
        return _credential(row)

    def touch_webauthn_credential(
        self, credential_row_id: UUID, *, sign_count: int, at: datetime
    ) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.webauthn_credentials SET sign_count = %s, last_used_at = %s WHERE id = %s",
                [sign_count, at, credential_row_id],
            )

    def has_webauthn_credential(self, user_id: UUID) -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM hub.webauthn_credentials WHERE user_id = %s LIMIT 1", [user_id]
            )
            return cursor.fetchone() is not None

    def count_unused_recovery_codes(self, user_id: UUID) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM hub.mfa_recovery_codes WHERE user_id = %s AND used_at IS NULL",
                [user_id],
            )
            row = cursor.fetchone()
        return int(row[0]) if row is not None else 0

    def insert_recovery_codes(self, user_id: UUID, hashes: list[bytes]) -> None:
        if not hashes:
            return
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO hub.mfa_recovery_codes (id, user_id, code_hash) VALUES (%s, %s, %s)",
                [(uuid7(), user_id, digest) for digest in hashes],
            )

    def replace_recovery_codes(self, user_id: UUID, hashes: list[bytes]) -> None:
        """Invalidate the previous set and store a new one in one transaction."""
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM hub.mfa_recovery_codes WHERE user_id = %s", [user_id])
        self.insert_recovery_codes(user_id, hashes)

    def delete_recovery_codes(self, user_id: UUID) -> int:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM hub.mfa_recovery_codes WHERE user_id = %s", [user_id])
            return int(cursor.rowcount)

    def consume_recovery_code(self, user_id: UUID, code_hash: bytes, *, at: datetime) -> bool:
        """Mark one unused code used; False when it was already used or never existed."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.mfa_recovery_codes SET used_at = %s "
                "WHERE user_id = %s AND code_hash = %s AND used_at IS NULL",
                [at, user_id, code_hash],
            )
            return int(cursor.rowcount) == 1

    def store_mfa_enrollment(
        self,
        user_id: UUID,
        *,
        secret: str,
        recovery_hashes: list[bytes],
        at: datetime,
    ) -> None:
        """Write the encrypted TOTP secret and the first set of recovery codes together."""
        encrypted = encrypt_secret(secret)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.users SET mfa_secret_enc = %s, mfa_enrolled_at = %s WHERE id = %s",
                [encrypted, at, user_id],
            )
        self.replace_recovery_codes(user_id, recovery_hashes)

    def totp_secret(self, user_id: UUID) -> str:
        """The decrypted TOTP secret; a configuration change surfaces as Unauthenticated."""
        record = self.get(user_id)
        if record is None or record.mfa_secret_enc is None:
            raise Unauthenticated(code="invalid_code")
        try:
            return decrypt_secret(record.mfa_secret_enc)
        except ValueError as exc:
            raise Unauthenticated(code="invalid_code") from exc

    def clear_mfa(self, user_id: UUID) -> None:
        """Forget the second factor: secret, enrolment time, recovery codes and credentials."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.users SET mfa_secret_enc = NULL, mfa_enrolled_at = NULL WHERE id = %s",
                [user_id],
            )
            cursor.execute("DELETE FROM hub.mfa_recovery_codes WHERE user_id = %s", [user_id])
            cursor.execute("DELETE FROM hub.webauthn_credentials WHERE user_id = %s", [user_id])

    def set_password_hash(self, user_id: UUID, password_hash: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.users SET password_hash = %s WHERE id = %s", [password_hash, user_id]
            )

    def update_profile(
        self, user_id: UUID, *, display_name: str | None, locale: str | None
    ) -> None:
        """Change the two fields a user owns; anything else is an administrator's decision."""
        if display_name is not None and locale is not None:
            sql = (
                "UPDATE hub.users SET display_name = %s, preferred_language = %s, "
                "updated_at = now() WHERE id = %s"
            )
            params: list[Any] = [display_name, locale, user_id]
        elif display_name is not None:
            sql = "UPDATE hub.users SET display_name = %s, updated_at = now() WHERE id = %s"
            params = [display_name, user_id]
        elif locale is not None:
            sql = "UPDATE hub.users SET preferred_language = %s, updated_at = now() WHERE id = %s"
            params = [locale, user_id]
        else:
            return
        with connection.cursor() as cursor:
            cursor.execute(sql, params)

    def mark_login(self, user_id: UUID, instant: datetime) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.users SET last_login_at = %s WHERE id = %s", [instant, user_id]
            )

    # --- the principal ---------------------------------------------------------------
    def load_principal(self, claims: TokenClaims) -> Principal:
        """Build the caller the request acts for, refusing a stale token version.

        The token proves *who*; this method decides whether that identity is still current
        and what it may do. Suspension and deletion are checked here, never in the token.
        A customer token names a ``hub.customers`` row instead of a staff account, so the two
        kinds take different paths — the portal's caller has no roles beyond its kind.
        """
        if claims.kind == "customer":
            return self._customer_principal(claims)
        record = self.get(claims.subject_id)
        if record is None or record.status != "active":
            raise Unauthenticated(code="token_revoked")
        current = self.token_version(claims.subject_id)
        if current is None or current != claims.token_version:
            raise Unauthenticated(code="token_revoked")
        return Principal(
            subject_id=record.id,
            kind=cast("Any", claims.kind),
            display_name=record.display_name,
            mfa_enrolled=record.mfa_enrolled,
            roles=self.roles_of(record.id),
            attributes=record.attributes,
            permissions=self.permissions_of(record.id),
            site_ids=self.site_ids_of(record.id),
            all_sites=record.all_sites,
            customer_id=None,
            session_id=claims.session_id,
            token_version=record.token_version,
            auth_time=claims.auth_time,
            acr=claims.acr,
            locale=cast("Locale", record.locale),
        )

    @staticmethod
    def _customer_principal(claims: TokenClaims) -> Principal:
        """The portal's caller: its own row, and nothing beyond it.

        The lookup goes through the directory the one-time-code login already uses, which
        answers only for an ``active`` customer — a closed account loses its reads with its
        sign-in, and the token's ``sub`` *is* the customer id (there is no user row to check a
        token version against, so closing the account is what revokes its access).
        """
        from hub.identity.infrastructure.customer_otp import CustomerDirectory  # noqa: PLC0415

        record = CustomerDirectory().by_id(claims.subject_id)
        if record is None:
            raise Unauthenticated(code="token_revoked")
        return Principal(
            subject_id=record.id,
            kind="customer",
            customer_id=record.id,
            session_id=claims.session_id,
            auth_time=claims.auth_time,
            acr=claims.acr,
            locale=cast("Locale", record.locale),
        )

    def build_principal(
        self,
        user_id: UUID,
        *,
        session_id: UUID | None,
        auth_time: datetime,
        acr: str,
    ) -> Principal:
        """The caller for an already-proven session (refresh, step-up), without a token to check."""
        record = self.get(user_id)
        if record is None or record.status != "active":
            raise Unauthenticated(code="token_revoked")
        return Principal(
            subject_id=record.id,
            kind="staff",
            display_name=record.display_name,
            mfa_enrolled=record.mfa_enrolled,
            roles=self.roles_of(record.id),
            attributes=record.attributes,
            permissions=self.permissions_of(record.id),
            site_ids=self.site_ids_of(record.id),
            all_sites=record.all_sites,
            session_id=session_id,
            token_version=record.token_version,
            auth_time=auth_time,
            acr=acr,
            locale=cast("Locale", record.locale),
        )

    @staticmethod
    def _version_key(user_id: UUID) -> str:
        return f"tv:{user_id}"


# Column order of a refresh-token row; spelled out so a schema change cannot reorder it.
_SESSION_SELECT = (
    "SELECT id, user_id, customer_id, family_id, issued_at, expires_at, rotated_at, revoked_at "
    "FROM hub.refresh_tokens"
)


def _credential(row: Any) -> WebauthnCredentialRow:
    values = tuple(row)
    transports = values[4] if isinstance(values[4], list) else list(values[4] or ())
    return WebauthnCredentialRow(
        id=cast("UUID", values[0]),
        credential_id=bytes(values[1]),
        public_key=bytes(values[2]),
        sign_count=int(values[3]),
        transports=tuple(str(item) for item in transports),
        name=str(values[5]),
        created_at=cast("datetime", values[6]),
        last_used_at=cast("datetime | None", values[7]),
    )


def _session(row: Any) -> SessionRow:
    values = tuple(row)
    return SessionRow(
        id=cast("UUID", values[0]),
        user_id=cast("UUID | None", values[1]),
        customer_id=cast("UUID | None", values[2]),
        family_id=cast("UUID", values[3]),
        issued_at=cast("datetime", values[4]),
        expires_at=cast("datetime", values[5]),
        rotated_at=cast("datetime | None", values[6]),
        revoked_at=cast("datetime | None", values[7]),
    )


class SessionRepository:
    """``SessionStore`` over ``hub.refresh_tokens``."""

    def insert(
        self,
        row: SessionRow,
        token_hash: bytes,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.refresh_tokens "
                "(id, user_id, customer_id, family_id, token_hash, issued_at, expires_at, ip, "
                "user_agent) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    row.id,
                    row.user_id,
                    row.customer_id,
                    row.family_id,
                    token_hash,
                    row.issued_at,
                    row.expires_at,
                    ip,
                    user_agent,
                ],
            )

    def find_by_hash(self, token_hash: bytes, *, for_update: bool = False) -> SessionRow | None:
        statement = _SESSION_SELECT + " WHERE token_hash = %s"
        if for_update:
            statement += " FOR UPDATE"
        with connection.cursor() as cursor:
            cursor.execute(statement, [token_hash])
            row = cursor.fetchone()
        return _session(row) if row is not None else None

    def family_first_issued_at(self, family_id: UUID) -> datetime | None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT min(issued_at) FROM hub.refresh_tokens WHERE family_id = %s",
                [family_id],
            )
            row = cursor.fetchone()
        return cast("datetime | None", row[0]) if row is not None else None

    def mark_rotated(self, session_id: UUID, *, at: datetime, replaced_by: UUID) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.refresh_tokens SET rotated_at = %s, replaced_by = %s "
                "WHERE id = %s AND rotated_at IS NULL",
                [at, replaced_by, session_id],
            )

    def revoke_family(self, family_id: UUID, *, at: datetime) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.refresh_tokens SET revoked_at = coalesce(revoked_at, %s) "
                "WHERE family_id = %s",
                [at, family_id],
            )
            return int(cursor.rowcount)

    def revoke(self, session_id: UUID, *, at: datetime) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.refresh_tokens SET revoked_at = coalesce(revoked_at, %s) WHERE id = %s",
                [at, session_id],
            )

    def revoke_all_for_user(self, user_id: UUID, *, at: datetime) -> int:
        """Every family this user still holds: what an MFA reset and a suspension need (D-21)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE hub.refresh_tokens SET revoked_at = coalesce(revoked_at, %s) "
                "WHERE user_id = %s AND revoked_at IS NULL",
                [at, user_id],
            )
            return int(cursor.rowcount)
