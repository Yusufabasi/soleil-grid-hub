"""Routes of the admin module. The URL name is the ``operationId`` (docs/03 7.7)."""

from typing import Any

from django.urls import path

from hub.admin.api.views import AuditLogView

__all__ = ["urlpatterns"]

urlpatterns: list[Any] = [
    path("audit-log", AuditLogView.as_view(), name="listAuditLog"),
]
