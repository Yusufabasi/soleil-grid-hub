"""Routes of the identity module. The URL name is the ``operationId`` (docs/03 7.7).

Only operations that ``api/openapi.yaml`` declares are mounted here (executor guide rule 4):
the two cookie endpoints of D-17, the break-glass login chain, and `/me` from Task 01.8 on.
"""

from typing import Any

from django.conf import settings
from django.urls import path

from hub.identity.api.cookie_views import logout_view, refresh_view
from hub.identity.api.oidc_views import oidc_callback_view, oidc_start_view
from hub.identity.api.views import (
    ConfirmMfaEnrollmentView,
    RegenerateRecoveryCodesView,
    RegisterWebauthnCredentialView,
    StaffLoginView,
    StaffMfaVerifyView,
    StartMfaEnrollmentView,
    VerifyWebauthnAssertionView,
    WebauthnAssertionOptionsView,
    WebauthnRegistrationOptionsView,
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
    path(
        "auth/webauthn/registration-options",
        WebauthnRegistrationOptionsView.as_view(),
        name="webauthnRegistrationOptions",
    ),
    path(
        "auth/webauthn/registrations",
        RegisterWebauthnCredentialView.as_view(),
        name="registerWebauthnCredential",
    ),
    path(
        "auth/webauthn/assertion-options",
        WebauthnAssertionOptionsView.as_view(),
        name="webauthnAssertionOptions",
    ),
    path(
        "auth/webauthn/assertions",
        VerifyWebauthnAssertionView.as_view(),
        name="verifyWebauthnAssertion",
    ),
    path("auth/refresh", refresh_view, name="refreshToken"),
    path("auth/logout", logout_view, name="logout"),
]

# The identity provider is not configured in every environment (Plan notes 1): the code ships,
# the routes do not, until an issuer and a client exist for it.
if settings.HUB_OIDC_ROUTES_ENABLED:
    urlpatterns += [
        path("auth/oidc/start", oidc_start_view, name="oidcStart"),
        path("auth/oidc/callback", oidc_callback_view, name="oidcCallback"),
    ]
