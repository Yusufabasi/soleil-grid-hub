"""Unmanaged models over the identity tables (ADR-0020, docs/04 sections 2, 3b and audit).

Every model here is a typed shim over a table that ``db/schema.sql`` creates: ``managed =
False``, no migration, no constraint of its own. The schema owns defaults, checks, triggers
and grants, so these classes carry the columns and nothing else — a constraint written here
would be a second, silent source of truth.

Two shapes need a note:

* ``role_permissions``, ``user_roles``, ``user_sites`` and ``staff_customer_link`` have
  composite primary keys, which Django cannot express; the first column is named as the model's
  key and the repositories always address a row by the whole pair.
* foreign keys that cross a module boundary (``customers``, ``sites``) are plain uuid columns:
  ``identity`` may not import another module's models (docs/03 section 0).

``db_table`` is a bare table name, never ``hub.users``: Django quotes its argument as one
identifier, so a dotted name would address a table literally called "hub.users". The schema
is resolved by the connection's ``search_path``, which ``db/schema.sql`` sets as a role
setting on every application role (``hub_api``, ``hub_worker``, ``ai_gateway``, ...) and which
the test alias has to mirror (D-43).
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

__all__ = [
    "Approval",
    "AuditLogRow",
    "CustomerOtp",
    "MfaRecoveryCode",
    "Permission",
    "RefreshToken",
    "Role",
    "RolePermission",
    "StaffCustomerLink",
    "User",
    "UserRole",
    "UserSite",
    "WebauthnCredential",
]


class UnmanagedShim(models.Model):
    """Base of the shims: no table of its own, and a printable identity for the ORM."""

    class Meta:
        abstract = True
        managed = False

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.pk})"


class User(UnmanagedShim):
    """``hub.users``: staff and customers share one row shape (docs/04 section 2)."""

    id = models.UUIDField(primary_key=True)
    email = models.TextField()
    display_name = models.TextField()
    # Nullable on purpose: the columns are nullable in db/schema.sql, and "" would be a value
    # the schema's checks do not allow to mean "absent".
    password_hash = models.TextField(null=True)  # noqa: DJ001
    idp_subject = models.TextField(null=True)  # noqa: DJ001
    mfa_secret_enc = models.BinaryField(null=True)
    mfa_enrolled_at = models.DateTimeField(null=True)
    status = models.TextField(default="invited")
    preferred_language = models.TextField(default="en")
    all_sites = models.BooleanField(default=False)
    finance_lead = models.BooleanField(default=False)
    dp_admin = models.BooleanField(default=False)
    token_version = models.IntegerField(default=1)
    last_login_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True)
    version = models.IntegerField(default=1)

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "users"


class Role(UnmanagedShim):
    code = models.TextField(primary_key=True)
    description = models.TextField()

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "roles"


class Permission(UnmanagedShim):
    code = models.TextField(primary_key=True)
    description = models.TextField()

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "permissions"


class RolePermission(UnmanagedShim):
    role_code = models.TextField(primary_key=True)
    permission_code = models.TextField()

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "role_permissions"


class UserRole(UnmanagedShim):
    user_id = models.UUIDField(primary_key=True)
    role_code = models.TextField()
    granted_by = models.UUIDField(null=True)
    granted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "user_roles"


class UserSite(UnmanagedShim):
    user_id = models.UUIDField(primary_key=True)
    site_id = models.UUIDField()

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "user_sites"


class RefreshToken(UnmanagedShim):
    """``hub.refresh_tokens``: one row per issued token, a family per login (D-17)."""

    id = models.UUIDField(primary_key=True)
    user_id = models.UUIDField(null=True)
    customer_id = models.UUIDField(null=True)
    family_id = models.UUIDField()
    token_hash = models.BinaryField()
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    rotated_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    replaced_by = models.UUIDField(null=True)
    ip = models.GenericIPAddressField(null=True)
    user_agent = models.TextField(null=True)  # noqa: DJ001 - nullable in db/schema.sql

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "refresh_tokens"


class CustomerOtp(UnmanagedShim):
    """``hub.customer_otp``: a challenge, never the code itself (D-21)."""

    id = models.UUIDField(primary_key=True)
    phone_hmac = models.BinaryField()
    code_hash = models.BinaryField()
    purpose = models.TextField(default="login")
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=5)
    request_ip = models.GenericIPAddressField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "customer_otp"


class MfaRecoveryCode(UnmanagedShim):
    id = models.UUIDField(primary_key=True)
    user_id = models.UUIDField()
    code_hash = models.BinaryField()
    used_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "mfa_recovery_codes"


class WebauthnCredential(UnmanagedShim):
    id = models.UUIDField(primary_key=True)
    user_id = models.UUIDField()
    credential_id = models.BinaryField()
    public_key = models.BinaryField()
    sign_count = models.BigIntegerField(default=0)
    transports = ArrayField(models.TextField(), default=list)
    name = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "webauthn_credentials"


class Approval(UnmanagedShim):
    """``hub.approvals``: the maker-checker record (D-15); ``payload_hash`` is set by a trigger."""

    id = models.UUIDField(primary_key=True)
    action = models.TextField()
    subject_customer_id = models.UUIDField(null=True)
    maker_user_id = models.UUIDField()
    maker_session_id = models.UUIDField()
    payload = models.JSONField()
    payload_hash = models.CharField(max_length=64)
    status = models.TextField(default="pending")
    approver_user_id = models.UUIDField(null=True)
    approver_session_id = models.UUIDField(null=True)
    decided_at = models.DateTimeField(null=True)
    consumed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "approvals"


class StaffCustomerLink(UnmanagedShim):
    """``hub.staff_customer_link``: the conflict-of-interest table an approval checks (D-15)."""

    user_id = models.UUIDField(primary_key=True)
    customer_id = models.UUIDField()
    relation = models.TextField(default="other")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "staff_customer_link"


class AuditLogRow(UnmanagedShim):
    """``hub.audit_log``: append-only, written through ``hub.audit_append`` only."""

    id = models.UUIDField(primary_key=True)
    stream = models.TextField()
    seq = models.BigIntegerField()
    prev_hash = models.BinaryField(null=True)
    hash = models.BinaryField()
    occurred_at = models.DateTimeField()
    actor_type = models.TextField()
    actor_id = models.UUIDField(null=True)
    action = models.TextField()
    entity_type = models.TextField()
    entity_id = models.UUIDField(null=True)
    site_id = models.UUIDField(null=True)
    request_id = models.TextField(null=True)  # noqa: DJ001 - nullable in db/schema.sql
    details = models.JSONField(default=dict)

    class Meta:
        managed = False
        app_label = "identity"
        db_table = "audit_log"
