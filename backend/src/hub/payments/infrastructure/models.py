"""Unmanaged models over the payments tables (ADR-0020, docs/04 section 4).

``hub.top_ups`` is guarded by ``hub.top_ups_guard``: the money, tariff and ownership columns
never change, only the payment path may confirm or reverse, and a confirmation after
``expired`` or ``cancelled`` must say ``late``. ``hub.provider_events`` has two unique keys —
``(provider, event_id)`` and ``(provider, provider_txn_id)`` — and those two constraints, not
Redis, are what make the webhook exactly-once.

The models carry columns and ``managed = False``; nothing here writes, because the application
roles hold column-level grants (``hub_ingest`` may update ten named columns of ``top_ups`` and
three of ``provider_events``) that the ORM cannot express and would flatten.

``db_table`` is bare: the connection's ``search_path`` resolves ``hub`` (D-43).
"""

from __future__ import annotations

from django.db import models

__all__ = ["ProviderEvent", "TopUp", "WebhookQuarantine"]


class UnmanagedShim(models.Model):
    """Base of the shims: no table of its own, no migration, nothing the ORM may write."""

    class Meta:
        abstract = True
        managed = False

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.pk})"


class TopUp(UnmanagedShim):
    """``hub.top_ups``: one attempt to move money from a payer to a customer's wallet."""

    id = models.UUIDField(primary_key=True)
    customer_id = models.UUIDField()
    meter_id = models.UUIDField()
    provider = models.TextField()
    provider_ref = models.TextField(null=True)  # noqa: DJ001 - NULL and "" differ in the schema
    provider_txn_id = models.TextField(null=True)  # noqa: DJ001 - NULL and "" differ in the schema
    payer_msisdn = models.TextField(null=True)  # noqa: DJ001 - NULL and "" differ in the schema
    payer_matched = models.BooleanField(null=True)
    status_pull_at = models.DateTimeField(null=True)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    plan_tariff_id = models.UUIDField()
    tariff_minor_per_kwh = models.BigIntegerField()
    energy_wh = models.BigIntegerField()
    status = models.TextField()
    late = models.BooleanField()
    provider_event_id = models.UUIDField(null=True)
    ledger_transaction_id = models.UUIDField(null=True)
    reversal_transaction_id = models.UUIDField(null=True)
    initiated_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField()

    class Meta(UnmanagedShim.Meta):
        db_table = "top_ups"


class ProviderEvent(UnmanagedShim):
    """``hub.provider_events``: one verified delivery, stored before any money moves."""

    id = models.UUIDField(primary_key=True)
    provider = models.TextField()
    event_id = models.TextField()
    provider_txn_id = models.TextField()
    event_type = models.TextField()
    amount_minor = models.BigIntegerField(null=True)
    currency = models.CharField(max_length=3, null=True)  # noqa: DJ001 - NULL and "" differ in the schema
    payload = models.JSONField()
    status = models.TextField()
    last_error = models.TextField(null=True)  # noqa: DJ001 - NULL and "" differ in the schema
    received_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True)

    class Meta(UnmanagedShim.Meta):
        db_table = "provider_events"


class WebhookQuarantine(UnmanagedShim):
    """``hub.webhook_quarantine``: a rejected delivery, as a hash and a length — never the body."""

    id = models.UUIDField(primary_key=True)
    provider = models.TextField()
    reason = models.TextField()
    source_ip = models.GenericIPAddressField(null=True)
    body_sha256 = models.CharField(max_length=64)
    body_length = models.IntegerField()
    signature_hdr = models.TextField(null=True)  # noqa: DJ001 - NULL and "" differ in the schema
    received_at = models.DateTimeField()

    class Meta(UnmanagedShim.Meta):
        db_table = "webhook_quarantine"
