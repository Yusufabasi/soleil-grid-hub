"""The identity models against the schema that owns them (D-01, ADR-0020).

These models are unmanaged shims: they exist so the ORM can type the repositories, and the
tables come from `db/schema.sql`. The test compares every column in both directions, so the
drift shows up here instead of in a query someone runs at 3am (TC-NFR-14-6).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hub.identity.infrastructure import models

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from support.db_alignment import check_db_alignment  # noqa: E402

IDENTITY_MODELS = (
    models.User,
    models.Role,
    models.Permission,
    models.RolePermission,
    models.UserRole,
    models.UserSite,
    models.RefreshToken,
    models.CustomerOtp,
    models.MfaRecoveryCode,
    models.WebauthnCredential,
    models.Approval,
    models.StaffCustomerLink,
    models.AuditLogRow,
)


@pytest.mark.django_db
@pytest.mark.tc("TC-NFR-14-6")
def test_models_match_database():
    check_db_alignment(*IDENTITY_MODELS)


@pytest.mark.tc("TC-NFR-14-6")
def test_models_are_unmanaged():
    # A managed model would let Django write the schema, and the schema is the authority.
    for model in IDENTITY_MODELS:
        assert model._meta.managed is False, model.__name__
        assert model._meta.db_table.startswith("hub."), model.__name__
