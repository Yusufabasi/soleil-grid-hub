"""Composition of the ledger module (docs/03 3.3): the only place naming its adapters.

The layers above see the Protocols in ``ledger.contracts``; this module turns them into the
reader, the writer, the service, the proposal use cases and the four approval executors, and
registers the executors and the three cron jobs at start-up. Two neighbours are reached through
a registration seam rather than an import:

* ``identity``'s maker-checker registry, which receives the executors here;
* ``payments``' top-up lookup and ``reconciliation``'s open-exception counter, both of which
  arrive later and default to an honest "nothing known" until they do.

The clock delegates to ``hub.shared.clock.current`` so a frozen clock installed by a test
freezes ledger timestamps too, exactly as ``customers.wiring`` does.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from django.conf import settings

from hub.identity.contracts import AuditLog, Principal
from hub.ledger.application.adjustments import ProposeAdjustment, ProposeRefund
from hub.ledger.application.executors import (
    AdjustmentExecutor,
    PeriodLockExecutor,
    PeriodReopenExecutor,
    RefundExecutor,
)
from hub.ledger.application.jobs import register_jobs as register_cron_jobs
from hub.ledger.application.ledger_service import LedgerServiceImpl
from hub.ledger.application.periods import ProposePeriodLock, ProposePeriodReopen
from hub.ledger.contracts import (
    ApprovalProposer,
    OpenExceptionCounter,
    ProposalReceipt,
    TopUpFacts,
    TopUpLookup,
)
from hub.ledger.infrastructure.periods import AccountingPeriodRepository
from hub.ledger.infrastructure.reader import LedgerReader
from hub.ledger.infrastructure.writer import LedgerWriter
from hub.shared import clock as kernel_clock
from hub.shared.contracts import UnitOfWork
from hub.shared.ids import Uuid7Generator
from hub.shared.ports import Clock
from hub.shared.registry import require

__all__ = [
    "adjustments",
    "approvals_adapter",
    "audit_log",
    "clock",
    "ids",
    "open_exception_counter",
    "period_locks",
    "period_reopens",
    "periods",
    "reader",
    "refunds",
    "register_approval_executors",
    "register_jobs",
    "reset",
    "service",
    "top_up_lookup",
    "unit_of_work",
    "use_open_exception_counter",
    "use_top_up_lookup",
    "writer",
]


class _KernelClock:
    """The process clock, read through the kernel's seam (a frozen clock in tests)."""

    def now(self) -> datetime:
        return kernel_clock.current()


class _NoTopUps:
    """Until `payments` registers its lookup (slice 04): no top-up is known, so none refunds."""

    def get(self, top_up_id: UUID) -> TopUpFacts | None:  # noqa: ARG002 - port shape
        return None


class _NoExceptions:
    """Until `reconciliation` registers its counter (slice 14): no exception is open."""

    def open_count(self, period_start: Any, period_end: Any) -> int:  # noqa: ARG002
        return 0


class _IdentityApprovals:
    """The propose half of `identity`'s maker-checker, adapted to this module's port."""

    def propose(
        self,
        principal: Principal,
        *,
        kind: str,
        payload: Any,
        reason: str,
        subject_id: UUID | None = None,
    ) -> ProposalReceipt:
        # `identity`'s Approval: its `document` is a method, while this port's receipt keeps the
        # document as a value, so the adaptation happens here.
        approval = cast(
            "Any",
            _approvals().propose(
                principal,
                kind=kind,
                payload=payload,
                reason=reason,
                subject_id=subject_id,
            ),
        )
        return ProposalReceipt(id=approval.id, document=approval.document())


_lock = threading.RLock()
_clock: Clock | None = None
_ids: Uuid7Generator | None = None
_audit: AuditLog | None = None
_reader: LedgerReader | None = None
_writer: LedgerWriter | None = None
_service: LedgerServiceImpl | None = None
_periods: AccountingPeriodRepository | None = None
_adjustments: ProposeAdjustment | None = None
_refunds: ProposeRefund | None = None
_locks: ProposePeriodLock | None = None
_reopens: ProposePeriodReopen | None = None

# Contributions from other modules: composition, so they survive a reset.
_top_ups: TopUpLookup = _NoTopUps()
_exceptions: OpenExceptionCounter = _NoExceptions()


# `identity` is the module below this one, and the import contract lets the ledger see its
# contracts and events only — not its composition root. The four services needed here are
# published to the kernel registry by ``identity.wiring.publish_ports`` and asked for by name
# (hub.shared.registry). Nothing in this file names an identity implementation.
APPROVALS_PORT = "identity.approvals"
AUDIT_PORT = "identity.audit"
UNIT_OF_WORK_PORT = "identity.unit_of_work"
EXECUTOR_REGISTRY_PORT = "identity.register_approval_executor"


def _approvals() -> ApprovalProposer:
    """The propose half of identity's maker-checker, resolved per call."""
    return cast("ApprovalProposer", require(APPROVALS_PORT)())


def _audit_port() -> AuditLog:
    return cast("AuditLog", require(AUDIT_PORT)())


def reset() -> None:
    """Drop every cached adapter; tests call it after changing a setting."""
    global _clock, _ids, _audit, _reader, _writer, _service, _periods  # noqa: PLW0603
    global _adjustments, _refunds, _locks, _reopens  # noqa: PLW0603
    with _lock:
        _clock = _ids = _audit = _reader = _writer = _service = _periods = None
        _adjustments = _refunds = _locks = _reopens = None


