"""The three public operations of customer sign-in (D-21, FR-15).

None of them reads a credential: `getProofChallenge`, `requestCustomerOtp` and
`verifyCustomerOtp` are the way in, so all three clear ``authentication_classes`` and let
`api/permissions.yaml` decide the rest (it lists them for ``public``).

`requestCustomerOtp` answers 202 with a challenge whether or not the number exists — the view
cannot even tell, because the service chooses the response shape before it looks anything up.
`verifyCustomerOtp` issues the token pair and puts the refresh token in the cookie the browser
flow uses, or in the body when ``X-Token-Transport: body`` asks for it, exactly like the staff
endpoints (D-17).
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
from hub.identity.api.serializers import OtpRequestInput, OtpVerifyInput, token_pair_document
from hub.shared.api.permissions import PermissionsYamlPermission, operation

__all__ = ["CustomerOtpRequestView", "CustomerOtpVerifyView", "ProofChallengeView"]


class ProofChallengeView(APIView):
    """`getProofChallenge`: a secret and the number of zero bits the client must find."""

    authentication_classes: list[Any] = []
    permission_classes = [PermissionsYamlPermission]

    @operation("getProofChallenge")
    def get(self, request: Request) -> Response:
        challenge = wiring.proof().challenge()
        return Response({"data": challenge.document()})


class CustomerOtpRequestView(APIView):
    """`requestCustomerOtp`: the proof, the prefix and three counters, then always a 202."""

    authentication_classes: list[Any] = []
    permission_classes = [PermissionsYamlPermission]

    @operation("requestCustomerOtp")
    def post(self, request: Request) -> Response:
        body = OtpRequestInput(data=request.data)
        body.is_valid(raise_exception=True)
        challenge = wiring.customer_otp().request(
            phone=str(body.validated_data["phone_e164"]),
            locale=body.validated_data.get("locale"),
            proof_token=str(body.validated_data["proof_token"]),
            ip=_client_ip(request._request),  # noqa: SLF001 - DRF wraps the Django request
        )
        return Response({"data": challenge.document()}, status=202)


class CustomerOtpVerifyView(APIView):
    """`verifyCustomerOtp`: spend the challenge and open a customer session."""

    authentication_classes: list[Any] = []
    permission_classes = [PermissionsYamlPermission]

    @operation("verifyCustomerOtp")
    def post(self, request: Request) -> Response:
        body = OtpVerifyInput(data=request.data)
        body.is_valid(raise_exception=True)
        sign_in = wiring.customer_otp().verify(
            challenge_id=body.validated_data["challenge_id"],
            code=str(body.validated_data["code"]),
            ip=_client_ip(request._request),  # noqa: SLF001
            user_agent=_user_agent(request._request),  # noqa: SLF001
        )
        body_transport = _wants_body_transport(request._request)  # noqa: SLF001
        document = token_pair_document(
            access_token=sign_in.access_token,
            expires_in=int(settings.HUB_ACCESS_TTL_SECONDS),
            principal=sign_in.principal,
            refresh_expires_in=sign_in.refresh_expires_in,
            refresh_token=sign_in.refresh_token if body_transport else None,
        )
        response = Response({"data": document})
        if not body_transport:
            set_refresh_cookie(response, sign_in.refresh_token, max_age=sign_in.refresh_expires_in)
        return response
