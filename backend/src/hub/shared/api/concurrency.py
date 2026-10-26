"""Optimistic concurrency over HTTP (``api/openapi.yaml#IfMatch``, TC-FR-01-4).

The contract asks for a *weak* validator derived from the row's ``version``: ``W/"v3"`` says
"the same representation semantically", which is exactly what a version is, and a client
that caches and revalidates does not need byte equality.

Two failures are distinct and must not be merged:

* **428 ``precondition_required``** — no ``If-Match`` at all. A versioned resource is never
  written blind; a missing header means the caller did not read the row first.
* **412 ``precondition_failed``** — a header that names another version. The row moved on,
  or the client is editing a stale copy.

``requires_if_match`` is the view-side combinator: it reads the header, compares it with the
version the use case just loaded, and refuses before the write. The repository's own
``WHERE version = %s`` is the second layer, for two writers that pass this check together.
"""

from __future__ import annotations

import re
from typing import Any

from rest_framework.request import Request

from hub.shared.errors import Conflict, DomainError

__all__ = ["PreconditionFailed", "PreconditionRequired", "etag_for", "require_if_match"]

_ETAG = re.compile(r'^(?:W/)?"v(?P<version>[0-9]+)"$')


class PreconditionFailed(Conflict):
    """``If-Match`` named a version the row no longer has: 412 (not 409)."""

    code = "precondition_failed"
    message_key = "precondition_failed"
    http_status = 412


class PreconditionRequired(DomainError):
    """A versioned write without ``If-Match``: 428."""

    code = "precondition_required"
    message_key = "precondition_required"
    http_status = 428


def etag_for(version: int) -> str:
    """The weak validator for a row at ``version``."""
    return f'W/"v{int(version)}"'


def require_if_match(request: Request, current_version: int) -> None:
    """Refuse unless the request carries ``If-Match`` naming ``current_version``.

    ``*`` is refused too: the contract's validator is a version, and a client that has not
    read the row cannot claim any version at all.
    """
    header: Any = request.headers.get("If-Match")
    if header is None or not str(header).strip():
        raise PreconditionRequired()
    match = _ETAG.fullmatch(str(header).strip())
    if match is None or int(match.group("version")) != int(current_version):
        raise PreconditionFailed()
