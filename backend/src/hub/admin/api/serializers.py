"""The transport shape of `listAuditLog`: query parameters in, `AuditEntry` documents out.

The cursor stays opaque (``api/openapi.yaml``: "from meta.next_cursor"). It carries the
sequence number of the last row of the page, base64url-encoded, and anything that does not
decode to one is refused with ``invalid_cursor`` (docs/03 7.7.5) instead of being guessed at.
Signing arrives with the generic ``CursorEnvelopePagination`` the other list operations use;
this cursor holds no state a client could gain from, only a position.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from typing import Any

from rest_framework import serializers

from hub.admin.application.list_audit_log import AuditQuery
from hub.identity.contracts import AuditEntry
from hub.shared.errors import InvalidCursor, InvalidRequest

__all__ = [
    "AuditEntryDocument",
    "AuditLogQuerySerializer",
    "decode_cursor",
    "encode_cursor",
    "entry_document",
    "parse_query",
]

# The only order `hub.audit_page` can serve: forward through the chain, oldest first.
SUPPORTED_SORTS = ("occurred_at",)


class AuditLogQuerySerializer(serializers.Serializer[Any]):
    """The query string of `GET /audit-log`, as the contract declares it."""

    stream = serializers.CharField(max_length=64, required=False)
    cursor = serializers.CharField(max_length=512, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False)
    sort = serializers.CharField(max_length=100, required=False)
    actor_id = serializers.UUIDField(required=False)
    action = serializers.CharField(max_length=100, required=False)
    entity_type = serializers.CharField(max_length=100, required=False)
    entity_id = serializers.UUIDField(required=False)
    outcome = serializers.CharField(max_length=100, required=False)
    occurred_after = serializers.DateTimeField(required=False)
    occurred_before = serializers.DateTimeField(required=False)


AuditEntryDocument = dict[str, Any]

DEFAULT_LIMIT = 25


def _errors(errors: Mapping[str, Any]) -> list[dict[str, str]]:
    """Flatten DRF's error tree into the contract's ``errors[]`` of JSON pointers."""
    flattened: list[dict[str, str]] = []
    for field, messages in errors.items():
        for message in messages if isinstance(messages, list) else [messages]:
            flattened.append({"field": f"/{field}", "code": "invalid", "message": str(message)})
    return flattened


def parse_query(params: Mapping[str, str]) -> AuditQuery:
    """Turn the query string into the use case's query, or refuse it."""
    serializer = AuditLogQuerySerializer(data=dict(params))
    if not serializer.is_valid():
        raise InvalidRequest(details={"errors": _errors(serializer.errors)})
    data = serializer.validated_data

    stream = data.get("stream")
    if not stream:
        # `hub.audit_page` is per stream and the application role cannot list streams, so an
        # unfiltered read has no read path (docs/plan/slice-01, Plan notes).
        raise InvalidRequest(
            details={"errors": [{"field": "/stream", "code": "required", "message": "required"}]}
        )

    sort = data.get("sort") or SUPPORTED_SORTS[0]
    if sort not in SUPPORTED_SORTS:
        raise InvalidRequest(
            details={
                "errors": [
                    {
                        "field": "/sort",
                        "code": "unsupported",
                        "message": "the audit read path is forward-only; use occurred_at",
                    }
                ]
            }
        )

    cursor = data.get("cursor")
    return AuditQuery(
        stream=stream,
        after_seq=decode_cursor(cursor) if cursor else 0,
        limit=int(data.get("limit") or DEFAULT_LIMIT),
        action=data.get("action"),
        actor_id=data.get("actor_id"),
        entity_type=data.get("entity_type"),
        entity_id=data.get("entity_id"),
        outcomes=frozenset(
            part.strip() for part in str(data.get("outcome") or "").split(",") if part.strip()
        ),
        occurred_after=data.get("occurred_after"),
        occurred_before=data.get("occurred_before"),
    )


def encode_cursor(after_seq: int | None) -> str | None:
    """The opaque cursor for the next page, or None on the last one."""
    if after_seq is None:
        return None
    raw = json.dumps({"s": after_seq}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int:
    """The sequence number a cursor carries; anything else is ``invalid_cursor`` (400)."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        after_seq = payload["s"]
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise InvalidCursor() from exc
    if not isinstance(after_seq, int) or isinstance(after_seq, bool) or after_seq < 0:
        raise InvalidCursor()
    return after_seq


def entry_document(entry: AuditEntry) -> AuditEntryDocument:
    """One entry as the contract's ``AuditEntry``; nullable fields are omitted when empty."""
    document: AuditEntryDocument = {
        "id": str(entry.id),
        "stream": entry.stream,
        "seq": entry.seq,
        "occurred_at": entry.occurred_at.isoformat(),
        "actor_type": entry.actor_type,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "outcome": entry.outcome,
        "details": dict(entry.details),
        "hash": entry.hash,
        "prev_hash": entry.prev_hash,
    }
    if entry.actor_id is not None:
        document["actor_id"] = str(entry.actor_id)
    if entry.entity_id is not None:
        document["entity_id"] = str(entry.entity_id)
    if entry.request_id:
        document["request_id"] = entry.request_id
    return document
