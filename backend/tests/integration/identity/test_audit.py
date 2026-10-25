"""The audit chain: one adapter, two SQL functions, and a verifier that notices tampering.

``db/schema.sql`` makes ``hub.audit_log`` append-only and unreadable for the application role:
the only ways in are ``hub.audit_append`` (which computes ``seq``, ``prev_hash`` and ``hash``
under an advisory lock) and ``hub.audit_page`` (which checks ``audit.read`` itself). These
tests prove the adapter uses exactly those doors, that the chain links row to row
(TC-FR-15-5), and that an altered row or an anchor that no longer matches the head is
reported by ``hub.verify_audit_chain`` (TC-FR-15-14).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest
from django.db import connection

from hub.identity.infrastructure.audit import HubAuditLog
from hub.shared.errors import PermissionDenied
from hub.shared.uow import DjangoUnitOfWork, PrincipalContext
from support.principals import SeededUser, seed_staff_user

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA = REPO_ROOT / "db" / "schema.sql"

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def administrator() -> SeededUser:
    return seed_staff_user("administrator")


def _lines(stream: str) -> list[tuple[int, bytes | None, bytes]]:
    """``(seq, prev_hash, hash)`` of one stream, in chain order."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT seq, prev_hash, hash FROM hub.audit_log WHERE stream = %s ORDER BY seq",
            [stream],
        )
        return [(int(seq), prev, digest) for seq, prev, digest in cursor.fetchall()]


@pytest.mark.tc("TC-FR-15-5")
def test_record_appends_chained_rows(administrator: SeededUser) -> None:
    log = HubAuditLog()

    first = log.record(
        stream="auth",
        actor=administrator.principal,
        action="auth.login",
        entity_type="user",
        entity_id=administrator.user_id,
        site_id=None,
        details={"method": "password"},
    )
    second = log.record(
        stream="auth",
        actor=administrator.principal,
        action="auth.mfa.verify",
        entity_type="user",
        entity_id=administrator.user_id,
        site_id=None,
        details={"method": "totp"},
    )

    assert isinstance(first, uuid.UUID) and isinstance(second, uuid.UUID)
    assert first != second

    lines = _lines("auth")
    assert [seq for seq, _, _ in lines] == [1, 2]
    assert lines[0][1] is None, "the first row has no predecessor"
    assert lines[1][1] == lines[0][2], "each row links to the one before it"


@pytest.mark.tc("TC-FR-15-5")
def test_record_stores_actor_and_address_without_personal_details(
    administrator: SeededUser,
) -> None:
    HubAuditLog().record(
        stream="privacy",
        actor=administrator.principal,
        action="customer.read",
        entity_type="customer",
        entity_id=None,
        site_id=None,
        details={"fields": ["masked_email"]},
        ip="203.0.113.7",
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT l.actor_type, l.actor_id, i.ip FROM hub.audit_log l "
            "JOIN hub.audit_ip i ON i.audit_id = l.id WHERE l.stream = 'privacy'"
        )
        assert cursor.fetchone() == ("user", administrator.user_id, "203.0.113.7")


def test_record_refuses_personal_data_keys(administrator: SeededUser) -> None:
    log = HubAuditLog()
    for details in (
        {"password": "hunter2"},
        {"access_token": "ey"},
        {"nested": {"phone": "+243800000000"}},
        {"Email": "a@example.test"},
        {"verification_code": "123456"},
    ):
        with pytest.raises(ValueError, match="personal"):
            log.record(
                stream="auth",
                actor=administrator.principal,
                action="auth.login",
                entity_type="user",
                entity_id=administrator.user_id,
                site_id=None,
                details=details,
            )


def test_record_keeps_outcome_out_of_the_details_it_returns(administrator: SeededUser) -> None:
    log = HubAuditLog()

    log.record(
        stream="auth",
        actor=administrator.principal,
        action="auth.login",
        entity_type="user",
        entity_id=administrator.user_id,
        site_id=None,
        details={"reason": "bad_password"},
        outcome="denied",
    )

    # The read path resolves `audit.read` for the session's user, so it runs inside a unit of
    # work that announced the administrator — exactly as the view does.
    with DjangoUnitOfWork(principal=PrincipalContext(user_id=administrator.user_id)):
        (entry,) = log.page("auth", after_seq=0, limit=10)
    assert entry.outcome == "denied"
    assert entry.details == {"reason": "bad_password"}
    assert entry.prev_hash == ""
    assert len(entry.hash) == 64


