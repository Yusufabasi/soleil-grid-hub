"""The registry repositories against real PostgreSQL: versioning and natural keys (Task 02.1).

Two things are only true of the database, not of the Python around it: the ``version`` bump
is the trigger's work, and a duplicate phone is a unique index the server refuses. Both are
asserted here so a later refactor cannot quietly move either into the application.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from django.db import transaction

from hub.customers.contracts import CustomerRow, PlanRow, SiteRow
from hub.customers.domain.errors import DuplicatePhone, VersionConflict
from hub.customers.infrastructure.repositories import (
    CustomerRepository,
    PlanRepository,
    SiteRepository,
)
from hub.shared.errors import Duplicate

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

SITE_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0201")
PLAN_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0202")
CUSTOMER_ID = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0203")
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def a_site(code: str = "KIN", site_id: UUID = SITE_ID) -> SiteRow:
    return SiteRow(
        id=site_id,
        code=code,
        name="Kinshasa",
        country_code="CD",
        currency="USD",
        timezone="Africa/Kinshasa",
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )


def a_plan(code: str = "BASIC", plan_id: UUID = PLAN_ID) -> PlanRow:
    return PlanRow(
        id=plan_id,
        code=code,
        name="Basic",
        currency="USD",
        min_topup_minor=100,
        max_topup_minor=100_000,
        status="draft",
        created_at=NOW,
        updated_at=NOW,
    )


def a_customer(phone: str = "+243810000201") -> CustomerRow:
    return CustomerRow(
        id=CUSTOMER_ID,
        site_id=SITE_ID,
        plan_id=PLAN_ID,
        external_ref=None,
        full_name="Amani Mbuyi",
        phone_e164=phone,
        email=None,
        language="fr",
        status="active",
        pseudonym=None,
        created_at=NOW,
        updated_at=NOW,
        version=1,
    )


def seed_registry() -> None:
    SiteRepository().add(a_site())
    PlanRepository().add(a_plan())


@pytest.mark.tc("TC-FR-01-1")
def test_customer_insert_and_version_bump() -> None:
    """Insert, update through the repository, and let the trigger own the version."""
    seed_registry()
    repository = CustomerRepository()
    repository.add(a_customer())

    stored = repository.get(CUSTOMER_ID)
    assert stored is not None
    assert (stored.full_name, stored.status, stored.version) == ("Amani Mbuyi", "active", 1)

    repository.update_with_version(replace(stored, full_name="Amani M. Mbuyi"), expected_version=1)

    bumped = repository.get(CUSTOMER_ID)
    assert bumped is not None
    assert bumped.full_name == "Amani M. Mbuyi"
    assert bumped.version == 2, "the database trigger, not Python, owns the increment"
    assert bumped.updated_at > stored.updated_at

    # The version the writer read is spent: the same expected version now loses.
    with pytest.raises(VersionConflict):
        repository.update_with_version(
            replace(stored, full_name="Someone Else"), expected_version=1
        )


def test_phone_unique_violation_maps_to_duplicate() -> None:
    """``uq_customers_phone`` is the natural key (D-11): one customer per number."""
    seed_registry()
    repository = CustomerRepository()
    repository.add(a_customer("+243810000202"))

    second = replace(a_customer("+243810000202"), id=UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0204"))
    # A failed statement aborts the enclosing transaction: the savepoint lets the test read
    # on afterwards, which is exactly what the API does around a repository call.
    with pytest.raises(DuplicatePhone), transaction.atomic():
        repository.add(second)

    assert repository.find_by_phone("+243810000202") is not None
    assert repository.find_by_phone("+243810000999") is None


def test_site_and_plan_codes_are_unique() -> None:
    """A duplicate code is a 409 ``duplicate_resource``, not a 500 from the driver."""
    seed_registry()

    with pytest.raises(Duplicate), transaction.atomic():
        SiteRepository().add(
            a_site(code="KIN", site_id=UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0205"))
        )
    with pytest.raises(Duplicate), transaction.atomic():
        PlanRepository().add(
            a_plan(code="BASIC", plan_id=UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0206"))
        )


def test_list_scoped_never_widens_access() -> None:
    """An empty site list matches nothing; ``None`` is the ``all_sites`` case, not a default."""
    seed_registry()
    repository = CustomerRepository()
    repository.add(a_customer())

    assert [row.id for row in repository.list_scoped(site_ids=None)] == [CUSTOMER_ID]
    assert [row.id for row in repository.list_scoped(site_ids=[SITE_ID])] == [CUSTOMER_ID]
    assert repository.list_scoped(site_ids=[]) == []
    assert repository.list_scoped(site_ids=[UUID(int=7)]) == []
