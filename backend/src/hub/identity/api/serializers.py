"""Request validators and response documents of the identity operations (docs/03 7.7).

Two directions, two jobs:

* **In** — DRF serializers validate every body against ``api/openapi.yaml`` before a use case
  sees it: lengths, patterns and choices are the contract's, so an invalid request is a 422
  with a JSON-pointer per field and never reaches the database.
* **Out** — plain functions build the response documents, because a ``Principal`` is a frozen
  dataclass, not a model: ``principal_document`` is the single mapping used by login, refresh,
  step-up and `/me`, so those four can never disagree about the shape of a caller.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from hub.identity.contracts import Principal

__all__ = [
    "MfaVerifyInput",
    "OtpRequestInput",
    "OtpVerifyInput",
    "LoginInput",
    "MeUpdateInput",
    "RefreshInput",
    "WebAuthnAssertionInput",
    "WebAuthnAssertionOptionsInput",
    "WebAuthnRegistrationInput",
    "principal_document",
    "token_pair_document",
]

_LOCALES = ("en", "fr")


class LoginInput(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=12, max_length=256, trim_whitespace=False)


class MfaVerifyInput(serializers.Serializer[dict[str, Any]]):
    mfa_token = serializers.CharField(max_length=128)
    totp_code = serializers.RegexField(r"^[0-9]{6}$", required=False)
    recovery_code = serializers.CharField(max_length=64, required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("totp_code") and not attrs.get("recovery_code"):
            raise serializers.ValidationError(
                {"totp_code": ["Provide totp_code or recovery_code."]}
            )
        return attrs


class MfaEnrollInput(serializers.Serializer[dict[str, Any]]):
    mfa_token = serializers.CharField(max_length=128)


class MfaEnrollConfirmInput(serializers.Serializer[dict[str, Any]]):
    mfa_token = serializers.CharField(max_length=128)
    totp_code = serializers.RegexField(r"^[0-9]{6}$")


class StepUpInput(serializers.Serializer[dict[str, Any]]):
    totp_code = serializers.RegexField(r"^[0-9]{6}$", required=False)
    recovery_code = serializers.CharField(max_length=64, required=False)
    webauthn_assertion = serializers.JSONField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not any(attrs.get(key) for key in ("totp_code", "recovery_code", "webauthn_assertion")):
            raise serializers.ValidationError(
                {"totp_code": ["Provide a TOTP code, a recovery code or a WebAuthn assertion."]}
            )
        return attrs


class RefreshInput(serializers.Serializer[dict[str, Any]]):
    refresh_token = serializers.CharField(max_length=256, required=False, allow_blank=False)


class LogoutInput(serializers.Serializer[dict[str, Any]]):
    refresh_token = serializers.CharField(max_length=256, required=False, allow_blank=False)


class OtpRequestInput(serializers.Serializer[dict[str, Any]]):
    phone_e164 = serializers.RegexField(r"^\+[1-9][0-9]{7,14}$")
    locale = serializers.ChoiceField(choices=_LOCALES, required=False)
    proof_token = serializers.CharField(max_length=256)


class OtpVerifyInput(serializers.Serializer[dict[str, Any]]):
    challenge_id = serializers.UUIDField()
    code = serializers.RegexField(r"^[0-9]{6}$")


class WebAuthnAssertionOptionsInput(serializers.Serializer[dict[str, Any]]):
    mfa_token = serializers.CharField(max_length=128)


class WebAuthnRegistrationInput(serializers.Serializer[dict[str, Any]]):
    challenge_id = serializers.UUIDField()
    credential = serializers.DictField()
    name = serializers.CharField(max_length=64, required=False, allow_blank=False)


class WebAuthnAssertionInput(serializers.Serializer[dict[str, Any]]):
    mfa_token = serializers.CharField(max_length=128)
    challenge_id = serializers.UUIDField()
    credential = serializers.DictField()


class MeUpdateInput(serializers.Serializer[dict[str, Any]]):
    display_name = serializers.CharField(max_length=120, required=False, allow_blank=False)
    locale = serializers.CharField(max_length=8, required=False, allow_blank=False)


class UserCreateInput(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=120)
    roles = serializers.ListField(
        child=serializers.ChoiceField(
            choices=("support_agent", "finance_officer", "operations_engineer", "administrator")
        ),
        min_length=1,
        allow_empty=False,
    )
    attributes = serializers.ListField(
        child=serializers.ChoiceField(choices=("finance_lead", "dp_admin")), required=False
    )
    site_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    locale = serializers.ChoiceField(choices=_LOCALES, required=False)
    reason = serializers.CharField(max_length=1000)


class RolesUpdateInput(serializers.Serializer[dict[str, Any]]):
    roles = serializers.ListField(
        child=serializers.ChoiceField(
            choices=("support_agent", "finance_officer", "operations_engineer", "administrator")
        ),
        min_length=1,
        allow_empty=False,
    )
    attributes = serializers.ListField(
        child=serializers.ChoiceField(choices=("finance_lead", "dp_admin")), required=False
    )
    reason = serializers.CharField(max_length=1000)


class UserSitesInput(serializers.Serializer[dict[str, Any]]):
    site_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=True)
    reason = serializers.CharField(max_length=1000)


class UserUpdateInput(serializers.Serializer[dict[str, Any]]):
    full_name = serializers.CharField(max_length=120, required=False, allow_blank=False)
    status = serializers.ChoiceField(choices=("active", "suspended"), required=False)
    locale = serializers.ChoiceField(choices=_LOCALES, required=False)


class MfaResetInput(serializers.Serializer[dict[str, Any]]):
    reason = serializers.CharField(max_length=1000)


def principal_document(principal: Principal) -> dict[str, Any]:
    """``api/openapi.yaml`` Principal: the caller, as every authenticated response shows them."""
    document: dict[str, Any] = {
        "id": str(principal.subject_id),
        "type": principal.kind,
        "display_name": principal.display_name,
        "roles": sorted(principal.roles),
        "attributes": sorted(principal.attributes),
        "scopes": sorted(principal.scopes),
        "site_ids": [str(site_id) for site_id in sorted(principal.site_ids)],
        "locale": principal.locale,
        "mfa_enrolled": principal.mfa_enrolled,
        "acr": principal.acr,
    }
    if principal.auth_time is not None:
        document["auth_time"] = principal.auth_time.isoformat().replace("+00:00", "Z")
    return document


def token_pair_document(
    *,
    access_token: str,
    expires_in: int,
    principal: Principal,
    refresh_expires_in: int | None = None,
    refresh_token: str | None = None,
) -> dict[str, Any]:
    """``TokenPair``: the access token, and the refresh token only for non-browser clients."""
    document: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "principal": principal_document(principal),
    }
    if refresh_expires_in is not None:
        document["refresh_expires_in"] = refresh_expires_in
    if refresh_token is not None:
        document["refresh_token"] = refresh_token
    return document
