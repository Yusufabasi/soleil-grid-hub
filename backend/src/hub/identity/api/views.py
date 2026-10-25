"""The DRF operations of the identity module (docs/03 7.7).

Each view is thin on purpose: validate through the contract's serializer, call one use case,
shape one document. The two cookie helpers are shared with ``cookie_views`` so a token pair
looks the same whether it came from a login, an enrolment, a refresh or a step-up.

The public operations (`staffLogin`, `staffMfaVerify`, the enrolment pair) are listed as
``public`` in `api/permissions.yaml`; everything else here is gated by
``PermissionsYamlPermission`` through ``@operation``.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.identity import wiring
from hub.identity.api.cookie_views import (
    _client_ip,
    _user_agent,
    _wants_body_transport,
    set_refresh_cookie,
)
from hub.identity.api.serializers import (
    LoginInput,
    MeUpdateInput,
    MfaEnrollConfirmInput,
    MfaEnrollInput,
    MfaVerifyInput,
    StepUpInput,
    WebAuthnAssertionInput,
    WebAuthnAssertionOptionsInput,
    WebAuthnRegistrationInput,
    principal_document,
    token_pair_document,
)
from hub.identity.application.step_up import StepUpProof
from hub.identity.contracts import Principal
from hub.shared.api.permissions import PermissionsYamlPermission, operation
from hub.shared.errors import Unauthenticated
from hub.shared.security import digest_token

__all__ = [
    "ConfirmMfaEnrollmentView",
    "MeView",
    "RegenerateRecoveryCodesView",
    "StaffLoginView",
    "StaffMfaVerifyView",
    "StartMfaEnrollmentView",
    "StepUpView",
    "VerifyWebauthnAssertionView",
    "WebauthnAssertionOptionsView",
    "WebauthnRegistrationOptionsView",
    "RegisterWebauthnCredentialView",
]


def _principal(request: Request) -> Principal:
    principal = getattr(request, "principal", None)
    if not isinstance(principal, Principal):
        raise Unauthenticated()
    return principal


def _ip(request: Request) -> str | None:
    return _client_ip(request._request)  # noqa: SLF001 - DRF wraps the Django request


def _ua(request: Request) -> str | None:
    return _user_agent(request._request)  # noqa: SLF001


def _token_pair_response(
    request: Request,
    *,
    user_id: Any,
    session_id: Any,
    auth_time: Any,
    acr: str,
    refresh_token: str,
    refresh_expires_in: int,
    status: int = 200,
) -> Response:
    """Issue the access token and put the refresh token where the client asked for it."""
    principal = wiring.users().build_principal(
        user_id, session_id=session_id, auth_time=auth_time, acr=acr
    )
    access = wiring.tokens().issue(principal, session_id=session_id, auth_time=auth_time, acr=acr)
    body_transport = _wants_body_transport(request)
    document = token_pair_document(
        access_token=access,
        expires_in=int(settings.HUB_ACCESS_TTL_SECONDS),
        principal=principal,
        refresh_expires_in=refresh_expires_in,
        refresh_token=refresh_token if body_transport else None,
    )
    response = Response({"data": document}, status=status)
    if not body_transport:
        set_refresh_cookie(response, refresh_token, max_age=refresh_expires_in)
    return response


class StaffLoginView(APIView):
    """`staffLogin`: password only, and never an access token."""

    permission_classes = [PermissionsYamlPermission]
    authentication_classes: list[Any] = []

    @operation("staffLogin")
    def post(self, request: Request) -> Response:
        payload = LoginInput(data=request.data)
        payload.is_valid(raise_exception=True)
        challenge = wiring.local_login().start(
            str(payload.validated_data["email"]),
            str(payload.validated_data["password"]),
            ip=_ip(request),
        )
        return Response(
            {
                "data": {
                    "mfa_required": True,
                    "mfa_token": challenge.mfa_token,
                    "mfa_token_expires_in": challenge.expires_in,
                    "enrollment_required": challenge.enrollment_required,
                    "methods": list(challenge.methods),
                }
            }
        )


class StaffMfaVerifyView(APIView):
    """`staffMfaVerify`: TOTP or a recovery code, and the token pair that follows."""

    permission_classes = [PermissionsYamlPermission]
    authentication_classes: list[Any] = []

    @operation("staffMfaVerify")
    def post(self, request: Request) -> Response:
        payload = MfaVerifyInput(data=request.data)
        payload.is_valid(raise_exception=True)
        authenticated = wiring.mfa().verify(
            str(payload.validated_data["mfa_token"]),
            totp_code=payload.validated_data.get("totp_code"),
            recovery_code=payload.validated_data.get("recovery_code"),
            ip=_ip(request),
            user_agent=_ua(request),
        )
        return _token_pair_response(
            request,
            user_id=authenticated.user_id,
            session_id=authenticated.session_id,
            auth_time=authenticated.auth_time,
            acr=authenticated.acr,
            refresh_token=authenticated.refresh_token,
            refresh_expires_in=authenticated.refresh_expires_in,
        )


class StartMfaEnrollmentView(APIView):
    """`startMfaEnrollment`: the secret and the first recovery codes, shown once."""

    permission_classes = [PermissionsYamlPermission]
    authentication_classes: list[Any] = []

    @operation("startMfaEnrollment")
    def post(self, request: Request) -> Response:
        payload = MfaEnrollInput(data=request.data)
        payload.is_valid(raise_exception=True)
        enrollment = wiring.enrollment().start(
            str(payload.validated_data["mfa_token"]),
            issuer=str(settings.HUB_WEBAUTHN_RP_NAME),
        )
        return Response(
            {
                "data": {
                    "otpauth_uri": enrollment.otpauth_uri,
                    "recovery_codes": list(enrollment.recovery_codes),
                }
            },
            status=201,
        )


class ConfirmMfaEnrollmentView(APIView):
    """`confirmMfaEnrollment`: one code proves the authenticator, then the session starts."""

    permission_classes = [PermissionsYamlPermission]
    authentication_classes: list[Any] = []

    @operation("confirmMfaEnrollment")
    def post(self, request: Request) -> Response:
        payload = MfaEnrollConfirmInput(data=request.data)
        payload.is_valid(raise_exception=True)
        user_id = wiring.enrollment().confirm(
            str(payload.validated_data["mfa_token"]),
            str(payload.validated_data["totp_code"]),
        )
        authenticated = wiring.sessions().issue(
            user_id=user_id,
            customer_id=None,
            auth_time=wiring.clock().now(),
            acr="mfa",
            ip=_ip(request),
            user_agent=_ua(request),
        )
        return _token_pair_response(
            request,
            user_id=user_id,
            session_id=authenticated.session_id,
            auth_time=authenticated.issued_at,
            acr="mfa",
            refresh_token=authenticated.token,
            refresh_expires_in=authenticated.expires_in,
        )


class WebauthnRegistrationOptionsView(APIView):
    """`webauthnRegistrationOptions`: a creation challenge for the signed-in user."""

    permission_classes = [PermissionsYamlPermission]

    @operation("webauthnRegistrationOptions")
    def post(self, request: Request) -> Response:
        principal = _principal(request)
        challenge = wiring.webauthn().registration_options(principal.subject_id)
        return Response(
            {
                "data": {
                    "options": challenge.options,
                    "challenge_id": str(challenge.challenge_id),
                }
            }
        )


class RegisterWebauthnCredentialView(APIView):
    """`registerWebauthnCredential`: verify the attestation and store the public key."""

    permission_classes = [PermissionsYamlPermission]

    @operation("registerWebauthnCredential")
    def post(self, request: Request) -> Response:
        principal = _principal(request)
        payload = WebAuthnRegistrationInput(data=request.data)
        payload.is_valid(raise_exception=True)
        credential = wiring.webauthn().register(
            principal.subject_id,
            payload.validated_data["challenge_id"],
            dict(payload.validated_data["credential"]),
            name=str(payload.validated_data.get("name") or "security key"),
        )
        return Response({"data": credential.document()}, status=201)


class WebauthnAssertionOptionsView(APIView):
    """`webauthnAssertionOptions`: a request challenge for a login, or for a step-up.

    With ``mfa_token`` this is step two of a login and answers for the account the challenge
    names. Without it the caller must already be signed in, and the challenge is issued for a
    step-up: the op has no other way to reach the account, and a signed-in user still needs one
    challenge per assertion.
    """

    permission_classes = [PermissionsYamlPermission]

    @operation("webauthnAssertionOptions")
    def post(self, request: Request) -> Response:
        payload = WebAuthnAssertionOptionsInput(data=request.data)
        payload.is_valid(raise_exception=True)
        mfa_token = str(payload.validated_data.get("mfa_token") or "")
        if mfa_token:
            challenge = wiring.webauthn().assertion_options(mfa_token)
        else:
            challenge = wiring.webauthn().assertion_options_for(_principal(request).subject_id)
        return Response(
            {
                "data": {
                    "options": challenge.options,
                    "challenge_id": str(challenge.challenge_id),
                }
            }
        )


class VerifyWebauthnAssertionView(APIView):
    """`verifyWebauthnAssertion`: a valid assertion opens the session."""

    permission_classes = [PermissionsYamlPermission]
    authentication_classes: list[Any] = []

    @operation("verifyWebauthnAssertion")
    def post(self, request: Request) -> Response:
        payload = WebAuthnAssertionInput(data=request.data)
        payload.is_valid(raise_exception=True)
        user_id = wiring.webauthn().assert_(
            str(payload.validated_data["mfa_token"]),
            payload.validated_data["challenge_id"],
            dict(payload.validated_data["credential"]),
        )
        now = wiring.clock().now()
        issued = wiring.sessions().issue(
            user_id=user_id,
            customer_id=None,
            auth_time=now,
            acr="phr",
            ip=_ip(request),
            user_agent=_ua(request),
        )
        wiring.store().delete(
            f"mfa_challenge:{_challenge_digest(str(payload.validated_data['mfa_token']))}"
        )
        wiring.audit_log().record(
            stream="auth",
            actor=None,
            action="auth.login",
            entity_type="user",
            entity_id=user_id,
            site_id=None,
            details={"method": "webauthn"},
            ip=_ip(request),
        )
        return _token_pair_response(
            request,
            user_id=user_id,
            session_id=issued.session_id,
            auth_time=now,
            acr="phr",
            refresh_token=issued.token,
            refresh_expires_in=issued.expires_in,
        )


def _challenge_digest(mfa_token: str) -> str:
    return digest_token(mfa_token).hex()


class StepUpView(APIView):
    """`stepUp`: prove a second factor again and answer with a token aged to right now.

    The session itself is untouched — same refresh token, same family — but its remembered
    authentication instant moves, so a later refresh carries the step-up rather than silently
    reverting to the login it replaced (D-16).
    """

    permission_classes = [PermissionsYamlPermission]

    @operation("stepUp")
    def post(self, request: Request) -> Response:
        principal = _principal(request)
        payload = StepUpInput(data=request.data)
        payload.is_valid(raise_exception=True)
        proof = StepUpProof(
            method=str(payload.validated_data["method"]),
            totp_code=payload.validated_data.get("totp_code"),
            recovery_code=payload.validated_data.get("recovery_code"),
            assertion=payload.validated_data.get("assertion"),
        )
        result = wiring.step_up().step_up(principal, proof, ip=_ip(request))
        if principal.session_id is None:
            raise Unauthenticated()
        wiring.sessions().remember_authentication(
            principal.session_id, auth_time=result.auth_time, acr=result.acr
        )
        stepped = wiring.users().build_principal(
            principal.subject_id,
            session_id=principal.session_id,
            auth_time=result.auth_time,
            acr=result.acr,
        )
        access = wiring.tokens().issue(
            stepped,
            session_id=principal.session_id,
            auth_time=result.auth_time,
            acr=result.acr,
        )
        return Response(
            {
                "data": token_pair_document(
                    access_token=access,
                    expires_in=int(settings.HUB_ACCESS_TTL_SECONDS),
                    principal=stepped,
                )
            }
        )


class MeView(APIView):
    """`getMe` and `updateMe`: the caller as every other response shows them."""

    permission_classes = [PermissionsYamlPermission]

    @operation("getMe")
    def get(self, request: Request) -> Response:
        return Response({"data": principal_document(_principal(request))})

    @operation("updateMe")
    def patch(self, request: Request) -> Response:
        principal = _principal(request)
        payload = MeUpdateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        display_name = payload.validated_data.get("display_name")
        locale = _supported_locale(payload.validated_data.get("locale"))
        wiring.users().update_profile(
            principal.subject_id, display_name=display_name, locale=locale
        )
        updated = wiring.users().build_principal(
            principal.subject_id,
            session_id=principal.session_id,
            auth_time=principal.auth_time or wiring.clock().now(),
            acr=principal.acr,
        )
        return Response({"data": principal_document(updated)})


def _supported_locale(value: Any) -> str | None:
    """``en`` or ``fr``; an unsupported value is stored as ``en`` rather than refused."""
    if value is None:
        return None
    return "fr" if str(value).lower().startswith("fr") else "en"


class RegenerateRecoveryCodesView(APIView):
    """`regenerateRecoveryCodes`: ten new codes, the old set invalidated."""

    permission_classes = [PermissionsYamlPermission]

    @operation("regenerateRecoveryCodes")
    def post(self, request: Request) -> Response:
        principal = _principal(request)
        codes = wiring.enrollment().regenerate(principal.subject_id)
        return Response({"data": {"recovery_codes": list(codes)}}, status=201)
