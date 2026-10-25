"""Ten-minute EdDSA access tokens (D-17, docs/03 2.2).

The token is identity, never authority: it names the subject, the session, the account kind,
the locale, when the session last proved itself, and the account's token version. Roles,
attributes, permissions and site scope are read from the database on every request, so a
claim the client edits has nothing to edit and a role change takes effect immediately.

Three properties are enforced rather than assumed, because each is a way tokens get forged:

* ``alg`` is EdDSA and nothing else. ``none`` and an HMAC signature computed with the public
  key as the secret are both refused by the same allow-list.
* the lifetime is at most ``ttl_seconds``; a token minted by another issuer with a longer
  life is still refused, even if it verifies.
* ``iat``/``exp`` are computed from the injected ``Clock``, so a test can sit exactly on the
  30-second leeway boundary instead of sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import jwt

from hub.identity.domain.principal import BASIC_ACR, MFA_ACR, Principal
from hub.shared.errors import Unauthenticated
from hub.shared.ports import Clock, IdGenerator

__all__ = ["AccessTokenService", "TokenClaims"]

ALGORITHM = "EdDSA"
ISSUER = "hub"
AUDIENCE = "hub-api"
ACCESS_TTL_SECONDS = 600
CLOCK_LEEWAY_SECONDS = 30

KNOWN_KINDS = frozenset({"staff", "customer", "provider", "gateway", "integrator", "public"})

# A token that verifies but claims a longer life than this API issues is refused: the
# signature only proves *who* signed it, not that it came from this service.
_LIFETIME_SLACK_SECONDS = 1


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """What a verified access token says: identity and session, never power."""

    subject_id: UUID
    session_id: UUID
    kind: str
    token_version: int
    locale: str
    auth_time: datetime
    acr: str
    amr: tuple[str, ...]

    @property
    def has_mfa(self) -> bool:
        return self.acr in {MFA_ACR, "phr"}


def _uuid_claim(claims: dict[str, object], name: str) -> UUID:
    try:
        return UUID(str(claims[name]))
    except (KeyError, ValueError) as exc:
        raise Unauthenticated() from exc


def _integer_claim(claims: dict[str, object], name: str) -> int:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise Unauthenticated()
    return value


def _instant(value: object) -> datetime:
    if not isinstance(value, int | float):
        raise Unauthenticated()
    return datetime.fromtimestamp(float(value), UTC)


class AccessTokenService:
    """Issues and verifies the access token. Stateless: everything lives in the JWT."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        private_key: str,
        public_key: str,
        issuer: str = ISSUER,
        audience: str = AUDIENCE,
        ttl_seconds: int = ACCESS_TTL_SECONDS,
        leeway_seconds: int = CLOCK_LEEWAY_SECONDS,
    ) -> None:
        if not private_key or not public_key:
            raise ValueError("both the JWT signing key and its public half are required")
        self._clock = clock
        self._ids = ids
        self._private_key = private_key
        self._public_key = public_key
        self._issuer = issuer
        self._audience = audience
        self._ttl = ttl_seconds
        self._leeway = leeway_seconds

    @property
    def public_key(self) -> str:
        """The verification half, exposed so a test can prove key confusion is refused."""
        return self._public_key

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def issue(
        self,
        principal: Principal,
        *,
        session_id: UUID,
        auth_time: datetime,
        acr: str = MFA_ACR,
        amr: tuple[str, ...] = ("pwd", "otp"),
    ) -> str:
        """Mint one token for ``principal`` in ``session_id``."""
        issued_at = int(self._clock.now().timestamp())
        payload: dict[str, object] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(principal.subject_id),
            "sid": str(session_id),
            "tv": principal.token_version,
            "kind": principal.kind,
            "loc": principal.locale,
            # OIDC NumericDate: seconds since the epoch, like iat and exp.
            "auth_time": int(auth_time.timestamp()),
            "acr": acr,
            "amr": list(amr),
            "iat": issued_at,
            "exp": issued_at + self._ttl,
            "jti": str(self._ids.new_id()),
        }
        return jwt.encode(
            payload,
            self._private_key,
            algorithm=ALGORITHM,
            headers={"kid": "k1", "typ": "JWT"},
        )

    def verify(self, token: str) -> TokenClaims:
        """Verify signature, issuer, audience, life and shape; raise 401 otherwise."""
        try:
            claims: dict[str, object] = jwt.decode(
                token,
                self._public_key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                # Time is judged below, against the injected clock: PyJWT would use the wall
                # clock, and a frozen clock is the whole point of this service's tests (and of
                # a replay window that must not depend on the machine's timezone).
                options={
                    "require": ["exp", "iat", "sub", "sid", "tv", "iss", "aud"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise Unauthenticated() from exc

        issued_at = _integer_claim(claims, "iat")
        expires_at = _integer_claim(claims, "exp")
        now = self._clock.now().timestamp()
        if expires_at <= now - self._leeway:
            raise Unauthenticated()
        if issued_at > now + self._leeway:
            raise Unauthenticated()
        if expires_at - issued_at > self._ttl + _LIFETIME_SLACK_SECONDS:
            raise Unauthenticated()

        kind = claims.get("kind")
        if not isinstance(kind, str) or kind not in KNOWN_KINDS:
            raise Unauthenticated()

        acr = claims.get("acr", BASIC_ACR)
        if not isinstance(acr, str):
            raise Unauthenticated()

        locale = claims.get("loc", "en")
        raw_amr = claims.get("amr", [])
        return TokenClaims(
            subject_id=_uuid_claim(claims, "sub"),
            session_id=_uuid_claim(claims, "sid"),
            kind=kind,
            token_version=_integer_claim(claims, "tv"),
            locale="fr" if locale == "fr" else "en",
            auth_time=_instant(claims.get("auth_time", issued_at)),
            acr=acr,
            amr=tuple(str(value) for value in raw_amr) if isinstance(raw_amr, list) else (),
        )
