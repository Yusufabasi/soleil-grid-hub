"""Composition of the identity module (docs/03 section 3.3): the only place that names adapters.

The api and application layers depend on the Protocols in ``identity.contracts`` and never
import ``hub.identity.infrastructure``; this module is the seam that turns those Protocols
into the concrete adapters. It is a package-root module on purpose: the layer contract
(``.importlinter``, contract "Layers inside each module") covers ``api``, ``application`` and
``domain``, and wiring sits outside them, exactly like ``bootstrap`` does for the process.
"""

from __future__ import annotations

import threading

from hub.identity.contracts import AuditLog, Principal
from hub.identity.infrastructure.audit import HubAuditLog
from hub.shared.uow import DjangoUnitOfWork, PrincipalContext, UnitOfWork

__all__ = ["audit_log", "unit_of_work"]

_audit_log: AuditLog | None = None
_lock = threading.Lock()


def audit_log() -> AuditLog:
    """The process-wide audit adapter: stateless, so one instance serves every request."""
    global _audit_log  # noqa: PLW0603 - one adapter per process, like the container's ports
    if _audit_log is None:
        with _lock:
            if _audit_log is None:
                _audit_log = HubAuditLog()
    return _audit_log


def unit_of_work(principal: Principal | None = None) -> UnitOfWork:
    """A transaction that announces the caller to the database (``app.user_id``, D-22).

    The setting is transaction-local, so the policies and ``hub.audit_page`` read this
    request's identity and never the previous request's on a pooled connection (docs/04 10.9).
    """
    if principal is None:
        return DjangoUnitOfWork()
    user_id = principal.subject_id if principal.kind == "staff" else None
    return DjangoUnitOfWork(
        principal=PrincipalContext(user_id=user_id, customer_id=principal.customer_id)
    )
