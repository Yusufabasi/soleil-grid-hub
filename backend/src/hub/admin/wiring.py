"""Composition of the admin module (docs/03 section 3.3).

The use case needs identity's audit adapter and a transaction that announces the caller;
both come from ``hub.identity.wiring``, its public composition seam, so this module never
names another module's infrastructure and `admin` still only reads identity's contracts at
runtime (module direction, docs/03 3.1).
"""

from __future__ import annotations

from hub.admin.application.list_audit_log import ListAuditLog
from hub.identity import wiring as identity_wiring

__all__ = ["list_audit_log"]


def list_audit_log() -> ListAuditLog:
    """The `listAuditLog` use case, wired to the real adapter and unit of work."""
    return ListAuditLog(
        audit_log=identity_wiring.audit_log(),
        unit_of_work=identity_wiring.unit_of_work,
    )
