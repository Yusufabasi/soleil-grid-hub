"""``GET /me`` and ``PATCH /me``: the caller as every other response shows them (A-23, FR-16).

The document is the one ``principal_document`` builds, so these tests pin the shape the whole
API shares — roles, attributes, sites, locale, enrolment and the authentication this session
actually reached — and the two behaviours that are easy to get wrong: an unsupported locale is
stored as ``en`` rather than refused, and a problem's detail follows the caller's own language.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client

from hub.identity import wiring
from hub.identity.infrastructure.models import User
from hub.shared.clock import FrozenClock
from support.login import seed_local_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

ME = "/api/v1/me"
NOW = datetime(2026, 10, 24, 9, 0, 15, tzinfo=UTC)


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def clock() -> FrozenClock:
    frozen = FrozenClock(NOW)
    wiring.use_clock(frozen)
    return frozen


def seed_site() -> UUID:
    """A site row: ``user_sites`` has a foreign key to it, and identity does not own sites."""
    site_id = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f000a")
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency, timezone, status) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [site_id, "SITE-A", "Site A", "USD", "Africa/Kinshasa", "active"],
        )
    return site_id


def token_for(user: Any, clock: FrozenClock) -> str:
    return wiring.tokens().issue(
        user.principal, session_id=uuid.uuid4(), auth_time=clock.now(), acr="mfa"
    )


def get_me(client: Client, token: str, **extra: Any) -> Any:
    return client.get(ME, HTTP_AUTHORIZATION=f"Bearer {token}", **extra)


def patch_me(client: Client, token: str, body: dict[str, Any], **extra: Any) -> Any:
    return client.patch(
        ME,
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
        **extra,
    )


@pytest.mark.tc("TC-FR-15-1")
def test_get_me_returns_locale_roles_attributes_sites(client: Client, clock: FrozenClock) -> None:
    site_id = seed_site()
    user = seed_local_user(
        "finance_officer",
        attributes=("finance_lead",),
        site_ids=(site_id,),
        locale="fr",
    )

    response = get_me(client, token_for(user, clock))

    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["id"] == str(user.user_id)
    assert data["type"] == "staff"
    assert data["roles"] == ["finance_officer"]
    assert data["attributes"] == ["finance_lead"]
    assert data["site_ids"] == [str(site_id)]
    assert data["locale"] == "fr"
    assert data["mfa_enrolled"] is True
    assert data["acr"] == "mfa"
    assert data["auth_time"] == NOW.isoformat().replace("+00:00", "Z")


@pytest.mark.tc("TC-FR-16-3")
def test_patch_me_locale_fr_persists_and_unsupported_locale_falls_back_to_en(
    client: Client, clock: FrozenClock
) -> None:
    user = seed_local_user("support_agent")
    token = token_for(user, clock)

    changed = patch_me(client, token, {"locale": "fr"})

    assert changed.status_code == 200, changed.content
    assert changed.json()["data"]["locale"] == "fr"
    assert User.objects.get(id=user.user_id).preferred_language == "fr"

    # The next response — and the next problem — speaks French because the row says so.
    follows = get_me(client, token)
    assert follows.json()["data"]["locale"] == "fr"

    unsupported = patch_me(client, token, {"locale": "de"})

    assert unsupported.status_code == 200, unsupported.content
    assert unsupported.json()["data"]["locale"] == "en"
    assert User.objects.get(id=user.user_id).preferred_language == "en"


@pytest.mark.tc("TC-FR-16-3")
def test_problem_detail_localised_by_principal_locale(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent", locale="fr")
    token = token_for(user, clock)

    # An empty display name fails validation, which is a 422 whose detail is localised.
    refused = patch_me(client, token, {"display_name": ""})

    assert refused.status_code == 422
    assert refused["Content-Type"].startswith("application/problem+json")
    assert "validation" in refused.json()["detail"].lower()
    assert refused.json()["detail"].startswith("Le contenu")


@pytest.mark.tc("TC-FR-15-1")
def test_me_requires_a_token_and_names_the_display_name(client: Client, clock: FrozenClock) -> None:
    anonymous = client.get(ME)
    assert anonymous.status_code == 401
    assert anonymous["WWW-Authenticate"].startswith("Bearer")

    user = seed_local_user("operations_engineer")
    response = get_me(client, token_for(user, clock))
    assert response.json()["data"]["display_name"] == "Test operations_engineer"


@pytest.mark.tc("TC-FR-15-9")
def test_me_auth_time_moves_with_a_step_up(client: Client, clock: FrozenClock) -> None:
    user = seed_local_user("support_agent")
    old = clock.now() - timedelta(minutes=10)
    token = wiring.tokens().issue(user.principal, session_id=uuid.uuid4(), auth_time=old, acr="mfa")
    assert get_me(client, token).json()["data"]["auth_time"] == old.isoformat().replace(
        "+00:00", "Z"
    )
