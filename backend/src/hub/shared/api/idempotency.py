"""The idempotency seam for a POST that must not happen twice (docs/05 4, D-12).

A client that retries a timed-out request may only ever cause one effect. The row in
``hub.idempotency_keys`` is the arbiter: ``(scope, idem_key)`` is the primary key, so the
reservation itself is the lock. The first request stores its response body; a replay with the
same body answers from that row (``Idempotent-Replayed: true``), a replay with a different
body is a 422, and a retry while the first one is still running is a 409 rather than a second
attempt.

The window is the database's (``hub.idempotency_window``: 24 hours for everything but top-up
initiation, which gets 7 days), so a client cannot choose how long a key lives. Scope names the
route and, when there is a caller, who called it: two customers reusing the same key must not
replay each other's answer.

The handler runs *inside* the reservation's transaction, so the row and the business write
commit or roll back together. A crash between them leaves the row reserved until
``locked_until``, after which the key may be retried — which is the documented behaviour of a
stuck in-progress key, and cheaper than a distributed lock.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import connections, transaction
from psycopg.types.json import Jsonb
from rest_framework.request import Request
from rest_framework.response import Response

from hub.shared.clock import current
from hub.shared.errors import Conflict, InvalidRequest, ValidationFailed

__all__ = ["IdempotentOutcome", "idempotent_response", "replay_or_run", "scope_for"]

# One request's reservation is held for this long before another attempt may take it over.
IN_PROGRESS_SECONDS = 30


@dataclass(frozen=True, slots=True)
class IdempotentOutcome:
    """What the handler answered, and whether this call was the replay of an earlier one."""

    status: int
    body: Mapping[str, Any]
    key: str | None
    replayed: bool = False


def scope_for(request: Request, route: str) -> str:
    """``POST /api/v1/<route>:<principal>``: one namespace per caller per operation."""
    principal = getattr(request, "principal", None)
    subject = getattr(principal, "subject_id", None)
    return (
        f"POST /api/v1/{route.strip('/')}:{subject}"
        if subject
        else f"POST /api/v1/{route.strip('/')}"
    )


def _key(request: Request) -> str | None:
    raw = request.headers.get("Idempotency-Key")
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        raise InvalidRequest(code="idempotency_key_required")
    return key


def _request_hash(body: Any) -> str:
    """SHA-256 of the canonical JSON body: whitespace and key order never change the hash."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def replay_or_run(
    request: Request,
    *,
    scope: str,
    run: Callable[[], tuple[int, Mapping[str, Any]]],
    subject_customer_id: UUID | None = None,
) -> IdempotentOutcome:
    """Reserve the key, run the handler once, and answer a replay from the stored row."""
    key = _key(request)
    if key is None:
        status, body = run()
        return IdempotentOutcome(status=status, body=body, key=None)

    request_hash = _request_hash(getattr(request, "data", {}))
    now = current()
    with transaction.atomic():
        replay = _reserve(scope, key, request_hash, now, subject_customer_id)
        if replay is not None:
            return replay
        status, body = run()
        _complete(scope, key, status, body, now)
    return IdempotentOutcome(status=status, body=body, key=key)


def _reserve(
    scope: str,
    key: str,
    request_hash: str,
    now: datetime,
    subject_customer_id: UUID | None,
) -> IdempotentOutcome | None:
    """Take the key, or explain why this call may not run. ``None`` means: run it."""
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT request_hash, response_status, response_body, locked_until, expires_at"
            "  FROM hub.idempotency_keys"
            " WHERE scope = %s AND idem_key = %s"
            "   FOR UPDATE",
            [scope, key],
        )
        row = cursor.fetchone()

        if row is not None:
            stored_hash, response_status, response_body, locked_until, expires_at = row
            expired = expires_at <= now
            if not expired:
                if stored_hash != request_hash:
                    raise ValidationFailed(
                        code="idempotency_key_reuse",
                        details={"idempotency_key": key},
                    )
                if response_status is not None:
                    return IdempotentOutcome(
                        status=int(response_status),
                        body=_body(response_body),
                        key=key,
                        replayed=True,
                    )
                if locked_until is not None and locked_until > now:
                    raise Conflict(code="idempotency_in_progress")
            # Expired, or an abandoned reservation: take the key over.
            cursor.execute(
                "UPDATE hub.idempotency_keys"
                "   SET request_hash = %s, response_status = NULL, response_body = NULL,"
                "       locked_until = %s, created_at = %s, expires_at = %s,"
                "       subject_customer_id = %s"
                " WHERE scope = %s AND idem_key = %s",
                [
                    request_hash,
                    now + timedelta(seconds=IN_PROGRESS_SECONDS),
                    now,
                    now + timedelta(days=1),
                    subject_customer_id,
                    scope,
                    key,
                ],
            )
            return None

        cursor.execute(
            "INSERT INTO hub.idempotency_keys"
            " (scope, idem_key, request_hash, subject_customer_id, locked_until, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [
                scope,
                key,
                request_hash,
                subject_customer_id,
                now + timedelta(seconds=IN_PROGRESS_SECONDS),
                now,
            ],
        )
    return None


def _complete(scope: str, key: str, status: int, body: Mapping[str, Any], now: datetime) -> None:
    """Store the answer so the next identical call is a replay, not a second effect."""
    params: list[Any] = [status, Jsonb(dict(body)), scope, key]
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "UPDATE hub.idempotency_keys"
            "   SET response_status = %s, response_body = %s, locked_until = NULL"
            " WHERE scope = %s AND idem_key = %s",
            params,
        )


def _body(stored: Any) -> Mapping[str, Any]:
    if isinstance(stored, Mapping):
        return dict(stored)
    parsed = json.loads(str(stored))
    return parsed if isinstance(parsed, Mapping) else {}


def idempotent_response(outcome: IdempotentOutcome) -> Response:
    """The DRF response, carrying the two headers the contract promises."""
    response = Response(dict(outcome.body), status=outcome.status)
    if outcome.key is not None:
        response["Idempotency-Key"] = outcome.key
        response["Idempotent-Replayed"] = "true" if outcome.replayed else "false"
    return response
