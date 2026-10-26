"""Unmanaged models over the meter tables (ADR-0020, docs/04 section 3).

``meter_assignments`` and ``meter_state`` are read-only here: the assignment history is
written by the ``trg_meters_assignment`` trigger and the state row by the ingest pool, so a
model field that looked writable would be a promise this module cannot keep.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

__all__ = ["Gateway", "Meter", "MeterAssignment", "MeterState"]


class UnmanagedShim(models.Model):
    """Base of the shims: no table of its own, and a printable identity for the ORM."""

    class Meta:
        abstract = True
        managed = False

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.pk})"


class Gateway(UnmanagedShim):
    """``hub.gateways``: read here only; site health is computed from its heartbeat."""

    id = models.UUIDField(primary_key=True)
    site_id = models.UUIDField()
    name = models.TextField()
    api_key_id = models.UUIDField(null=True)
    status = models.TextField(default="active")
    firmware = models.TextField(null=True)  # noqa: DJ001 - nullable in db/schema.sql
    last_heartbeat_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        app_label = "meters"
        db_table = "gateways"


class Meter(UnmanagedShim):
    """``hub.meters``: serial, site, the customer it serves and its lifecycle (FR-01)."""

    id = models.UUIDField(primary_key=True)
    serial_no = models.TextField()
    site_id = models.UUIDField()
    gateway_id = models.UUIDField(null=True)
    customer_id = models.UUIDField(null=True)
    status = models.TextField(default="provisioned")
    firmware = models.TextField(null=True)  # noqa: DJ001
    token_counter = models.BigIntegerField(default=0)
    installed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    version = models.IntegerField(default=1)

    class Meta:
        managed = False
        app_label = "meters"
        db_table = "meters"


class MeterAssignment(UnmanagedShim):
    """``hub.meter_assignments``: every pair that ever existed, written by a trigger."""

    meter_id = models.UUIDField(primary_key=True)
    customer_id = models.UUIDField()
    first_assigned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        app_label = "meters"
        db_table = "meter_assignments"


class MeterState(UnmanagedShim):
    """``hub.meter_state``: the hot last-seen row, owned by the ingest pool."""

    meter_id = models.UUIDField(primary_key=True)
    last_seen_at = models.DateTimeField(null=True)
    last_reading_at = models.DateTimeField(null=True)
    last_cum_energy_wh = models.BigIntegerField(null=True)
    relay_closed = models.BooleanField(null=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        app_label = "meters"
        db_table = "meter_state"
