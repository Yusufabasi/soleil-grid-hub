"""Dev-only token mint, mounted nowhere outside ``HUB_ENV`` in {local, test} (see ``urls.py``).

Why this exists at all: the customer OTP flow is real, tested and correctly refuses to leak the
code anywhere — ``customer_otp`` stores a ``code_hash``, logs the delivery without the code, and
there is no SMS gateway in this environment. That is the right security posture and a dead end for
a live demo, so the demo signs a customer in the same way this project's own end-to-end suite does
(``backend/tests/e2e/test_topup_with_stub.py``, the ``MINT_SCRIPT`` it runs through
``docker compose exec``): by minting a token directly from the same signing authority the real
login uses. This view is that hop, reachable over HTTP instead of a ``docker exec``.

Two things it deliberately does not do:

* **no refresh cookie and no session row.** It returns one access token, so a dev session lasts
  the access token's ten minutes and then the operator signs in again. Minting a real session would
  mean writing ``refresh_tokens`` rows, which is a larger surface than a demo route deserves.
* **no path to the real code.** Nothing here reads an OTP out of the database or out of a log.

The route is mounted only where ``HUB_ENV`` is ``local`` or ``test``: in every other environment
the path does not exist in the URLconf at all, which is a stronger guarantee than a 403.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.identity import wiring
from hub.identity.api.serializers import token_pair_document
from hub.identity.domain.principal import MFA_ACR, Principal
from hub.shared.errors import ValidationFailed

__all__ = ["DevMintTokenView"]


class DevMintTokenView(APIView):
    """``POST /api/v1/dev/mint-token``: an access token for a seeded staff or customer account.

    Body: ``{"kind": "staff" | "customer", "subject": "<email>" | "<customer uuid>"}``. The answer
    is the same ``TokenPair`` document the real login endpoints answer with, minus the refresh
    token, so the client has exactly one way to apply a signed-in session.
    """

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    def post(self, request: Request) -> Response:
        body: dict[str, Any] = request.data if isinstance(request.data, dict) else {}
        kind = str(body.get("kind") or "").strip()
        subject = str(body.get("subject") or "").strip()
        now = wiring.clock().now()
        session = wiring.ids().new_id()
        if kind == "staff":
            principal, amr = self._staff_principal(subject, session=session, now=now)
        elif kind == "customer":
            principal, amr = self._customer_principal(subject, session=session, now=now)
        else:
            raise ValidationFailed(
                code="dev_mint_kind_invalid",
                details={
                    "errors": [
                        {
                            "field": "/kind",
                            "code": "invalid_choice",
                            "message": "kind must be 'staff' or 'customer'.",
                        }
                    ]
                },
            )
        token = wiring.tokens().issue(
            principal,
            session_id=session,
            auth_time=now,
            acr=principal.acr,
            amr=amr,
        )
        return Response(
            {
                "data": token_pair_document(
                    access_token=token,
                    expires_in=int(settings.HUB_ACCESS_TTL_SECONDS),
                    principal=principal,
                )
            }
        )

    def _staff_principal(
        self, email: str, *, session: UUID, now: datetime
    ) -> tuple[Principal, tuple[str, ...]]:
        """The principal of an active staff account, read from the same repository login uses."""
        record = wiring.users().find_by_email(email) if email else None
        if record is None or record.status != "active":
            # One answer for an unknown address and a disabled account: this is a demo tool and
            # telling them apart would leak which seeded accounts exist.
            raise ValidationFailed(
                code="dev_mint_no_active_staff",
                details={
                    "errors": [
                        {
                            "field": "/subject",
                            "code": "not_found",
                            "message": "No active staff account with that address.",
                        }
                    ]
                },
            )
        principal = wiring.users().build_principal(
            record.id, session_id=session, auth_time=now, acr=MFA_ACR
        )
        return principal, ("pwd", "totp")

    def _customer_principal(
        self, subject: str, *, session: UUID, now: datetime
    ) -> tuple[Principal, tuple[str, ...]]:
        """The principal of one customer, exactly as ``MINT_SCRIPT``'s ``customer_token`` builds it."""
        try:
            customer_id = UUID(subject)
        except (ValueError, AttributeError) as error:
            raise ValidationFailed(
                code="dev_mint_subject_invalid",
                details={
                    "errors": [
                        {
                            "field": "/subject",
                            "code": "invalid_uuid",
                            "message": "subject must be the customer's uuid.",
                        }
                    ]
                },
            ) from error
        principal = Principal(
            subject_id=customer_id,
            kind="customer",
            customer_id=customer_id,
            display_name="Demo customer",
            session_id=session,
            auth_time=now,
            acr="pwd",
            locale="fr",
        )
        return principal, ("otp",)
