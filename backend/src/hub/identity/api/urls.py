"""Routes of the identity module. The URL name is the ``operationId`` (docs/03 7.7).

Only operations that ``api/openapi.yaml`` declares are mounted here (executor guide rule 4):
the two cookie endpoints of D-17, the break-glass login chain, and `/me` from Task 01.8 on.
"""

from typing import Any

from django.urls import path

from hub.identity.api.cookie_views import logout_view, refresh_view
from hub.identity.api.views import (
    ConfirmMfaEnrollmentView,
    RegenerateRecoveryCodesView,
    StaffLoginView,
    StaffMfaVerifyView,
    StartMfaEnrollmentView,
)

__all__ = ["urlpatterns"]

urlpatterns: list[Any] = [
    path("auth/staff/login", StaffLoginView.as_view(), name="staffLogin"),
    path("auth/staff/mfa/verify", StaffMfaVerifyView.as_view(), name="staffMfaVerify"),
    path(
        "auth/staff/mfa/enrollments",
        StartMfaEnrollmentView.as_view(),
        name="startMfaEnrollment",
    ),
    path(
        "auth/staff/mfa/enrollments/confirm",
        ConfirmMfaEnrollmentView.as_view(),
        name="confirmMfaEnrollment",
    ),
    path(
        "auth/staff/mfa/recovery-codes",
        RegenerateRecoveryCodesView.as_view(),
        name="regenerateRecoveryCodes",
    ),
    path("auth/refresh", refresh_view, name="refreshToken"),
    path("auth/logout", logout_view, name="logout"),
]
