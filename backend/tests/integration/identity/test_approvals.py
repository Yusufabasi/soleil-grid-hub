"""Maker-checker end to end: four eyes, one payload, one transaction (D-15).

The executors here are fakes on purpose — what is under test is the *service* around them: who
may propose, who may decide, what the executor is handed, and what happens when it fails. The
real executors arrive with their own slices; the registry is the seam that lets a decision be
driven without a ledger, and the parametrised test walks all eleven schema actions through it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from django.db import connection
from django.test import Client

from hub.identity import wiring
from hub.identity.application.approvals import KIND_TO_ACTION
from hub.shared import clock as kernel_clock
from hub.shared.clock import FrozenClock
from support.login import seed_local_user

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

APPROVALS = "/api/v1/approvals"
NOW = datetime(2026, 10, 24, 9, 0, 15, tzinfo=UTC)
SCHEMA_KINDS = sorted(KIND_TO_ACTION)


@dataclass
class FakeExecutor:
    """Records what it was handed; optionally fails, which must roll the decision back."""

    kind: str
    required_permission: str = "user.admin"
    required_attribute: str | None = None
    fail: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def execute(self, payload: Any, principal: Any, uow: Any) -> dict[str, Any] | None:
        self.calls.append(dict(payload))
        if self.fail:
            raise RuntimeError("executor exploded")
        return {"applied": True}


@pytest.fixture
def client(redis_store) -> Client:  # noqa: ANN001 - the fixture's job is the store
    return Client()


@pytest.fixture
def clock() -> Iterator[FrozenClock]:
    """A frozen clock, removed again so the next module starts on the system clock."""
    frozen = FrozenClock(NOW)
    wiring.use_clock(frozen)
    yield frozen
    wiring.reset()
    kernel_clock.use_clock(None)


@pytest.fixture
def executors(clock: FrozenClock) -> dict[str, FakeExecutor]:
    """A registry with every schema kind executable by an administrator."""
    wiring.reset()
    wiring.use_clock(clock)
    registered: dict[str, FakeExecutor] = {}
    for kind in SCHEMA_KINDS:
        executor = FakeExecutor(kind=kind)
        registered[kind] = executor
        wiring.approval_registry().register(
            kind,
            required_permission=executor.required_permission,
            required_attribute=executor.required_attribute,
            execute=executor.execute,
        )
    return registered


def token_for(user: Any, clock: FrozenClock, session_id: UUID | None = None) -> str:
    return wiring.tokens().issue(
        user.principal,
        session_id=session_id or uuid.uuid4(),
        auth_time=clock.now(),
        acr="mfa",
    )


def propose(
    client: Client,
    token: str,
    *,
    kind: str = "user_creation",
    payload: dict[str, Any] | None = None,
    subject_id: UUID | None = None,
) -> Any:
    body: dict[str, Any] = {
        "kind": kind,
        "payload": payload or {"reason": "Cover for the on-call engineer"},
        "reason": "Cover for the on-call engineer",
    }
    if subject_id is not None:
        body["subject_id"] = str(subject_id)
    return client.post(
        APPROVALS,
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def approve(client: Client, token: str, approval_id: str, **body: Any) -> Any:
    return client.post(
        f"{APPROVALS}/{approval_id}/approve",
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def reject(client: Client, token: str, approval_id: str, **body: Any) -> Any:
    return client.post(
        f"{APPROVALS}/{approval_id}/reject",
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def get(client: Client, token: str, approval_id: str) -> Any:
    return client.get(f"{APPROVALS}/{approval_id}", HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.mark.tc("TC-FR-15-7")
def test_maker_cannot_approve_own(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor]
) -> None:
    maker = seed_local_user("administrator")
    created = propose(client, token_for(maker, clock))
    assert created.status_code == 202, created.content
    approval_id = created.json()["data"]["id"]

    refused = approve(client, token_for(maker, clock), approval_id)

    assert refused.status_code == 403
    assert refused.json()["code"] == "self_approval_forbidden"
    assert executors["user_creation"].calls == []
    assert get(client, token_for(maker, clock), approval_id).json()["data"]["status"] == "pending"


@pytest.mark.tc("TC-FR-15-7")
def test_maker_second_session_refused(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor]
) -> None:
    maker = seed_local_user("administrator")
    created = propose(client, token_for(maker, clock, session_id=uuid.uuid4()))
    approval_id = created.json()["data"]["id"]

    refused = approve(client, token_for(maker, clock, session_id=uuid.uuid4()), approval_id)

    assert refused.status_code == 403
    assert refused.json()["code"] == "self_approval_forbidden"


@pytest.mark.tc("TC-FR-15-7")
def test_payload_hash_mismatch_409(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor]
) -> None:
    maker = seed_local_user("administrator")
    approver = seed_local_user("administrator")
    created = propose(client, token_for(maker, clock))
    approval_id = created.json()["data"]["id"]

    refused = approve(client, token_for(approver, clock), approval_id, payload_hash="0" * 64)

    assert refused.status_code == 409
    assert refused.json()["code"] == "approval_payload_mismatch"
    assert executors["user_creation"].calls == []


@pytest.mark.tc("TC-FR-15-7")
def test_executor_receives_stored_payload_not_client_body(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor]
) -> None:
    maker = seed_local_user("administrator")
    approver = seed_local_user("administrator")
    stored = {"reason": "Cover for the on-call engineer", "roles": ["support_agent"]}
    created = propose(client, token_for(maker, clock), payload=stored)
    approval_id = created.json()["data"]["id"]

    approved = approve(
        client,
        token_for(approver, clock),
        approval_id,
        payload={"reason": "something else entirely"},
    )

    assert approved.status_code == 200, approved.content
    assert executors["user_creation"].calls == [stored]
    assert approved.json()["data"]["status"] == "executed"
    assert approved.json()["data"]["result"] == {"applied": True}


@pytest.mark.tc("TC-FR-15-7")
def test_failed_execution_leaves_approval_pending(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor]
) -> None:
    executors["user_creation"].fail = True
    maker = seed_local_user("administrator")
    approver = seed_local_user("administrator")
    created = propose(client, token_for(maker, clock))
    approval_id = created.json()["data"]["id"]

    # The executor's failure is a bug, not a client error: it surfaces as a 500 to the approver.
    # Django's test client re-raises view exceptions by default, so this one asks for the response.
    failing_client = Client(raise_request_exception=False)
    failed = approve(failing_client, token_for(approver, clock), approval_id)

    assert failed.status_code == 500
    assert executors["user_creation"].calls  # it ran, and then the transaction was rolled back
    assert get(client, token_for(maker, clock), approval_id).json()["data"]["status"] == "pending"


@pytest.mark.tc("TC-FR-15-8")
def test_conflict_of_interest_blocks_approver_linked_to_customer(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor]
) -> None:
    customer_id = seed_customer()
    maker = seed_local_user("administrator")
    approver = seed_local_user("administrator")
    link_staff(approver.user_id, customer_id)
    created = propose(client, token_for(maker, clock), subject_id=customer_id)
    approval_id = created.json()["data"]["id"]

    refused = approve(client, token_for(approver, clock), approval_id)

    assert refused.status_code == 403
    assert refused.json()["code"] == "conflict_of_interest"
    assert executors["user_creation"].calls == []


@pytest.mark.tc("TC-FR-15-7")
def test_expired_approval_cannot_be_approved(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor]
) -> None:
    maker = seed_local_user("administrator")
    approver = seed_local_user("administrator")
    created = propose(client, token_for(maker, clock))
    approval_id = created.json()["data"]["id"]

    clock.advance(timedelta(hours=73))
    refused = approve(client, token_for(approver, clock), approval_id)

    assert refused.status_code == 409
    assert refused.json()["code"] == "approval_not_pending"
    assert get(client, token_for(maker, clock), approval_id).json()["data"]["status"] == "expired"


@pytest.mark.tc("TC-FR-15-7")
def test_approver_without_required_permission_or_attribute_403(
    client: Client, clock: FrozenClock
) -> None:
    wiring.reset()
    wiring.use_clock(clock)
    wiring.approval_registry().register(
        "manual_adjustment",
        required_permission="ledger.adjust",
        required_attribute="finance_lead",
        execute=lambda payload, principal, uow: None,
    )
    maker = seed_local_user("finance_officer")
    created = propose(
        client,
        token_for(maker, clock),
        kind="manual_adjustment",
        payload={"reason": "Goodwill after a failed token"},
    )
    assert created.status_code == 202, created.content
    approval_id = created.json()["data"]["id"]

    # A finance officer without the attribute may propose but not decide.
    without_attribute = seed_local_user("finance_officer")
    refused = approve(client, token_for(without_attribute, clock), approval_id)
    assert refused.status_code == 403

    # With the attribute, and a different person, it goes through.
    lead = seed_local_user("finance_officer", attributes=("finance_lead",))
    allowed = approve(client, token_for(lead, clock), approval_id)
    assert allowed.status_code == 200, allowed.content


@pytest.mark.tc("TC-FR-15-8")
def test_list_shows_only_kinds_caller_may_decide(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor]
) -> None:
    wiring.approval_registry().register(
        "manual_adjustment",
        required_permission="ledger.adjust",
        required_attribute=None,
        execute=lambda payload, principal, uow: None,
    )
    admin = seed_local_user("administrator")
    finance = seed_local_user("finance_officer")
    propose(client, token_for(admin, clock), kind="user_creation")
    propose(
        client,
        token_for(finance, clock),
        kind="manual_adjustment",
        payload={"reason": "Goodwill after a failed token"},
    )

    listed = client.get(APPROVALS, HTTP_AUTHORIZATION=f"Bearer {token_for(finance, clock)}")

    assert listed.status_code == 200
    kinds = {row["kind"] for row in listed.json()["data"]}
    assert kinds == {"manual_adjustment"}


@pytest.mark.tc("TC-FR-15-7")
@pytest.mark.parametrize("kind", SCHEMA_KINDS)
def test_every_action_requires_second_person(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor], kind: str
) -> None:
    maker = seed_local_user("administrator")
    other = seed_local_user("administrator")
    created = propose(client, token_for(maker, clock), kind=kind)
    assert created.status_code == 202, created.content
    approval_id = created.json()["data"]["id"]

    refused = approve(client, token_for(maker, clock), approval_id)
    assert refused.status_code == 403
    assert refused.json()["code"] == "self_approval_forbidden"

    allowed = approve(client, token_for(other, clock), approval_id)
    assert allowed.status_code == 200, allowed.content
    assert allowed.json()["data"]["status"] == "executed"


def test_reject_closes_the_proposal(
    client: Client, clock: FrozenClock, executors: dict[str, FakeExecutor]
) -> None:
    maker = seed_local_user("administrator")
    other = seed_local_user("administrator")
    created = propose(client, token_for(maker, clock))
    approval_id = created.json()["data"]["id"]

    refused = reject(client, token_for(other, clock), approval_id, note="Not enough context")

    assert refused.status_code == 200, refused.content
    assert refused.json()["data"]["status"] == "rejected"
    assert executors["user_creation"].calls == []
    again = approve(client, token_for(other, clock), approval_id)
    assert again.status_code == 409


def test_proposing_a_kind_this_caller_may_not_propose_is_refused(
    client: Client, clock: FrozenClock
) -> None:
    """No database action yet, or no permission for the kind: refused, never stored."""
    wiring.reset()
    admin = seed_local_user("administrator")
    support = seed_local_user("support_agent")

    # A contract kind whose database action arrives with a later slice: the body is not accepted.
    unmapped = propose(client, token_for(admin, clock), kind="period_lock")
    assert unmapped.status_code == 422

    # A kind this build can execute, proposed by somebody without the permission it needs.
    unpermitted = propose(client, token_for(support, clock), kind="user_creation")
    assert unpermitted.status_code == 403
    assert unpermitted.json()["code"] == "forbidden"


def seed_customer() -> UUID:
    """A customer row: the conflict check needs a real ``hub.customers`` id to link to."""
    site_id = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0101")
    plan_id = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0102")
    customer_id = UUID("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0103")
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.sites (id, code, name, currency) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            [site_id, "SITE-C", "Site C", "USD"],
        )
        cursor.execute(
            "INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [plan_id, "PLAN-C", "Plan C", "USD", 100, 100000],
        )
        cursor.execute(
            "INSERT INTO hub.customers (id, site_id, plan_id, full_name) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [customer_id, site_id, plan_id, "Customer C"],
        )
    return customer_id


def link_staff(user_id: UUID, customer_id: UUID) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO hub.staff_customer_link (user_id, customer_id, relation) "
            "VALUES (%s, %s, 'relative') ON CONFLICT DO NOTHING",
            [user_id, customer_id],
        )
