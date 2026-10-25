"""Access tokens: EdDSA only, ten minutes, and claims that are identity rather than power.

The token is the one value a client holds, so its failure modes are the ones that matter:
`alg: none`, an HMAC signature computed with the public key as the secret (key confusion),
a token edited after signature, and a token that outlives its ten minutes. Each is refused
here, and the honest token carries no `roles` claim at all — roles are read from the
database on every request (docs/03 2.2, D-17), so there is nothing in a token worth
tampering with (TC-FR-15-4).
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from hub.identity.application.tokens import AccessTokenService, TokenClaims
from hub.identity.domain.principal import Principal
from hub.shared.clock import FrozenClock
from hub.shared.errors import Unauthenticated
from hub.shared.ids import SequentialIdGenerator

ISSUER = "hub"
AUDIENCE = "hub-api"
TTL = 600
LEEWAY = 30
START = datetime(2026, 10, 24, 8, 0, tzinfo=UTC)


def key_pair() -> tuple[str, str]:
    """A throwaway Ed25519 pair in PEM form, exactly the shape the settings carry."""
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(START)


@pytest.fixture
def service(clock: FrozenClock) -> AccessTokenService:
    private, public = key_pair()
    return AccessTokenService(
        clock=clock,
        ids=SequentialIdGenerator(),
        private_key=private,
        public_key=public,
        issuer=ISSUER,
        audience=AUDIENCE,
        ttl_seconds=TTL,
        leeway_seconds=LEEWAY,
    )


@pytest.fixture
def principal() -> Principal:
    return Principal(
        subject_id=uuid.uuid4(),
        kind="staff",
        roles=frozenset({"finance_officer"}),
        attributes=frozenset({"finance_lead"}),
        session_id=uuid.uuid4(),
        token_version=7,
        locale="fr",
    )


def issue(service: AccessTokenService, principal: Principal) -> str:
    return service.issue(
        principal,
        session_id=principal.session_id or uuid.uuid4(),
        auth_time=principal.auth_time or START,
        acr="mfa",
    )


def payload_of(token: str) -> dict[str, object]:
    segment = token.split(".")[1]
    padded = segment + "=" * (-len(segment) % 4)
    decoded: dict[str, object] = json.loads(base64.urlsafe_b64decode(padded))
    return decoded


def forge(header: dict[str, object], payload: dict[str, object]) -> str:
    """A token with the given header and payload and no usable signature."""

    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


@pytest.mark.tc("TC-FR-15-4")
def test_token_lifetime_is_600_seconds(service: AccessTokenService, principal: Principal) -> None:
    token = issue(service, principal)
    header = jwt.get_unverified_header(token)
    claims = payload_of(token)

    assert header["alg"] == "EdDSA"
    assert int(cast("int", claims["exp"])) - int(cast("int", claims["iat"])) == TTL
    assert claims["iss"] == ISSUER
    assert claims["aud"] == AUDIENCE
    assert claims["sub"] == str(principal.subject_id)
    assert claims["sid"] == str(principal.session_id)
    assert claims["tv"] == 7
    assert claims["kind"] == "staff"
    assert claims["loc"] == "fr"
    assert claims["acr"] == "mfa"


@pytest.mark.tc("TC-FR-15-4")
def test_token_carries_no_roles_or_attributes(
    service: AccessTokenService, principal: Principal
) -> None:
    """A tampered role claim cannot exist: the token does not carry roles at all."""
    claims = payload_of(issue(service, principal))

    assert "roles" not in claims
    assert "attributes" not in claims
    assert "permissions" not in claims


@pytest.mark.tc("TC-FR-15-4")
def test_verified_claims_are_identity_only(
    service: AccessTokenService, principal: Principal
) -> None:
    claims = service.verify(issue(service, principal))

    assert isinstance(claims, TokenClaims)
    assert claims.subject_id == principal.subject_id
    assert claims.session_id == principal.session_id
    assert claims.token_version == 7
    assert claims.locale == "fr"
    assert claims.acr == "mfa"


@pytest.mark.tc("TC-FR-15-4")
def test_alg_none_rejected(service: AccessTokenService) -> None:
    now = int(START.timestamp())
    token = forge(
        {"alg": "none", "typ": "JWT"},
        {"sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "tv": 1, "iat": now, "exp": now + 600},
    )

    with pytest.raises(Unauthenticated):
        service.verify(token)


@pytest.mark.tc("TC-FR-15-4")
def test_hs256_with_public_key_as_secret_rejected(
    service: AccessTokenService, principal: Principal
) -> None:
    """Key confusion: signing with HS256 and the *public* key as the secret must not verify."""
    public = load_pem_public_key(service.public_key.encode())
    # The raw 32-byte key material: PyJWT refuses a PEM as an HMAC secret, but the bytes
    # themselves are what a confused verifier would accept.
    raw = public.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    now = int(START.timestamp())
    forged = jwt.encode(
        {
            "sub": str(principal.subject_id),
            "sid": str(principal.session_id),
            "tv": 7,
            "iat": now,
            "exp": now + 600,
            "iss": ISSUER,
            "aud": AUDIENCE,
        },
        raw,
        algorithm="HS256",
    )

    with pytest.raises(Unauthenticated):
        service.verify(forged)


@pytest.mark.tc("TC-FR-15-4")
def test_expired_token_rejected_after_leeway(
    service: AccessTokenService, principal: Principal, clock: FrozenClock
) -> None:
    token = issue(service, principal)

    clock.advance(timedelta(seconds=TTL + LEEWAY - 1))
    assert service.verify(token).subject_id == principal.subject_id

    clock.advance(timedelta(seconds=2))
    with pytest.raises(Unauthenticated):
        service.verify(token)


@pytest.mark.tc("TC-FR-15-4")
def test_tampered_role_claim_rejected(service: AccessTokenService, principal: Principal) -> None:
    token = issue(service, principal)
    header, payload, signature = token.split(".")
    claims = payload_of(token)
    claims["roles"] = ["administrator"]
    edited = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")

    with pytest.raises(Unauthenticated):
        service.verify(f"{header}.{edited}.{signature}")


@pytest.mark.tc("TC-FR-15-4")
def test_token_from_another_issuer_or_audience_rejected(
    principal: Principal, clock: FrozenClock
) -> None:
    private, public = key_pair()
    other = AccessTokenService(
        clock=clock,
        ids=SequentialIdGenerator(),
        private_key=private,
        public_key=public,
        issuer="someone-else",
        audience=AUDIENCE,
        ttl_seconds=TTL,
        leeway_seconds=LEEWAY,
    )
    mine = AccessTokenService(
        clock=clock,
        ids=SequentialIdGenerator(),
        private_key=private,
        public_key=public,
        issuer=ISSUER,
        audience=AUDIENCE,
        ttl_seconds=TTL,
        leeway_seconds=LEEWAY,
    )
    token = issue(other, principal)

    with pytest.raises(Unauthenticated):
        mine.verify(token)


@pytest.mark.tc("TC-FR-15-4")
def test_token_longer_than_the_ttl_rejected(clock: FrozenClock, principal: Principal) -> None:
    """A token minted elsewhere with a month-long life is not this API's token."""
    private, public = key_pair()
    service = AccessTokenService(
        clock=clock,
        ids=SequentialIdGenerator(),
        private_key=private,
        public_key=public,
        issuer=ISSUER,
        audience=AUDIENCE,
        ttl_seconds=TTL,
        leeway_seconds=LEEWAY,
    )
    now = int(START.timestamp())
    long_lived = jwt.encode(
        {
            "sub": str(principal.subject_id),
            "sid": str(principal.session_id),
            "tv": 7,
            "iat": now,
            "exp": now + 30 * 86_400,
            "iss": ISSUER,
            "aud": AUDIENCE,
        },
        private,
        algorithm="EdDSA",
    )

    with pytest.raises(Unauthenticated):
        service.verify(long_lived)
