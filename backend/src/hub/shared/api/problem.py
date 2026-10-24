"""RFC 9457 problem documents: the single error converter of the kernel.

docs/03 section 7.7 item 6: one DRF exception handler, the same converter behind the
middleware ``process_exception`` and the URLConf ``handler404``/``handler500``, so a client
sees one shape everywhere. ``detail`` is localised through ``Accept-Language`` (only the
``en`` and ``fr`` catalogues exist today, default ``en``); clients branch on ``code``, never
on ``detail`` (docs/05 section 5), and no body ever carries a stack trace, SQL or secret.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    ParseError,
    Throttled,
    ValidationError,
)
from rest_framework.exceptions import NotFound as DRFNotFound
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.response import Response

from hub.shared.context import request_id_var
from hub.shared.errors import DomainError, Unexpected

__all__ = [
    "MESSAGES",
    "exception_handler",
    "handler404",
    "handler500",
    "problem_response",
    "retry_after",
    "to_problem",
]

logger = logging.getLogger(__name__)

# 429 and 503 carry Retry-After (slice 00 contract); this is the fallback when the error
# itself carries no hint.
DEFAULT_RETRY_AFTER_SECONDS = 30

# detail text per message_key and language. Modules add their keys here (or fall back to
# the generic sentence) — the ``code`` is what clients branch on.
MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "validation_error": "The request payload failed validation.",
        "unauthenticated": "Authentication is required, or the access token has expired.",
        "forbidden": "The authenticated principal may not perform this action.",
        "not_found": "The requested resource does not exist or is not visible to you.",
        "conflict": "The request conflicts with the current state of the resource.",
        "duplicate_resource": "A resource with the same natural key already exists.",
        "rate_limited": "Too many requests; retry after the delay in Retry-After.",
        "provider_unavailable": "An upstream provider is unavailable; retry shortly.",
        "invariant_violation": "An internal consistency rule was violated.",
        "internal_error": "An unexpected error occurred; quote the request id when reporting it.",
    },
    "fr": {
        "validation_error": "Le contenu de la requête n'a pas passé la validation.",
        "unauthenticated": "L'authentification est requise, ou le jeton d'accès a expiré.",
        "forbidden": "Le principal authentifié n'est pas autorisé à effectuer cette action.",
        "not_found": "La ressource demandée n'existe pas ou n'est pas visible pour vous.",
        "conflict": "La requête entre en conflit avec l'état actuel de la ressource.",
        "duplicate_resource": "Une ressource avec la même clé naturelle existe déjà.",
        "rate_limited": "Trop de requêtes ; réessayez après le délai de Retry-After.",
        "provider_unavailable": "Un fournisseur externe est indisponible ; réessayez sous peu.",
        "invariant_violation": "Une règle de cohérence interne a été violée.",
        "internal_error": "Une erreur inattendue s'est produite ; citez l'identifiant de requête.",
    },
}

# Fallback for message keys a module has not catalogued yet: one safe sentence per language.
_GENERIC_DETAIL: dict[str, str] = {
    "en": "The request could not be completed.",
    "fr": "La requête n'a pas pu être traitée.",
}


def _language(accept_language: str | None) -> str:
    """Pick a catalogue language from an Accept-Language header; default ``en``."""
    if not accept_language:
        return "en"
    primary = accept_language.split(",", 1)[0].split(";", 1)[0].strip().lower()
    return "fr" if primary.startswith("fr") else "en"


def _detail(message_key: str, language: str) -> str:
    return (
        MESSAGES[language].get(message_key)
        or MESSAGES["en"].get(message_key)
        or _GENERIC_DETAIL[language]
    )


def _title(status: int) -> str:
    """RFC 9457: the title is the HTTP reason phrase, never localised."""
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def _validation_errors(detail: Any) -> list[dict[str, str]]:
    """Flatten DRF's error tree into ``errors[]`` of JSON pointers (docs/08, fe-00)."""
    errors: list[dict[str, str]] = []

    def walk(node: Any, pointer: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{pointer}/{key}")
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item, pointer)
        else:
            errors.append(
                {
                    "field": pointer,
                    "code": str(getattr(node, "code", "invalid")),
                    "message": str(node),
                }
            )

    walk(detail, "")
    return errors