def test_page_requires_audit_read_in_the_database() -> None:
    """The second line of defence: the SQL function refuses a caller without the permission."""
    agent = seed_staff_user("support_agent")
    log = HubAuditLog()

    with pytest.raises(PermissionDenied):
        with DjangoUnitOfWork(principal=PrincipalContext(user_id=agent.user_id)):
            log.page("auth", after_seq=0, limit=10)


@pytest.mark.tc("TC-FR-15-14")
def test_app_role_cannot_update_or_delete_audit(postgres_server, administrator: SeededUser) -> None:
    HubAuditLog().record(
        stream="auth",
        actor=administrator.principal,
        action="auth.login",
        entity_type="user",
        entity_id=administrator.user_id,
        site_id=None,
        details={},
    )
    dsn = postgres_server.dsn("hub_test", user="hub_api", password="tamper-test")

    with psycopg.connect(postgres_server.dsn("hub_test"), autocommit=True) as owner:
        # The schema creates every application role NOLOGIN on purpose; this test opts one in
        # for the length of a transaction and puts it back afterwards.
        owner.execute("ALTER ROLE hub_api LOGIN PASSWORD 'tamper-test'")
    try:
        with psycopg.connect(dsn) as connection:
            for statement in (
                "UPDATE hub.audit_log SET action = 'auth.nothing'",
                "DELETE FROM hub.audit_log",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(statement)
                connection.rollback()
    finally:
        with psycopg.connect(postgres_server.dsn("hub_test"), autocommit=True) as owner:
            owner.execute("ALTER ROLE hub_api NOLOGIN")


@pytest.fixture(scope="module")
def scratch_dsn(postgres_server) -> str:
    """A throwaway database of its own, so a tampered row never touches ``hub_test``."""
    dsn: str = postgres_server.create_database("hub_audit_tamper")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
    return dsn


@pytest.mark.tc("TC-FR-15-14")
def test_tamper_detected_by_verifier(scratch_dsn: str) -> None:
    actor = uuid.uuid4()
    append = (
        "SELECT hub.audit_append('auth', 'user', %s, %s, 'user', %s, NULL, 'req_1', '{}'::jsonb)"
    )
    with psycopg.connect(scratch_dsn, autocommit=True) as connection:
        connection.execute(append, [actor, "auth.login", actor])
        connection.execute(append, [actor, "auth.logout", actor])
        assert connection.execute("SELECT hub.verify_audit_chain('auth')").fetchone() == (None,)

        # The chain trigger is ENABLE ALWAYS, so only its owner can silence it; that owner is
        # what an attacker with the migration role would have.
        connection.execute("ALTER TABLE hub.audit_log DISABLE TRIGGER trg_audit_log_immutable")
        connection.execute(
            "UPDATE hub.audit_log SET action = 'auth.neutralised' WHERE stream = 'auth' AND seq = 2"
        )
        connection.execute(
            "ALTER TABLE hub.audit_log ENABLE ALWAYS TRIGGER trg_audit_log_immutable"
        )

        assert connection.execute("SELECT hub.verify_audit_chain('auth')").fetchone() == (2,)


@pytest.mark.tc("TC-FR-15-14")
def test_anchor_mismatch_detected(postgres_server) -> None:
    stream = "anchorcheck"
    with psycopg.connect(postgres_server.dsn("hub_test"), autocommit=True) as connection:
        connection.execute(
            "SELECT hub.audit_append(%s, 'system', NULL, 'job.run', 'job', NULL, NULL, NULL, '{}'::jsonb)",
            [stream],
        )
        # An anchor whose hash is not the head it claims to pin: a truncated or rewritten tail.
        connection.execute(
            "INSERT INTO hub.audit_anchors (id, stream, seq, hash) "
            "VALUES (hub.uuid_v7(), %s, 1, %s)",
            [stream, b"\x00" * 32],
        )

        assert connection.execute("SELECT hub.verify_audit_chain(%s)", [stream]).fetchone() == (1,)
