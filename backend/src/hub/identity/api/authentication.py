"""`Authorization: Bearer <EdDSA JWT>` is the only way in (D-17, docs/03 2.2).

The class does three things and refuses to do a fourth:

1. parse the header (no header is "no credential", not a failure — three operations are
   public and DRF must be able to fall through to them);
2. verify the token through ``AccessTokenService`` — signature, issuer, audience, life;
3. load the caller from the database — roles, attributes, permissions, sites — and refuse a
   stale ``tv`` or a suspended account.

It never reads a role from the token, because the token does not carry one.
"""

from __future__ import annotations

from typing import Any

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.request import Request

from hub.identity import wiring
from hub.shared.context import bind_context
from hub.shared.errors import Unauthenticated

__all__ = ["BearerJwtAuthentication"]


class BearerJwtAuthentication(BaseAuthentication):
    """Resolve the bearer token to a ``Principal`` and attach it to the request."""

    def authenticate(self, request: Request) -> tuple[Any, None] | None:
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if parts[0].lower() != b"bearer":
            # Another scheme's credential: this class is not the one to judge it.
            return None
        if len(parts) != 2:
            raise Unauthenticated()
        try:
            token = parts[1].decode("ascii")
        except UnicodeDecodeError as exc:
            raise Unauthenticated() from exc

        claims = wiring.tokens().verify(token)
        # Bound before `load_principal`, not after: a customer's principal is read from
        # `hub.customers`, which is forced-RLS, so the read itself has to announce the customer
        # it is reading. A machine principal announces nothing — no policy grants it a row.
        bind_context(
            db_user_id=str(claims.subject_id) if claims.kind == "staff" else None,
            db_customer_id=str(claims.subject_id) if claims.kind == "customer" else None,
        )
        principal = wiring.users().load_principal(claims)

        # The logging processors downstream read this (docs/03 7.3); it is set only after the
        # identity is proven, so a rejected request logs no principal.
        bind_context(principal_id=str(principal.subject_id))
        setattr(request, "principal", principal)  # noqa: B010 - Django's request has no field
        setattr(request, "token_claims", claims)  # noqa: B010
        return principal, None

    def authenticate_header(self, request: Request) -> str:
        """A string makes DRF answer 401 with a challenge, not 403 (RFC 6750)."""
        return 'Bearer realm="hub-api"'