def clock() -> Clock:
    global _clock  # noqa: PLW0603
    if _clock is None:
        with _lock:
            if _clock is None:
                _clock = _KernelClock()
    return _clock


def ids() -> Uuid7Generator:
    global _ids  # noqa: PLW0603
    if _ids is None:
        with _lock:
            if _ids is None:
                _ids = Uuid7Generator(clock())
    return _ids


def audit_log() -> AuditLog:
    global _audit  # noqa: PLW0603
    if _audit is None:
        with _lock:
            if _audit is None:
                _audit = _audit_port()
    return _audit


def unit_of_work(principal: Principal | None = None) -> UnitOfWork:
    """A transaction that announces the caller, exactly as identity's own wiring does."""
    return cast("UnitOfWork", require(UNIT_OF_WORK_PORT)(principal))


def reader() -> LedgerReader:
    global _reader  # noqa: PLW0603
    if _reader is None:
        with _lock:
            if _reader is None:
                _reader = LedgerReader()
    return _reader


def writer() -> LedgerWriter:
    global _writer  # noqa: PLW0603
    if _writer is None:
        with _lock:
            if _writer is None:
                _writer = LedgerWriter()
    return _writer


def periods() -> AccountingPeriodRepository:
    global _periods  # noqa: PLW0603
    if _periods is None:
        with _lock:
            if _periods is None:
                _periods = AccountingPeriodRepository()
    return _periods


def service() -> LedgerServiceImpl:
    """The `LedgerService` later slices inject, and the executors post through."""
    global _service  # noqa: PLW0603
    if _service is None:
        with _lock:
            if _service is None:
                _service = LedgerServiceImpl(
                    clock=clock(),
                    ids=ids(),
                    writer=writer(),
                    reader=reader(),
                    low_balance_minor=int(settings.HUB_LEDGER_LOW_BALANCE_MINOR),
                )
    return _service


def approvals_adapter() -> _IdentityApprovals:
    return _IdentityApprovals()


def adjustments() -> ProposeAdjustment:
    global _adjustments  # noqa: PLW0603
    if _adjustments is None:
        with _lock:
            if _adjustments is None:
                _adjustments = ProposeAdjustment(
                    clock=clock(),
                    reader=reader(),
                    approvals=approvals_adapter(),
                    audit=audit_log(),
                    unit_of_work=unit_of_work,
                )
    return _adjustments


def refunds() -> ProposeRefund:
    global _refunds  # noqa: PLW0603
    if _refunds is None:
        with _lock:
            if _refunds is None:
                _refunds = ProposeRefund(
                    clock=clock(),
                    top_ups=top_up_lookup(),
                    approvals=approvals_adapter(),
                    audit=audit_log(),
                    unit_of_work=unit_of_work,
                )
    return _refunds


def period_locks() -> ProposePeriodLock:
    global _locks  # noqa: PLW0603
    if _locks is None:
        with _lock:
            if _locks is None:
                _locks = ProposePeriodLock(
                    clock=clock(),
                    reader=reader(),
                    exceptions=open_exception_counter(),
                    approvals=approvals_adapter(),
                    audit=audit_log(),
                    unit_of_work=unit_of_work,
                )
    return _locks


def period_reopens() -> ProposePeriodReopen:
    global _reopens  # noqa: PLW0603
    if _reopens is None:
        with _lock:
            if _reopens is None:
                _reopens = ProposePeriodReopen(
                    clock=clock(),
                    reader=reader(),
                    approvals=approvals_adapter(),
                    audit=audit_log(),
                    unit_of_work=unit_of_work,
                )
    return _reopens


def use_top_up_lookup(lookup: TopUpLookup) -> None:
    """Install the top-up lookup; ``payments`` calls this at start-up (slice 04)."""
    global _top_ups, _refunds  # noqa: PLW0603
    with _lock:
        _top_ups = lookup
        _refunds = None


def top_up_lookup() -> TopUpLookup:
    return _top_ups


def use_open_exception_counter(counter: OpenExceptionCounter) -> None:
    """Install the open-exception counter; ``reconciliation`` calls this (slice 14)."""
    global _exceptions, _locks  # noqa: PLW0603
    with _lock:
        _exceptions = counter
        _locks = None


def open_exception_counter() -> OpenExceptionCounter:
    return _exceptions


def register_approval_executors() -> None:
    """Publish the four ledger approval kinds to `identity`'s registry, once per process.

    ``add_approval_executor`` stores each as a contribution, so a registry `identity` rebuilds
    later (its wiring resets in tests) still knows the kind. An approval kind without an
    executor is refused, so losing one of these would look like a permission bug.
    """
    for executor in (
        AdjustmentExecutor(clock=clock(), service=service(), reader=reader(), audit=audit_log()),
        RefundExecutor(service=service(), audit=audit_log()),
        PeriodLockExecutor(
            clock=clock(),
            periods=periods(),
            reader=reader(),
            exceptions=open_exception_counter(),
            audit=audit_log(),
        ),
        PeriodReopenExecutor(periods=periods(), reader=reader(), audit=audit_log()),
    ):
        register_executor = require(EXECUTOR_REGISTRY_PORT)
        register_executor(
            executor.kind,
            required_permission=executor.required_permission,
            required_attribute=executor.required_attribute,
            execute=executor,
        )


def register_jobs() -> None:
    """Publish the three nightly crons to the kernel's job registry."""
    register_cron_jobs(writer=writer(), reader=reader(), unit_of_work=unit_of_work, ids=ids())
