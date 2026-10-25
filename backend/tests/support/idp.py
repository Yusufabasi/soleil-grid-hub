"""A real, if tiny, OpenID provider on loopback: discovery, JWKS and a token endpoint.

The login under test is a confidential client that talks HTTP to a provider, so a mock object
would prove almost nothing: the discovery document, the JWKS, the PKCE exchange and the ID
token signature are exactly the parts that break. This serves all three from a real socket, and
``authorize`` plays the browser's part — it parses the authorization URL the server produced,
checks the PKCE parameters it must contain, and hands back a code bound to them. The token
endpoint then refuses a verifier that does not match, so a broken PKCE implementation fails the
test rather than passing it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

__all__ = ["FakeIdp", "IdpIdentity"]

KID = "fake-idp-signing-key"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _challenge_for(verifier: str) -> str:
    """S256: the only code challenge method this provider advertises."""
    return _b64url(hashlib.sha256(verifier.encode()).digest())


@dataclass
class IdpIdentity:
    """Who the provider will claim to have authenticated, and how."""

    sub: str = "idp-subject-0001"
    email: str = "officer@example.test"
    amr: tuple[str, ...] = ("pwd", "mfa")


@dataclass
class FakeIdp:
    """An authorization-code provider with ES256 ID tokens."""

    client_id: str = "hub-api-test"
    client_secret: str = "idp-client-secret-never-in-a-redirect"
    identity: IdpIdentity = field(default_factory=IdpIdentity)
    #: Set by a test to make the provider answer the code exchange with an error.
    fail_token_exchange: bool = False
    token_requests: int = 0
    authorization_requests: list[dict[str, str]] = field(default_factory=list)

    _pending: dict[str, dict[str, str]] = field(default_factory=dict)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None
    _key: ec.EllipticCurvePrivateKey = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())

    # --- lifecycle -----------------------------------------------------------------------
    def __enter__(self) -> FakeIdp:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    def start(self) -> FakeIdp:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
                provider._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                provider._handle_post(self)

            def log_message(self, *args: Any) -> None:
                """Silence the default stderr log: a test run should not print HTTP lines."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("the provider is not started")
        return int(self._server.server_address[1])

    @property
    def issuer(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def metadata_url(self) -> str:
        return f"{self.issuer}/.well-known/openid-configuration"

    # --- the browser's part --------------------------------------------------------------
    def authorize(self, location: str) -> str:
        """Parse the server's authorization URL, check it, and return a code for it."""
        parts = urlsplit(location)
        params = {key: values[0] for key, values in parse_qs(parts.query).items()}
        assert parts.scheme == "http", location
        assert params.get("response_type") == "code", params
        assert params.get("client_id") == self.client_id, params
        assert params.get("redirect_uri"), params
        assert params.get("code_challenge_method") == "S256", params
        assert len(params.get("code_challenge", "")) == 43, params
        assert params.get("state"), params
        assert params.get("nonce"), params
        self.authorization_requests.append(params)
        code = f"code-{secrets.token_urlsafe(12)}"
        self._pending[code] = {
            "challenge": params["code_challenge"],
            "nonce": params["nonce"],
        }
        return code

    def id_token(self, *, nonce: str, **overrides: Any) -> str:
        """Sign an ID token; overrides let a test bend one claim at a time."""
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": self.identity.sub,
            "email": self.identity.email,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": nonce,
            "amr": list(self.identity.amr),
        }
        claims.update(overrides)
        return str(jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": KID}))

    # --- the HTTP surface ----------------------------------------------------------------
    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        path = urlsplit(handler.path).path
        if path == "/.well-known/openid-configuration":
            self._send_json(
                handler,
                {
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/jwks",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["ES256"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
            return
        if path == "/jwks":
            self._send_json(handler, {"keys": [self._public_jwk()]})
            return
        self._send_json(handler, {"error": "not_found"}, status=404)

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        self.token_requests += 1
        if urlsplit(handler.path).path != "/token":
            self._send_json(handler, {"error": "not_found"}, status=404)
            return
        length = int(handler.headers.get("Content-Length") or 0)
        form = {
            key: values[0] for key, values in parse_qs(handler.rfile.read(length).decode()).items()
        }
        client_id, client_secret = self._client_credentials(handler, form)
        if (client_id, client_secret) != (self.client_id, self.client_secret):
            self._send_json(handler, {"error": "invalid_client"}, status=401)
            return
        if self.fail_token_exchange:
            self._send_json(handler, {"error": "invalid_grant"}, status=400)
            return
        code = form.get("code", "")
        pending = self._pending.pop(code, None)
        if pending is None:
            self._send_json(handler, {"error": "invalid_grant"}, status=400)
            return
        if form.get("grant_type") != "authorization_code":
            self._send_json(handler, {"error": "unsupported_grant_type"}, status=400)
            return
        if _challenge_for(form.get("code_verifier", "")) != pending["challenge"]:
            # The provider cannot invert S256; it recomputes it, which is the whole check.
            self._send_json(handler, {"error": "invalid_grant"}, status=400)
            return
        self._send_json(
            handler,
            {
                "access_token": f"at-{secrets.token_urlsafe(8)}",
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": self.id_token(nonce=pending["nonce"]),
            },
        )

    # --- helpers -------------------------------------------------------------------------
    def _client_credentials(
        self, handler: BaseHTTPRequestHandler, form: dict[str, str]
    ) -> tuple[str, str]:
        """Basic auth first (what Authlib sends), then the form-encoded fallback."""
        header = handler.headers.get("Authorization") or ""
        if header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
            except ValueError:
                return "", ""
            client_id, _, client_secret = decoded.partition(":")
            return client_id, client_secret
        return form.get("client_id", ""), form.get("client_secret", "")

    def _public_jwk(self) -> dict[str, str]:
        numbers = self._key.public_key().public_numbers()
        return {
            "kty": "EC",
            "crv": "P-256",
            "use": "sig",
            "alg": "ES256",
            "kid": KID,
            "x": _b64url(numbers.x.to_bytes(32, "big")),
            "y": _b64url(numbers.y.to_bytes(32, "big")),
        }

    def _send_json(
        self, handler: BaseHTTPRequestHandler, body: dict[str, Any], *, status: int = 200
    ) -> None:
        payload = json.dumps(body).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
