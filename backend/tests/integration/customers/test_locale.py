"""`detail` follows the caller's language, and `code` never changes with it (FR-16, TC-FR-16-3).

Three things are pinned here:

* **A stored language wins.** A support agent whose account says ``fr`` reads a French
  ``detail`` without sending an ``Accept-Language`` header at all — the UI is rendering for
  that person, not for the browser's preference.
* **The header is the fallback, and it fails closed.** ``fr-CA`` is French; an unknown
  language (``es-ES``) is answered in English, never in a half-translated or empty string.
* **Every registry code has both strings**, and they differ: a code with only one catalogue is
  exactly the bug this task exists to prevent, and a French sentence that is a copy of the
  English one is the same bug wearing a beret.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client

from hub.customers import wiring as customers_wiring
from hub.identity import wiring as identity_wiring
from hub.meters import wiring as meters_wiring
from hub.shared.api import problem
from hub.shared.clock import FrozenClock
from hub.shared.errors import Conflict

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

CUSTOMERS = "/api/v1/customers"
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
SITE_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0701")
PHONE = "+243810000701"

# The codes `docs/plan/slice-02-registry.md` task 02.7 names, plus the two the registry raises
# on paths a French support agent reaches every day.
REGISTRY_CODES = (
    "duplicate_phone",
    "serial_conflict",
    "customer_has_balance",
    "precondition_failed",
    "precondition_required",
    "tariff_change_too_large",
    "meter_decommissioned",
    "meter_not_owned",
    "plan_locked",
    "invalid_state_transition",
    "site_mismatch",
    "invalid_phone_number",
)


@pytest.fixture(autouse=True)
def frozen(redis_store) -> Any:  # noqa: ANN001 - the fixture's job is the store
    """A frozen clock, installed after the session store is ready.

    ``redis_store`` resets `identity`'s wiring, which drops whatever clock was installed
    before it. Depending on it here makes that reset happen first, so the clock this suite
    froze is the one the use cases read; the other order stamps approvals with the wall
    clock while the assertions move a frozen one.
    """

    clock = FrozenClock(NOW)
    identity_wiring.use_clock(clock)
    customers_wiring.reset()
    meters_wiring.reset()
    yield clock
    meters_wiring.reset()
    customers_wiring.reset()
    identity_wiring.use_clock(None)
    identity_wiring.reset()


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def site() -> UUID:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, 'LOC-1', 'Locale', 'USD')",
            [SITE_ID],
        )
        cursor.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor) "
            "VALUES (%s, 'LOC-P', 'Locale plan', 'USD', 100, 100000)",
            [uuid.uuid4()],
        )
    return SITE_ID


def seed_user(*roles: str, locale: str = "en", all_sites: bool = True) -> str:
    """An active staff user with a stored language, and a token for it."""
    from hub.identity.infrastructure.models import User, UserRole  # noqa: PLC0415

    user_id = uuid.uuid4()
    User.objects.create(
        id=user_id,
        email=f"{user_id}@example.test",
        display_name="Locale Caller",
        status="active",
        all_sites=all_sites,
        token_version=1,
        preferred_language=locale,
    )
    for role in roles:
        UserRole.objects.create(user_id=user_id, role_code=role)
    principal = identity_wiring.users().build_principal(
        user_id, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )
    return identity_wiring.tokens().issue(
        principal, session_id=uuid.uuid4(), auth_time=NOW, acr="mfa"
    )


def plan_id() -> UUID:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM hub.plans WHERE code = 'LOC-P'")
        row = cursor.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def create_customer(client: Client, token: str, phone: str = PHONE) -> Any:
    return client.post(
        CUSTOMERS,
        data={
            "full_name": "Locale Customer",
            "phone_e164": phone,
            "site_id": str(SITE_ID),
            "plan_id": str(plan_id()),
        },
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.tc("TC-FR-16-1")
def test_problem_detail_in_french_for_french_principal(client: Client, site: UUID) -> None:
    """A French account reads a French detail for a duplicate number, and the same code."""
    francophone = seed_user("support_agent", locale="fr")
    assert create_customer(client, francophone).status_code == 201

    duplicate = create_customer(client, francophone, phone="+243 810 000 701")
    assert duplicate.status_code == 409, duplicate.json()
    body = duplicate.json()
    assert body["code"] == "duplicate_resource", "the code is the contract and does not translate"
    assert body["detail"] == problem.MESSAGES["fr"]["duplicate_phone"]
    assert body["detail"] != problem.MESSAGES["en"]["duplicate_phone"]

    # The same request as an English account: same code, the other sentence.
    anglophone = seed_user("support_agent", locale="en")
    same = create_customer(client, anglophone, phone="+243810000701")
    assert same.status_code == 409, same.json()
    assert same.json()["code"] == body["code"]
    assert same.json()["detail"] == problem.MESSAGES["en"]["duplicate_phone"]


@pytest.mark.tc("TC-FR-16-3")
def test_accept_language_fallback_to_en_for_unknown_locale(client: Client, site: UUID) -> None:
    """No stored language: the header decides, `fr-CA` is French, `es-ES` falls back to English."""
    anonymous = Client()
    path = f"{CUSTOMERS}/{uuid.uuid4()}"

    french = anonymous.get(path, headers={"Accept-Language": "fr-CA,fr;q=0.9"})
    assert french.status_code == 401, french.json()
    assert french.json()["detail"] == problem.MESSAGES["fr"]["unauthenticated"]

    unknown = anonymous.get(path, headers={"Accept-Language": "es-ES,es;q=0.9"})
    assert unknown.status_code == 401, unknown.json()
    assert unknown.json()["detail"] == problem.MESSAGES["en"]["unauthenticated"]

    # A stored language wins over the header: a French caller reading a missing customer gets
    # the French sentence even when the browser asks for English.
    french_token = seed_user("support_agent", locale="fr")
    stored = anonymous.get(
        path,
        headers={"Authorization": f"Bearer {french_token}", "Accept-Language": "en-GB"},
    )
    assert stored.status_code == 404, stored.json()
    assert stored.json()["detail"] == problem.MESSAGES["fr"]["not_found"]


def test_every_registry_error_code_has_en_and_fr_message() -> None:
    missing = {
        code: [language for language in ("en", "fr") if not problem.MESSAGES[language].get(code)]
        for code in REGISTRY_CODES
    }
    assert not any(missing.values()), f"missing catalogue entries: {missing}"

    identical = [
        code
        for code in REGISTRY_CODES
        if problem.MESSAGES["en"][code].strip() == problem.MESSAGES["fr"][code].strip()
    ]
    assert identical == [], f"French text is a copy of the English one: {identical}"


def test_module_catalogues_reach_the_handler_verbatim() -> None:
    """Each module's JSON is what the handler answers with — no second copy, no drift."""
    import hub.customers  # noqa: PLC0415
    import hub.meters  # noqa: PLC0415

    for package in (hub.customers, hub.meters):
        assert package.__file__ is not None
        directory = Path(package.__file__).parent / "locale"
        for language in ("en", "fr"):
            catalogue = json.loads((directory / f"{language}.json").read_text(encoding="utf-8"))
            assert catalogue, directory
            for key, text in catalogue.items():
                assert problem.MESSAGES[language][key] == text


def test_uncatalogued_key_falls_back_to_a_sentence() -> None:
    """A key no module catalogued still answers a sentence, in the caller's language."""
    unknown = Conflict(message_key="not_catalogued_anywhere")
    french = problem.to_problem(
        unknown,
        instance="/api/v1/x",
        request_id="00000000-0000-0000-0000-000000000000",
        accept_language="fr",
    )[1]
    assert french["detail"] == "La requête n'a pas pu être traitée."
    english = problem.to_problem(
        unknown,
        instance="/api/v1/x",
        request_id="00000000-0000-0000-0000-000000000000",
        accept_language="en",
    )[1]
    assert english["detail"] == "The request could not be completed."
