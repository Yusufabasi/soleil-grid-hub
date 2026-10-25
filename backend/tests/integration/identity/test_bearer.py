"""Bearer authentication against the real rows: the token proves who, the database says what.

Two controls are on show. A token's ``tv`` must match ``users.token_version`` within the
cache's sixty seconds, so a role change or a suspension stops working immediately rather than
when the ten-minute token expires (TC-FR-15-4). And authority is always read from the
database: a token minted before a role was granted acts with the role, because the token never
carried one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from django.test import Client

from hub.identity import wiring
from hub.identity.domain.principal import Principal
from hub.identity.infrastructure.models import User, UserRole
from hub.identity.infrastructure.repositories import UserRepository
from support.principals import SeededUser, seed_staff_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

AUDIT_URL = "/api/v1/audit-log?stream=auth"


def token_for(
    user: SeededUser,
    *,
    token_version: int | None = None,
    auth_time: datetime | None = None,
) -> str:
    """A token exactly as the login flow mints it, for an existing row."""
    principal = Principal(
        subject_id=user.user_id,
        kind="staff",
        roles=user.principal.roles,
        attributes=user.principal.attributes,
        session_id=uuid.uuid4(),
        token_version=(user.principal.token_version if token_version is None else token_version),
        locale=user.principal.locale,
    )
    return wiring.tokens().issue(
        principal,
        session_id=principal.session_id or uuid.uuid4(),
        auth_time=auth_time or datetime.now(UTC),
        acr="mfa",
    )


def get(client: Client, token: str | None, url: str = AUDIT_URL) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(url, headers=headers)


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's only job is the store
    return Client()


@pytest.mark.tc("TC-FR-15-4")
def test_valid_token_authenticates(client: Client) -> None:
    administrator = seed_staff_user("administrator")

    response = get(client, token_for(administrator))

    assert response.status_code == 200, response.content


@pytest.mark.tc("TC-FR-15-4")
def test_missing_token_is_401(client: Client) -> None:
    response = get(client, None)

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.tc("TC-FR-15-4")
def test_garbage_token_is_401(client: Client) -> None:
    response = get(client, "not.a.token")

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.tc("TC-FR-15-4")
def test_stale_tv_rejected(client: Client, redis_store) -> None:  # noqa: ANN001
    administrator = seed_staff_user("administrator")
    token = token_for(administrator)
    assert get(client, token).status_code == 200

    UserRepository(wiring.store()).bump_token_version(administrator.user_id)

    response = get(client, token)
    assert response.status_code == 401
    assert response.json()["code"] == "token_revoked"


@pytest.mark.tc("TC-FR-15-4")
def test_stale_tv_rejected_once_the_cache_is_cold(client: Client, redis_store) -> None:  # noqa: ANN001
    """A second process's bump is visible at most one cache TTL later."""
    administrator = seed_staff_user("administrator")
    token = token_for(administrator)
    assert get(client, token).status_code == 200

    # Bypass the repository (and its cache invalidation), as another process would.
    User.objects.filter(id=administrator.user_id).update(token_version=2)
    redis_store.delete(f"tv:{administrator.user_id}")

    response = get(client, token)
    assert response.status_code == 401
    assert response.json()["code"] == "token_revoked"


@pytest.mark.tc("TC-FR-15-4")
def test_suspended_user_rejected(client: Client) -> None:
    agent = seed_staff_user("support_agent")
    token = token_for(agent)
    assert get(client, token).status_code in (200, 403)

    User.objects.filter(id=agent.user_id).update(status="suspended")

    response = get(client, token)
    assert response.status_code == 401
    assert response.json()["code"] == "token_revoked"


@pytest.mark.tc("TC-FR-15-4")
def test_soft_deleted_user_rejected(client: Client) -> None:
    administrator = seed_staff_user("administrator")
    token = token_for(administrator)

    User.objects.filter(id=administrator.user_id).update(deleted_at=datetime.now(UTC))

    assert get(client, token).status_code == 401


@pytest.mark.tc("TC-FR-15-4")
def test_authority_is_read_from_the_database_not_the_token(client: Client) -> None:
    """The token was minted without a role; the grant still applies on the next request."""
    agent = seed_staff_user()
    token = token_for(agent)
    assert get(client, token).status_code == 403

    UserRole.objects.create(user_id=agent.user_id, role_code="administrator")

    assert get(client, token).status_code == 200


@pytest.mark.tc("TC-FR-15-4")
def test_non_bearer_scheme_is_treated_as_no_credential(client: Client) -> None:
    response = client.get(AUDIT_URL, HTTP_AUTHORIZATION="Basic YWJjOmRlZg==")

    assert response.status_code == 401
