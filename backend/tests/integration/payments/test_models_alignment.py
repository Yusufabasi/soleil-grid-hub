"""The payments shims against ``db/schema.sql`` (ADR-0020).

A model that invents a column or forgets one is drift the ORM hides until a query fails in
production; a model whose nullability disagrees with the table hides a bug the other way (a
``None`` the schema would have refused). ``check_db_alignment`` walks both directions.
"""

from __future__ import annotations

import pytest

from hub.payments.infrastructure.models import ProviderEvent, TopUp, WebhookQuarantine
from support.db_alignment import check_db_alignment

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def test_models_match_schema() -> None:
    check_db_alignment(TopUp, ProviderEvent, WebhookQuarantine)


def test_models_are_unmanaged() -> None:
    """Nothing here may be created by ``migrate``: the schema owns these tables."""
    for model in (TopUp, ProviderEvent, WebhookQuarantine):
        assert model._meta.managed is False
        assert "." not in str(model._meta.db_table)
