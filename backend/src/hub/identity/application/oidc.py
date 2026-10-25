"""The company identity provider: hub-api is the confidential OIDC client (D-47).

hub-web only sends the browser here; the client secret and the provider tokens never leave the
server. The flow is the authorization code with PKCE:

* ``authorization_url`` mints a fresh ``state``, ``code_verifier`` and ``nonce``, stores them
  in Redis under the browser's binding cookie, and returns the provider URL. The cookie is the
  only thing that ties the callback to the browser that started the flow, so a code stolen from
  a URL is useless in another browser.
* ``complete`` pops that entry — single use, like every challenge in this module — checks the
  returned ``state`` against it, exchanges the code (the secret is sent to the token endpoint
  only), verifies the ID token against the provider's JWKS, and refuses unless the provider
  says the second factor happened.

Validation is the point of the module, so it is explicit: signature, ``iss``, ``aud``,
``exp``, ``nonce`` and ``amr``. An identity that maps to no staff user is not provisioned on
demand — the administrator creates the account and stores its subject first (docs/05).

The metadata and the JWKS are cached for the life of the process and refetched once when a
token fails to verify, which covers a provider key rotation without a restart. A provider
outage is a 503, not a 401: the user did nothing wrong.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import jwt
import requests

# Authlib ships neither types nor stubs; the client is used through the small surface below,
# which this module's own tests exercise end to end.
from authlib.integrations.requests_client import OAuth2Session  # type: ignore[import-untyped]
from jwt import InvalidTokenError, PyJWKSet
from requests import RequestException

from hub.identity.application.sessions import SessionService
from hub.identity.contracts import AuditLog
from hub.shared.contracts import KeyValueStore
from hub.shared.errors import (
    ExternalUnavailable,
    InvalidRequest,
    PermissionDenied,
    Unauthenticated,
)
from hub.shared.ports import Clock

__all__ = ["OidcLogin", "OidcSession"]

STATE_TTL_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 5
SUPPORTED_ALGORITHMS = ["ES256", "RS256"]


@dataclass(frozen=True, slots=True)
class OidcSession:
    """What a completed callback hands back: the session and where the browser should land.

    ``return_to`` comes from the Redis entry, not from the callback query: the provider only
    echoes ``code`` and ``state``, and a redirect target read from the request would be an
    open redirect.
    """

    refresh_token: str
    expires_in: int
    session_id: UUID
    auth_time: datetime
    acr: str
    return_to: str


class OidcLogin:
    """Start the authorization-code flow and complete it with a verified ID token."""

    def __init__(
        self,
        *,
        clock: Clock,
        store: KeyValueStore,
        users: Any,
        sessions: SessionService,
        audit: AuditLog,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        self._clock = clock
        self._store = store
        self._users = users
        self._sessions = sessions
        self._audit = audit
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._discovery: dict[str, Any] | None = None
        self._key_set_cache: PyJWKSet | None = None

    # --- step one: the redirect ---------------------------------------------------------
    def authorization_url(
        self, browser_binding: str | None = None, *, return_to: str | None = None
    ) -> tuple[str, str]:
        """Return ``(authorization_url, state_cookie)`` for this browser.

        A binding cookie that arrived is reused, so two starts in one browser replace each
        other instead of leaving a second entry behind; its random value is what the Redis key
        and the cookie share.
        """
        binding = browser_binding or secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        nonce = secrets.token_urlsafe(32)

        client = self._client()
        url, _ = client.create_authorization_url(
            self._endpoint("authorization_endpoint"),
            state=state,
            code_verifier=verifier,
            nonce=nonce,
        )
        self._store.set(
            _state_key(binding),
            json.dumps(
                {"state": state, "verifier": verifier, "nonce": nonce, "return_to": return_to}
            ),
            ttl_seconds=STATE_TTL_SECONDS,
        )
        return url, binding

    # --- step two: the callback ---------------------------------------------------------
    def complete(
        self,
        code: str,
        state: str,
        browser_binding: str | None,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> OidcSession:
        """Exchange the code and open a staff session for the identity it proves."""
        pending = self._consume(browser_binding)
        if not secrets.compare_digest(str(pending.get("state") or ""), state or ""):
            # The state belongs to another browser, or was replayed: both are refusals.
            raise InvalidRequest(code="invalid_request")

        token = self._exchange(code, str(pending["verifier"]))
        claims = self._claims(token, str(pending["nonce"]))

        subject = str(claims.get("sub") or "")
        record = self._users.find_by_idp_subject(subject)
        if record is None:
            self._audit.record(
                stream="auth",
                actor=None,
                action="auth.login",
                entity_type="user",
                entity_id=None,
                site_id=None,
                details={"method": "oidc", "reason": "unknown_subject"},
                outcome="denied",
                ip=ip,
            )
            raise PermissionDenied(code="account_not_provisioned")
        if record.status != "active":
            self._audit.record(
                stream="auth",
                actor=None,
                action="auth.login",
                entity_type="user",
                entity_id=record.id,
                site_id=None,
                details={"method": "oidc", "reason": "inactive"},
                outcome="denied",
                ip=ip,
            )
            raise Unauthenticated(code="account_inactive")

        now = self._clock.now()
        issued = self._sessions.issue(
            user_id=record.id,
            customer_id=None,
            auth_time=now,
            acr="mfa",
            ip=ip,
            user_agent=user_agent,
        )
        self._users.mark_login(record.id, now)
        self._audit.record(
            stream="auth",
            actor=None,
            action="auth.login",
            entity_type="user",
            entity_id=record.id,
            site_id=None,
            details={"method": "oidc"},
            ip=ip,
        )
        return OidcSession(
            refresh_token=issued.token,
            expires_in=issued.expires_in,
            session_id=issued.session_id,
            auth_time=now,
            acr="mfa",
            return_to=str(pending.get("return_to") or "/"),
        )

    # --- the provider -------------------------------------------------------------------
    def _client(self) -> OAuth2Session:
        return OAuth2Session(
            self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            scope="openid email profile",
            code_challenge_method="S256",
        )

    def _exchange(self, code: str, verifier: str) -> dict[str, Any]:
        """Spend the authorization code at the token endpoint, secret in the header only."""
        client = self._client()
        try:
            token = client.fetch_token(
                self._endpoint("token_endpoint"),
                code=code,
                code_verifier=verifier,
                grant_type="authorization_code",
            )
        except RequestException as exc:
            raise ExternalUnavailable(code="provider_unavailable") from exc
        except (ValueError, KeyError) as exc:
            # Authlib raises its own OAuth2Error (a ValueError) for a refused exchange.
            raise Unauthenticated(code="invalid_code") from exc
        return dict(token)

    def _claims(self, token: dict[str, Any], nonce: str) -> dict[str, Any]:
        id_token = token.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise Unauthenticated(code="invalid_code")
        # Two attempts: the second refetches the key set, in case the provider rotated it.
        claims = self._decode(id_token, refresh=False) or self._decode(id_token, refresh=True)
        if claims is None:
            raise Unauthenticated(code="invalid_code")
        if not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
            raise Unauthenticated(code="invalid_code")
        amr = claims.get("amr") or ()
        if "mfa" not in [str(entry) for entry in amr]:
            # The provider did not confirm a second factor, so this is not the MFA assurance
            # the staff API requires: the user must sign in through a flow that asserts it.
            raise Unauthenticated(code="mfa_required")
        return claims

    def _decode(self, id_token: str, *, refresh: bool) -> dict[str, Any] | None:
        """Verify one ID token; ``None`` means "refused", never "try again with a token"."""
        try:
            header = jwt.get_unverified_header(id_token)
            signing_key = self._signing_key(str(header.get("kid") or ""), refresh=refresh)
            if signing_key is None:
                return None
            claims = jwt.decode(
                id_token,
                signing_key,
                algorithms=SUPPORTED_ALGORITHMS,
                audience=self._client_id,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except InvalidTokenError:
            return None
        return dict(claims)

    def _signing_key(self, kid: str, *, refresh: bool) -> Any:
        """The provider's key for this token, by ``kid`` when it names one."""
        keys = {entry.key_id: entry for entry in self._key_set(refresh=refresh).keys}
        entry = keys.get(kid) if kid else next(iter(keys.values()), None)
        return None if entry is None else entry.key

    def _endpoint(self, name: str) -> str:
        endpoint = self._metadata().get(name)
        if not isinstance(endpoint, str) or not endpoint:
            raise ExternalUnavailable(code="provider_unavailable")
        return endpoint

    def _metadata(self) -> dict[str, Any]:
        """The discovery document, fetched once and kept for the life of the process."""
        if self._discovery is None:
            url = f"{self._issuer}/.well-known/openid-configuration"
            try:
                response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
                response.raise_for_status()
                self._discovery = dict(response.json())
            except (RequestException, ValueError) as exc:
                raise ExternalUnavailable(code="provider_unavailable") from exc
        return self._discovery

    def _key_set(self, *, refresh: bool = False) -> PyJWKSet:
        if self._key_set_cache is None or refresh:
            try:
                response = requests.get(self._endpoint("jwks_uri"), timeout=HTTP_TIMEOUT_SECONDS)
                response.raise_for_status()
                self._key_set_cache = PyJWKSet.from_dict(dict(response.json()))
            except (RequestException, ValueError, InvalidTokenError) as exc:
                raise ExternalUnavailable(code="provider_unavailable") from exc
        return self._key_set_cache

    # --- the browser binding ------------------------------------------------------------
    def _consume(self, browser_binding: str | None) -> dict[str, Any]:
        """Pop the pending flow for this browser; a missing or spent one is a 400."""
        if not browser_binding:
            raise InvalidRequest(code="invalid_request")
        raw = self._store.pop(_state_key(browser_binding))
        if raw is None:
            raise InvalidRequest(code="invalid_request")
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise InvalidRequest(code="invalid_request") from exc
        if not isinstance(value, dict):
            raise InvalidRequest(code="invalid_request")
        return value


def _state_key(binding: str) -> str:
    return f"oidc_state:{binding}"
