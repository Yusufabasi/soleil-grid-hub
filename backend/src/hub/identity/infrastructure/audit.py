"""The audit adapter: the two SECURITY DEFINER functions of ``db/schema.sql``, nothing else.

The application roles have no privilege at all on ``hub.audit_log`` (no SELECT, INSERT,
UPDATE or DELETE), so this module cannot read or write the table even if someone tried:
``hub.audit_append`` computes ``seq``, ``prev_hash`` and ``hash`` under an advisory lock, and
``hub.audit_page`` checks ``audit.read`` against the session's user before returning rows.

Two rules are enforced here rather than in SQL, because they are about what a caller may say:

* ``details`` holds ids and field names, never a personal value or a credential. A key that
  mentions a password, a code, a token, a phone number or an email is refused outright — the
  table is append-only, so a value written by mistake can never be taken back.
* ``outcome`` is carried inside ``details``. ``hub.audit_log`` has no column for it, and the
  contract (`AuditEntry`) requires it; the read path lifts it back out, so callers of this
  adapter keep the signature the plan describes.

``record`` must be the last call in a unit of work: the advisory lock ``audit_append`` takes
is held to commit, and that is what makes one stream's chain dense and gap-free.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

from django.db import DatabaseError, connection
from psycopg.types.json import Jsonb

from hub.identity.contracts import AuditEntry, AuditLog, AuditOutcome, Principal
from hub.shared.context import request_id_var
from hub.shared.db import call_function
from hub.shared.errors import PermissionDenied

__all__ = ["HubAuditLog"]

# A key containing any of these would put a personal value or a credential into a table that
# can never be corrected. Matching is on substrings, so `password_hash`, `access_token` and
# `phone_hmac` are refused too.
FORBIDDEN_DETAIL_FRAGMENTS = ("password", "code", "token", "phone", "email")

# `ck_audit_log_actor` allows five actor types; a principal kind maps onto them.
ACTOR_TYPE_BY_KIND: dict[str, str] = {
    "staff": "user",
    "customer": "customer",
    "provider": "api_key",
    "gateway": "api_key",
    "integrator": "api_key",
    "public": "system",
}

# hub.audit_append(p_stream, p_actor_type, p_actor_id, p_action, p_entity_type, p_entity_id,
#                  p_site_id, p_request_id, p_details, p_ip, p_subject_customer)
_APPEND_CASTS = (
    "text",
    "text",
    "uuid",
    "text",
    "text",
    "uuid",
    "uuid",
    "text",
    "jsonb",
    "inet",
    "uuid",
)

# SQLSTATE for "permission denied" (docs/04 10.8: `audit_page` raises it for `audit.read`).
INSUFFICIENT_PRIVILEGE = "42501"

# Column order of hub.audit_page(); spelled out so a schema change cannot reorder the mapping.
_PAGE_SQL = """
    SELECT id, stream, seq, occurred_at, actor_type, actor_id, action, entity_type, entity_id,
           request_id, details, prev_hash, hash
      FROM hub.audit_page(%s, %s, %s)
"""


def _assert_no_personal_keys(details: Mapping[str, Any], *, path: str = "") -> None:
    """Refuse a ``details`` tree whose keys name personal data or a credential."""
    for key, value in details.items():
        lowered = str(key).lower()
        for fragment in FORBIDDEN_DETAIL_FRAGMENTS:
            if fragment in lowered:
                raise ValueError(
                    f"audit details may not carry personal data: {path}/{key} names a {fragment}"
                )
        if isinstance(value, Mapping):
            _assert_no_personal_keys(cast("Mapping[str, Any]", value), path=f"{path}/{key}")


def _actor_type(actor: Principal | None) -> str:
    if actor is None:
        return "system"
    return ACTOR_TYPE_BY_KIND.get(actor.kind, "system")


def _actor_id(actor: Principal | None) -> UUID | None:
    """The row the actor names: a user, a customer or an API key. ``system`` names none."""
    if actor is None or actor.kind == "public":
        return None
    return actor.subject_id


def _sqlstate(exc: BaseException) -> str | None:
    """The SQLSTATE behind a Django ``DatabaseError``, which wraps the psycopg error."""
    cause: BaseException | None = exc
    while cause is not None:
        state = getattr(cause, "sqlstate", None)
        if isinstance(state, str):
            return state
        cause = cause.__cause__
    return None


def _details(value: Any) -> dict[str, Any]:
    """A jsonb column as a dict.

    Django's psycopg backend registers a text loader for ``jsonb`` (so its own JSONField can
    apply a custom decoder), so a raw cursor sees the JSON text and has to parse it.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError(f"audit details is not an object: {decoded!r}")
        return decoded
    return dict(cast("Mapping[str, Any]", value))


def _entry(row: Sequence[Any]) -> AuditEntry:
    """Map one ``hub.audit_page`` row onto the contract's ``AuditEntry``."""
    details = _details(row[10])
    outcome = str(details.pop("outcome", "success"))
    return AuditEntry(
        id=cast("UUID", row[0]),
        stream=str(row[1]),
        seq=int(row[2]),
        occurred_at=cast("Any", row[3]),
        actor_type=str(row[4]),
        actor_id=cast("UUID | None", row[5]),
        action=str(row[6]),
        entity_type=str(row[7]),
        entity_id=cast("UUID | None", row[8]),
        outcome=outcome,
        request_id=cast("str | None", row[9]),
        details=details,
        prev_hash=bytes(row[11]).hex() if row[11] is not None else "",
        hash=bytes(row[12]).hex(),
    )


class HubAuditLog(AuditLog):
    """``AuditLog`` over ``hub.audit_append`` and ``hub.audit_page``."""

    def record(
        self,
        *,
        stream: str,
        actor: Principal | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        site_id: UUID | None,
        details: Mapping[str, Any],
        outcome: AuditOutcome = "success",
        ip: str | None = None,
        subject_customer_id: UUID | None = None,
    ) -> UUID:
        """Append one chained row; the advisory lock it takes lives until the commit."""
        _assert_no_personal_keys(details)
        payload: dict[str, Any] = {**details, "outcome": outcome}
        row = call_function(
            "audit_append",
            stream,
            _actor_type(actor),
            _actor_id(actor),
            action,
            entity_type,
            entity_id,
            site_id,
            request_id_var.get(),
            Jsonb(payload),
            ip,
            subject_customer_id,
            casts=_APPEND_CASTS,
        )
        if row is None:  # pragma: no cover - a SECURITY DEFINER function always returns the id
            raise RuntimeError("hub.audit_append returned no id")
        return cast("UUID", row[0])

    def page(self, stream: str, *, after_seq: int, limit: int) -> list[AuditEntry]:
        """Rows of ``stream`` with ``seq > after_seq``, oldest first.

        Must run inside a unit of work that announced the reader (``app.user_id``): the
        database resolves ``audit.read`` from that transaction-local setting, and a caller
        without it is refused here rather than being allowed to see an empty page and
        believe otherwise.
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute(_PAGE_SQL, [stream, after_seq, limit])
                rows = cursor.fetchall()
        except DatabaseError as exc:
            if _sqlstate(exc) != INSUFFICIENT_PRIVILEGE:
                raise
            raise PermissionDenied(details={"permission": "audit.read"}) from exc
        return [_entry(row) for row in rows]