def _classify(exc: Exception) -> tuple[int, str, str, list[dict[str, str]] | None]:
    """Map an exception to ``(status, code, message_key, errors)`` (docs/03 7.1, 7.7.6)."""
    if isinstance(exc, DomainError):
        errors = exc.details.get("errors")
        return (
            exc.http_status,
            exc.code,
            exc.message_key,
            errors if isinstance(errors, list) else None,
        )
    if isinstance(exc, ValidationError):
        return 422, "validation_error", "validation_error", _validation_errors(exc.detail)
    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        return 401, "unauthenticated", "unauthenticated", None
    if isinstance(exc, DRFPermissionDenied):
        return 403, "forbidden", "forbidden", None
    if isinstance(exc, (Http404, DRFNotFound)):
        return 404, "not_found", "not_found", None
    if isinstance(exc, Throttled):
        return 429, "rate_limited", "rate_limited", None
    if isinstance(exc, ParseError):
        return 400, "invalid_request", "invalid_request", None
    return 500, "internal_error", "internal_error", None


def retry_after(exc: Exception) -> int | None:
    """Retry-After seconds for 429/503 responses, or None for other statuses."""
    status = exc.http_status if isinstance(exc, DomainError) else getattr(exc, "status_code", None)
    if status not in (429, 503):
        return None
    hint = getattr(exc, "retry_after_seconds", None) or getattr(exc, "wait", None)
    return int(hint) if hint else DEFAULT_RETRY_AFTER_SECONDS


def to_problem(
    exc: Exception,
    *,
    instance: str,
    request_id: str,
    accept_language: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Convert any exception to ``(status, RFC 9457 body)`` — the one mapping (docs/03 7.1)."""
    status, code, message_key, errors = _classify(exc)
    language = _language(accept_language)
    body: dict[str, Any] = {
        "type": f"{settings.HUB_PROBLEM_BASE_URL.rstrip('/')}/{code}",
        "title": _title(status),
        "status": status,
        "code": code,
        "detail": _detail(message_key, language),
        "instance": instance,
        "request_id": request_id,
    }
    if errors is not None:
        body["errors"] = errors
    if status == 500 and not isinstance(exc, DomainError):
        # Unexpected failures are logged with the request id; the client gets none of it.
        logger.error("unhandled_exception", extra={"request_id": request_id}, exc_info=exc)
    return status, body


def problem_response(
    status: int,
    body: dict[str, Any],
    *,
    retry_after_seconds: int | None = None,
) -> HttpResponse:
    """Render a problem document for a plain Django view (docs/03 7.7 item 9)."""
    response = HttpResponse(
        json.dumps(body).encode("utf-8"),
        status=status,
        content_type="application/problem+json",
    )
    if retry_after_seconds is not None:
        response["Retry-After"] = str(retry_after_seconds)
    return response


def exception_handler(exc: Exception, context: Mapping[str, Any]) -> Response | None:
    """DRF EXCEPTION_HANDLER: every failure leaves as problem+json (docs/03 7.7 item 6)."""
    request = context.get("request")
    instance = request.path if request is not None else ""
    accept = request.headers.get("Accept-Language") if request is not None else None
    status, body = to_problem(
        exc,
        instance=instance,
        request_id=request_id_var.get() or "",
        accept_language=accept,
    )
    seconds = retry_after(exc)
    return Response(
        body,
        status=status,
        headers={"Retry-After": str(seconds)} if seconds is not None else None,
        content_type="application/problem+json",
    )


def handler404(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """URLConf handler: unmatched paths answer problem+json, never an HTML page."""
    status, body = to_problem(
        exception or Http404(),
        instance=request.path,
        request_id=request_id_var.get() or "",
        accept_language=request.headers.get("Accept-Language"),
    )
    return problem_response(status, body)


def handler500(request: HttpRequest) -> HttpResponse:
    """URLConf handler for failures that reached Django unconverted (docs/03 7.1).

    Django calls it without the exception, so the body can only ever carry the generic
    internal error: the detail was already logged, never returned.
    """
    status, body = to_problem(
        Unexpected(),
        instance=request.path,
        request_id=request_id_var.get() or "",
        accept_language=request.headers.get("Accept-Language"),
    )
    return problem_response(status, body)
