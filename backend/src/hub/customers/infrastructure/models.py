"""Unmanaged models over the registry tables (ADR-0020, docs/04 sections 3 and 3b).

``managed = False`` and a bare ``db_table``: ``db/schema.sql`` owns every column, default,
constraint and trigger, and the role's ``search_path`` resolves the bare name (D-43). The
models exist so the repositories are typed, nothing more.

Foreign keys that cross a module boundary stay plain uuid columns: ``customers`` may not
import ``meters`` or ``identity`` models (docs/03 section 0), and the database already
enforces the reference.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone as tz

__all__ = ["Customer", "Plan", "PlanTariff", "Site"]


class UnmanagedShim(models.Model):
    """Base of the shims: no table of its own, and a printable identity for the ORM."""

    class Meta:
        abstract = True
        managed = False

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.pk})"


class Site(UnmanagedShim):
    """``hub.sites``: one physical location, the scope every registry row hangs from."""

    id = models.UUIDField(primary_key=True)
    code = models.TextField()
    name = models.TextField()
    country_code = models.CharField(max_length=2, default="CD")
    currency = models.CharField(max_length=3)
    timezone = models.TextField(default="Africa/Lubumbashi")
    status = models.TextField(default="active")
    created_at = models.DateTimeField(default=tz.now)
    updated_at = models.DateTimeField(default=tz.now)

    class Meta:
        managed = False
        app_label = "customers"
        db_table = "sites"


class Plan(UnmanagedShim):
    """``hub.plans``: a tariff family, with the top-up bounds of its currency."""

    id = models.UUIDField(primary_key=True)
    code = models.TextField()
    name = models.TextField()
    currency = models.CharField(max_length=3)
    min_topup_minor = models.BigIntegerField()
    max_topup_minor = models.BigIntegerField()
    status = models.TextField(default="active")
    created_at = models.DateTimeField(default=tz.now)
    updated_at = models.DateTimeField(default=tz.now)

    class Meta:
        managed = False
        app_label = "customers"
        db_table = "plans"


class PlanTariff(UnmanagedShim):
    """``hub.plan_tariffs``: a version of a plan's price, never edited (D-08).

    The row is written only by the ``tariff.change`` executor; a trigger refuses a change
    beyond the per-revision cap, so the guard is the database's, not this model's.
    """

    id = models.UUIDField(primary_key=True)
    plan_id = models.UUIDField()
    tariff_minor_per_kwh = models.BigIntegerField()
    effective_from = models.DateTimeField()
    status = models.TextField(default="proposed")
    proposed_by = models.UUIDField(null=True)
    approval_id = models.UUIDField(null=True)
    created_at = models.DateTimeField(default=tz.now)

    class Meta:
        managed = False
        app_label = "customers"
        db_table = "plan_tariffs"


class Customer(UnmanagedShim):
    """``hub.customers``: the person a meter is assigned to (FR-01, D-11).

    ``version`` is bumped by the ``trg_customers_version`` trigger, never by Python: the
    optimistic check is "did the row I wrote have the version I read", and letting the
    application compute it would make two concurrent writers agree by accident.
    """

    id = models.UUIDField(primary_key=True)
    site_id = models.UUIDField()
    plan_id = models.UUIDField()
    external_ref = models.TextField(null=True)  # noqa: DJ001 - nullable in db/schema.sql
    full_name = models.TextField()
    phone_e164 = models.TextField(null=True)  # noqa: DJ001 - erased customers hold none
    email = models.TextField(null=True)  # noqa: DJ001
    language = models.TextField(default="fr")
    status = models.TextField(default="active")
    pseudonym = models.TextField(null=True)  # noqa: DJ001
    erased_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=tz.now)
    updated_at = models.DateTimeField(default=tz.now)
    deleted_at = models.DateTimeField(null=True)
    version = models.IntegerField(default=1)

    class Meta:
        managed = False
        app_label = "customers"
        db_table = "customers"
