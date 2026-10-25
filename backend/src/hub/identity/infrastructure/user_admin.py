"""Rows behind user administration: ``hub.users``, its roles and its sites (FR-15, D-13).

The list is one statement with two correlated subqueries rather than three queries per user:
an administrator's screen is a page of twenty, and the alternative is sixty round trips.

Writes here are the *executor's* writes, so they run inside the approval's transaction (D-15):
a failed execution must leave the approved action undone, which it cannot if the adapter opens
its own connection or commits on its own.

The DTOs come from ``application.user_admin``: the application layer owns the shape it reasons
about, this module only turns rows into it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import connection

from hub.identity.application.user_admin import RoleRow, UserRow

__all__ = ["UserAdminRepository"]

_USER_COLUMNS = (
    "u.id, u.email, u.display_name, u.status, u.preferred_language, u.finance_lead, "
    "u.dp_admin, u.mfa_secret_enc, u.mfa_enrolled_at, u.last_login_at, u.created_at"
)
_AGGREGATES = (
    "coalesce((SELECT array_agg(ur.role_code ORDER BY ur.role_code) FROM hub.user_roles ur "
    "WHERE ur.user_id = u.id), '{}') AS roles, "
    "coalesce((SELECT array_agg(us.site_id ORDER BY us.site_id) FROM hub.user_sites us "
    "WHERE us.user_id = u.id), '{}') AS site_ids, "
    "EXISTS (SELECT 1 FROM hub.webauthn_credentials w WHERE w.user_id = u.id) AS webauthn_enrolled"
)
_SELECT = f"SELECT {_USER_COLUMNS}, {_AGGREGATES} FROM hub.users u"  # noqa: S608 - literals only


def _row(values: Sequence[Any]) -> UserRow:
    (
        user_id,
        email,
        display_name,
        status,
        language,
        finance_lead,
        dp_admin,
        mfa_secret_enc,
        mfa_enrolled_at,
        last_login_at,
        created_at,
        roles,
        site_ids,
        webauthn_enrolled,
    ) = values
    return UserRow(
        id=user_id,
        email=str(email),
        display_name=str(display_name),
        status=str(status),
        preferred_language=str(language),
        finance_lead=bool(finance_lead),
        dp_admin=bool(dp_admin),
        mfa_enrolled=mfa_secret_enc is not None or mfa_enrolled_at is not None,
        webauthn_enrolled=bool(webauthn_enrolled),
        last_login_at=last_login_at,
        created_at=created_at,
        roles=tuple(str(role) for role in (roles or ())),
        site_ids=tuple(site_ids or ()),
    )


class UserAdminRepository:
    """Everything ``/users`` and ``/roles`` read, and the writes their approvals execute."""

    # --- reads -------------------------------------------------------------------------
    def get(self, user_id: UUID) -> UserRow | None:
        with connection.cursor() as cursor:
            cursor.execute(_SELECT + " WHERE u.id = %s AND u.deleted_at IS NULL", [user_id])
            row = cursor.fetchone()
        return _row(row) if row is not None else None

    def page(
        self,
        *,
        statuses: Sequence[str],
        role: str | None,
        query: str | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[UserRow]:
        """One page, newest first; ``(created_at, id)`` keeps the keyset stable."""
        conditions = ["u.deleted_at IS NULL"]
        params: list[Any] = []
        if statuses:
            conditions.append("u.status = ANY(%s)")
            params.append(list(statuses))
        if role:
            conditions.append(
                "EXISTS (SELECT 1 FROM hub.user_roles ur WHERE ur.user_id = u.id AND ur.role_code = %s)"
            )
            params.append(role)
        if query:
            conditions.append("(u.email ILIKE %s OR u.display_name ILIKE %s)")
            params.extend([f"%{query}%", f"%{query}%"])
        if after is not None:
            conditions.append("(u.created_at, u.id) < (%s, %s)")
            params.extend([after[0], after[1]])
        params.append(limit)
        # The conditions are fixed strings; every value is a bound parameter.
        sql = (
            f"{_SELECT} WHERE {' AND '.join(conditions)} "  # noqa: S608 - literals only
            "ORDER BY u.created_at DESC, u.id DESC LIMIT %s"
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [_row(row) for row in rows]

    def email_taken(self, email: str) -> bool:
        """Soft-deleted accounts keep their address reserved (``uq_users_email``)."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM hub.users WHERE email = lower(%s)", [email.strip()])
            return cursor.fetchone() is not None

    def known_sites(self, site_ids: Sequence[UUID]) -> frozenset[UUID]:
        """The subset of these site ids that exists, so a bad one is a 404 and not a 500."""
        if not site_ids:
            return frozenset()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM hub.sites WHERE id = ANY(%s)", [list(site_ids)])
            return frozenset(value for (value,) in cursor.fetchall())

    def administrators(self) -> list[UUID]:
        """Who holds the administrator role now (the last one cannot be demoted)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT ur.user_id FROM hub.user_roles ur JOIN hub.users u ON u.id = ur.user_id "
                "WHERE ur.role_code = 'administrator' AND u.deleted_at IS NULL "
                "AND u.status <> 'suspended'"
            )
            return [value for (value,) in cursor.fetchall()]

    def roles(self) -> list[RoleRow]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT r.code, r.description, "
                "coalesce((SELECT array_agg(rp.permission_code ORDER BY rp.permission_code) "
                "FROM hub.role_permissions rp WHERE rp.role_code = r.code), '{}') "
                "FROM hub.roles r ORDER BY r.code"
            )
            return [RoleRow(str(code), str(text), tuple(perms or ())) for code, text, perms in cursor]

    # --- writes the executors run ------------------------------------------------------
    def insert(
        self,
        *,
        user_id: UUID,
        email: str,
        display_name: str,
        status: str,
        locale: str,
        roles: Sequence[str],
        attributes: Sequence[str],
        site_ids: Sequence[UUID],
        at: datetime,
    ) -> None:
        """Create the account, its roles and its sites; an invited user has no credential."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hub.users (id, email, display_name, status, preferred_language, "
                "finance_lead, dp_admin, created_at, updated_at) "
                "VALUES (%s, lower(%s), %s, %s, %s, %s, %s, %s, %s)",
                [
                    user_id,
                    email.strip(),
                    display_name,
                    status,
                    locale,
                    "finance_lead" in attributes,
                    "dp_admin" in attributes,
                    at,
                    at,
                ],
            )
        self.replace_roles(user_id, roles=roles, attributes=attributes, granted_by=None, at=at)
        self.replace_sites(user_id, site_ids=site_ids)

    def replace_roles(
        self,
        user_id: UUID,
        *,
        roles: Sequence[str],
        attributes: Sequence[str],
        granted_by: UUID | None,
        at: datetime,
    ) -> None:
        """Roles and the two attributes are one authority change, so they change together."""
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM hub.user_roles WHERE user_id = %s", [user_id])
            cursor.executemany(
                "INSERT INTO hub.user_roles (user_id, role_code, granted_by, granted_at) "
                "VALUES (%s, %s, %s, %s)",
                [(user_id, role, granted_by, at) for role in roles],
            )
            cursor.execute(
                "UPDATE hub.users SET finance_lead = %s, dp_admin = %s, updated_at = %s "
                "WHERE id = %s",
                ["finance_lead" in attributes, "dp_admin" in attributes, at, user_id],
            )

    def replace_sites(self, user_id: UUID, *, site_ids: Sequence[UUID]) -> None:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM hub.user_sites WHERE user_id = %s", [user_id])
            cursor.executemany(
                "INSERT INTO hub.user_sites (user_id, site_id) VALUES (%s, %s)",
                [(user_id, site_id) for site_id in site_ids],
            )

    def update_profile(
        self,
        user_id: UUID,
        *,
        display_name: str | None,
        status: str | None,
        locale: str | None,
        at: datetime,
    ) -> None:
        """The three fields ``updateUser`` may change without an approval."""
        assignments: list[str] = []
        params: list[Any] = []
        if display_name is not None:
            assignments.append("display_name = %s")
            params.append(display_name)
        if status is not None:
            assignments.append("status = %s")
            params.append(status)
        if locale is not None:
            assignments.append("preferred_language = %s")
            params.append(locale)
        if not assignments:
            return
        assignments.append("updated_at = %s")
        params.extend([at, user_id])
        with connection.cursor() as cursor:
            # The assignments are this module's own literals; every value is a parameter.
            cursor.execute(
                f"UPDATE hub.users SET {', '.join(assignments)} WHERE id = %s",  # noqa: S608
                params,
            )
