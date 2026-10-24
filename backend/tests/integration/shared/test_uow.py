"""The unit of work and the outbox writer (docs/03 2.1, docs/04 10.9).

The outbox row must be written inside the same transaction as the business change, or not
at all: a rollback discards staged events, a clean exit writes them atomically, and a
savepoint that fails takes only its own events with it. The principal context is
transaction-local, so a pooled connection can never leak one request's identity into the
next (docs/04 10.9).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from django.conf import settings
from django.db import connection

from hub.shared.events import DomainEvent
from hub.shared.uow import DjangoUnitOfWork, PrincipalContext

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _event(name: str = "payment.confirmed", **payload: Any) -> DomainEvent:
    return DomainEvent(
        event_id=uuid.uuid4(),
        name=name,
        version=1,
        occurred_at=datetime.now(UTC),
        correlation_id=uuid.uuid4(),
        causation_id=None,
        aggregate_type="payment",
        aggregate_id=uuid.uuid4(),
        payload=payload,
    )


def _rows() -> list[tuple[Any, Any, Any]]:
    """Every outbox row; the payload comes back as jsonb text through Django's cursor."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, event_type, payload FROM hub.outbox ORDER BY id")
        return [
            (row[0], row[1], json.loads(row[2]) if isinstance(row[2], str) else row[2])
            for row in cursor.fetchall()
        ]


def test_outbox_rows_roll_back_with_the_transaction() -> None:
    event = _event()

    with pytest.raises(RuntimeError), DjangoUnitOfWork() as uow:
        uow.outbox.add(event)
        raise RuntimeError("use case failed after staging the event")

    assert _rows() == []


def test_clean_exit_commits_outbox_row() -> None:
    event = _event(top_up_id=str(uuid.uuid4()))

    with DjangoUnitOfWork() as uow:
        uow.outbox.add(event)

    rows = _rows()
    assert len(rows) == 1
    row_id, event_type, payload = rows[0]
    assert row_id == event.event_id
    assert event_type == "payment.confirmed"
    for key in (
        "event_id",
        "name",
        "version",
        "occurred_at",
        "correlation_id",
        "causation_id",
        "aggregate_id",
    ):
        assert key in payload, key
    assert payload["event_id"] == str(event.event_id)
    assert payload["name"] == "payment.confirmed"
    assert payload["top_up_id"] == event.payload["top_up_id"]


def test_require_active_outside_transaction_raises() -> None:
    with pytest.raises(RuntimeError):
        DjangoUnitOfWork().require_active()


def test_savepoint_partial_rollback() -> None:
    kept = _event()
    discarded = _event(name="payment.failed")

    with DjangoUnitOfWork() as uow:
        uow.outbox.add(kept)
        with pytest.raises(RuntimeError):
            with uow.savepoint():
                uow.outbox.add(discarded)
                raise RuntimeError("inner step failed")

    assert [row[0] for row in _rows()] == [kept.event_id]


def test_on_commit_runs_after_commit_not_after_rollback(
    django_capture_on_commit_callbacks: Any,
) -> None:
    calls: list[str] = []

    with django_capture_on_commit_callbacks(execute=True):
        with DjangoUnitOfWork() as uow:
            uow.on_commit(lambda: calls.append("committed"))
        assert calls == [], "the callback must wait for the transaction to end"

        with pytest.raises(RuntimeError):
            with DjangoUnitOfWork() as rolled_back:
                rolled_back.on_commit(lambda: calls.append("rolled back"))
                raise RuntimeError("aborted")

    assert calls == ["committed"]


@pytest.mark.django_db(transaction=True)
def test_principal_context_is_transaction_local() -> None:
    principal = PrincipalContext(user_id=uuid.uuid4(), customer_id=None)
    second = PrincipalContext(user_id=uuid.uuid4(), customer_id=uuid.uuid4())

    with DjangoUnitOfWork(principal=principal) as uow:
        uow.require_active()
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.user_id', true)")
            assert cursor.fetchone()[0] == str(principal.user_id)

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.user_id', true)")
        leftover = cursor.fetchone()[0]
    assert leftover in (None, ""), "the principal must not survive the transaction"

    with DjangoUnitOfWork(principal=second):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('app.user_id', true), "
                "current_setting('app.customer_id', true)"
            )
            user_id, customer_id = cursor.fetchone()
    assert user_id == str(second.user_id)
    assert customer_id == str(second.customer_id)

    with DjangoUnitOfWork():  # no principal: nothing is set, nothing leaks in
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.user_id', true)")
            assert cursor.fetchone()[0] in (None, "")


def test_atomic_requests_not_used() -> None:
    assert settings.DATABASES[connection.alias]["ATOMIC_REQUESTS"] is False
    assert connection.settings_dict["ATOMIC_REQUESTS"] is False
