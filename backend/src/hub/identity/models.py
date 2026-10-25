"""Django discovers an app's models in ``<app>.models``; the models themselves live in
``infrastructure`` (docs/03 section 1: the ORM is an adapter, never the domain).
"""

from hub.identity.infrastructure.models import (
    Approval,
    AuditLogRow,
    CustomerOtp,
    MfaRecoveryCode,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    StaffCustomerLink,
    User,
    UserRole,
    UserSite,
    WebauthnCredential,
)

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
