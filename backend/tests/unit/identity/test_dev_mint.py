"""The dev-only token mint (docs/plan/frontend/demo-shell-plan.md section 3).

Two properties are worth a test, and neither is about the happy path:

* the route is **absent** from the URLconf anywhere but ``local``/``test`` — that is the whole
  security claim of this route, so it is asserted against the real guard rather than trusted;
* a customer token minted here **is** one of this API's tokens: same signing authority, same
  claims, verified by the same service the authentication class uses. A demo signed in with a
  token nobody accepts would be a worse failure than no demo at all.
"""

from __future__ import annotations

import uuid
from importlib import reload

import pytest
from django.test import override_settings
from django.urls import clear_url_caches
from rest_framework.test import APIRequestFactory

import hub.identity.api.urls as identity_urls
from hub.identity import wiring
from hub.identity.api.dev_views import DevMintTokenView

ENVIRONMENT = "test"


@pytest.fixture(autouse=True)
def restore_urlconf():
    """Whatever a case does to the module is undone: the URLconf is process state."""
    yield
    reload(identity_urls)
    clear_url_caches()


def mounted_names() -> set[str]:
    reload(identity_urls)
    clear_url_caches()
    return {str(pattern.name) for pattern in identity_urls.urlpatterns}


def mint(body: dict[str, str]):
    factory = APIRequestFactory()
    request = factory.post("/api/v1/dev/mint-token", body, format="json")
    return DevMintTokenView.as_view()(request)


def test_the_route_is_mounted_in_local_and_test() -> None:
    assert "devMintToken" in mounted_names()


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_route_does_not_exist_anywhere_else(environment: str) -> None:
    """Not denied — absent: there is no path, no view and no name to reach it by."""
    with override_settings(HUB_ENV=environment):
        assert "devMintToken" not in mounted_names()


def test_a_customer_token_is_this_api_s_own_token() -> None:
    customer_id = uuid.uuid4()

    response = mint({"kind": "customer", "subject": str(customer_id)})

    assert response.status_code == 200
    document = response.data["data"]
    assert document["token_type"] == "Bearer"
    assert document["principal"]["type"] == "customer"
    assert "refresh_token" not in document, "the dev route opens no session and stores no cookie"

    claims = wiring.tokens().verify(str(document["access_token"]))
    assert claims.subject_id == customer_id
    assert claims.acr == "pwd"


def test_an_unknown_kind_is_refused() -> None:
    response = mint({"kind": "administrator", "subject": str(uuid.uuid4())})

    assert response.status_code == 422
    assert response.data["code"] == "dev_mint_kind_invalid"


def test_a_malformed_customer_subject_is_refused() -> None:
    response = mint({"kind": "customer", "subject": "not-a-uuid"})

    assert response.status_code == 422
    assert response.data["code"] == "dev_mint_subject_invalid"


@pytest.mark.django_db
def test_an_unknown_staff_address_is_refused_without_saying_which() -> None:
    """The staff path resolves the address the way login does; nobody holds this one."""
    response = mint({"kind": "staff", "subject": "nobody@example.test"})

    assert response.status_code == 422
    assert response.data["code"] == "dev_mint_no_active_staff"
