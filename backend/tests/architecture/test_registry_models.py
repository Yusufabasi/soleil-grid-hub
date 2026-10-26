"""The registry models against the schema that owns them (D-01, ADR-0020).

A column the model invents, or one it forgets, is drift the ORM hides until a query fails in
production; the comparison walks both directions, so slice 02's tables are pinned exactly as
slice 01's were (TC-NFR-14-6).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hub.customers.infrastructure import models as customer_models
from hub.meters.infrastructure import models as meter_models

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from support.db_alignment import check_db_alignment  # noqa: E402

REGISTRY_MODELS = (
    customer_models.Site,
    customer_models.Plan,
    customer_models.PlanTariff,
    customer_models.Customer,
    meter_models.Gateway,
    meter_models.Meter,
    meter_models.MeterAssignment,
    meter_models.MeterState,
)


@pytest.mark.django_db
@pytest.mark.tc("TC-NFR-14-6")
def test_registry_models_match_database() -> None:
    check_db_alignment(*REGISTRY_MODELS)


@pytest.mark.tc("TC-NFR-14-6")
def test_registry_models_are_unmanaged_and_unqualified() -> None:
    for model in REGISTRY_MODELS:
        assert model._meta.managed is False, model.__name__
        assert "." not in model._meta.db_table, model.__name__
