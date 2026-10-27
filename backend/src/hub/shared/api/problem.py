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
from pathlib import Path
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
    "MethodNotAllowed",
    "exception_handler",
    "handler404",
    "handler500",
    "problem_response",
    "register_catalogue",
    "register_messages",
    "retry_after",
    "to_problem",
]

logger = logging.getLogger(__name__)


class MethodNotAllowed(DomainError):
    """405 for a plain view: the route exists but not for this method (RFC 9110 15.5.6).

    A transport concern rather than a domain one, so it lives next to the converter: a
    plain view has no DRF machinery to turn ``HttpResponseNotAllowed`` into problem+json.
    """

    code = "method_not_allowed"
    message_key = "method_not_allowed"
    http_status = 405


# 429 and 503 carry Retry-After (slice 00 contract); this is the fallback when the error
# itself carries no hint.
DEFAULT_RETRY_AFTER_SECONDS = 30
# docs/05 section 4: an operation with `x-step-up` demands an authentication at most this old.
DEFAULT_STEP_UP_MAX_AGE_SECONDS = 300

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
        "service_unavailable": "This instance cannot serve the request right now; retry as advised.",
        "method_not_allowed": "This route does not support that HTTP method.",
        "invalid_request": "The request parameters cannot be served by this operation.",
        "invalid_cursor": "The cursor is malformed or has expired; restart from the first page.",
        "invariant_violation": "An internal consistency rule was violated.",
        "internal_error": "An unexpected error occurred; quote the request id when reporting it.",
        # Authentication and authorisation codes of slice 01 (docs/05 section 5).
        "token_revoked": "This session was ended; sign in again.",
        "session_expired": "The session reached its idle or absolute limit; sign in again.",
        "refresh_token_reused": "The refresh token was already used; every session of its family was ended.",
        "csrf_check_failed": "The request was not recognised as coming from the site.",
        "step_up_required": "Confirm your identity again to perform this action.",
        "invalid_code": "The code is not valid.",
        "webauthn_required": "This account must confirm with a security key.",
        "account_not_provisioned": "This identity has no account on the platform.",
        "account_inactive": "This account is not active.",
        "mfa_required": "The identity provider did not confirm a second factor.",
        "proof_required": "Solve the proof-of-work challenge first.",
        "otp_invalid": "The code is not valid or has expired.",
        "rate_limited_phone": "Too many codes requested for this number.",
        "self_approval_forbidden": "An approval must be decided by someone other than its maker.",
        "conflict_of_interest": "You are linked to the customer this decision concerns.",
        "approval_payload_mismatch": "The payload changed since it was proposed.",
        "approval_not_pending": "This approval has already been decided or has expired.",
        # Optimistic concurrency and lifecycle codes, raised by the kernel and by more than
        # one module (customers and meters share both), so they live here once.
        "precondition_failed": "The resource changed since it was read; reload it and retry.",
        "precondition_required": "This change requires an If-Match header with the current ETag.",
        "version_conflict": (
            "Another write changed this resource at the same time; reload it and retry."
        ),
        "invalid_state_transition": (
            "The resource cannot move to that state from its current one."
        ),
        "payload_too_large": "The request body is larger than this route accepts.",
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
        "service_unavailable": "Cette instance ne peut pas traiter la requête pour le moment ; réessayez comme indiqué.",
        "method_not_allowed": "Cette route ne prend pas en charge cette méthode HTTP.",
        "invalid_request": "Les paramètres de la requête ne peuvent pas être traités par cette opération.",
        "invalid_cursor": "Le curseur est malformé ou a expiré ; reprendre à la première page.",
        "invariant_violation": "Une règle de cohérence interne a été violée.",
        "internal_error": "Une erreur inattendue s'est produite ; citez l'identifiant de requête.",
        "token_revoked": "Cette session a été terminée ; reconnectez-vous.",
        "session_expired": "La session a atteint sa limite d'inactivité ou sa durée maximale ; reconnectez-vous.",
        "refresh_token_reused": "Le jeton de rafraîchissement a déjà servi ; toutes les sessions de sa famille ont été fermées.",
        "csrf_check_failed": "La requête n'a pas été reconnue comme provenant du site.",
        "step_up_required": "Confirmez à nouveau votre identité pour effectuer cette action.",
        "invalid_code": "Le code n'est pas valide.",
        "webauthn_required": "Ce compte doit confirmer avec une clé de sécurité.",
        "account_not_provisioned": "Cette identité n'a pas de compte sur la plateforme.",
        "account_inactive": "Ce compte n'est pas actif.",
        "mfa_required": "Le fournisseur d'identité n'a pas confirmé de second facteur.",
        "proof_required": "Résolvez d'abord le défi de preuve de travail.",
        "otp_invalid": "Le code n'est pas valide ou a expiré.",
        "rate_limited_phone": "Trop de codes demandés pour ce numéro.",
        "self_approval_forbidden": "Une approbation doit être décidée par une autre personne que son auteur.",
        "conflict_of_interest": "Vous êtes lié au client concerné par cette décision.",
        "approval_payload_mismatch": "La charge utile a changé depuis la proposition.",
        "approval_not_pending": "Cette approbation a déjà été décidée ou a expiré.",
        "precondition_failed": (
            "La ressource a changé depuis sa lecture ; rechargez-la et réessayez."
        ),
        "precondition_required": (
            "Cette modification exige un en-tête If-Match contenant l'ETag actuel."
        ),
        "version_conflict": (
            "Une autre écriture a modifié cette ressource en même temps ; "
            "rechargez-la et réessayez."
        ),
        "invalid_state_transition": (
            "La ressource ne peut pas passer à cet état depuis son état actuel."
        ),
        "payload_too_large": "Le corps de la requête dépasse ce que cette route accepte.",
    },
}

# Fallback for message keys a module has not catalogued yet: one safe sentence per language.
_GENERIC_DETAIL: dict[str, str] = {
    "en": "The request could not be completed.",
    "fr": "La requête n'a pas pu être traitée.",
}


def register_messages(language: str, messages: Mapping[str, str]) -> None:
    """Merge one module's catalogue into the handler's table (docs/05 5, FR-16).

    Modules own their strings and this module owns the lookup: `hub.shared` may not import a
    business module, so each one registers its own keys from its ``ready()`` hook. A later
    registration for the same key wins, which is what lets a module correct a text without
    touching the kernel. Blank values are refused rather than allowed to blank out a message.
    """
    cleaned = {key: text for key, text in messages.items() if text and text.strip()}
    if not cleaned:
        return
    MESSAGES.setdefault(language, {}).update(cleaned)


def register_catalogue(directory: Path) -> None:
    """Register every ``<language>.json`` in ``directory`` as that language's messages.

    A malformed file raises at start-up: a catalogue that silently half-loaded would surface
    as one English sentence in a French UI, which is exactly the bug this task exists to fix.
    """
    for path in sorted(directory.glob("*.json")):
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in loaded.items()
        ):
            raise ValueError(f"{path} is not a flat object of message_key -> text")
        register_messages(path.stem, loaded)


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
    # A refused period lock names the open items it found, so the maker knows what to clear
    # before proposing again. It is the same field `AccountingPeriod` carries.
    open_exceptions = exc.details.get("open_exceptions") if isinstance(exc, DomainError) else None
    if isinstance(open_exceptions, int):
        body["open_exceptions"] = open_exceptions
    step_up = exc.details.get("step_up") if isinstance(exc, DomainError) else None
    if isinstance(step_up, dict):
        # docs/05: a step-up refusal says how old the authentication may be, so the client can
        # explain the dialog it opens rather than guessing at the window.
        body["step_up"] = step_up
    if status == 500 and not isinstance(exc, DomainError):
        # Unexpected failures are logged with the request id; the client gets none of it.
        logger.error("unhandled_exception", extra={"request_id": request_id}, exc_info=exc)
    return status, body


def auth_challenge(body: Mapping[str, Any]) -> str | None:
    """The RFC 6750 challenge a step-up refusal must carry, or None for other problems."""
    if body.get("status") != 401 or body.get("code") != "step_up_required":
        return None
    step_up = body.get("step_up")
    max_age = step_up.get("max_age") if isinstance(step_up, dict) else None
    seconds = max_age if isinstance(max_age, int) else DEFAULT_STEP_UP_MAX_AGE_SECONDS
    return f'Bearer error="insufficient_user_authentication", max_age={seconds}'


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
    challenge = auth_challenge(body)
    if challenge is not None:
        response["WWW-Authenticate"] = challenge
    return response


def exception_handler(exc: Exception, context: Mapping[str, Any]) -> Response | None:
    """DRF EXCEPTION_HANDLER: every failure leaves as problem+json (docs/03 7.7 item 6)."""
    request = context.get("request")
    instance = request.path if request is not None else ""
    # A signed-in caller's stored language wins over the header: the UI is rendering for that
    # person, not for whatever the browser happened to send (docs/05 5, FR-16).
    principal = getattr(request, "principal", None) if request is not None else None
    stored_locale = getattr(principal, "locale", None)
    accept = (
        str(stored_locale)
        if isinstance(stored_locale, str) and stored_locale
        else (request.headers.get("Accept-Language") if request is not None else None)
    )
    status, body = to_problem(
        exc,
        instance=instance,
        request_id=request_id_var.get() or "",
        accept_language=accept,
    )
    headers: dict[str, str] = {}
    seconds = retry_after(exc)
    if seconds is not None:
        headers["Retry-After"] = str(seconds)
    challenge = auth_challenge(body) or ('Bearer realm="hub-api"' if status == 401 else None)
    if challenge is not None:
        headers["WWW-Authenticate"] = challenge
    return Response(
        body,
        status=status,
        headers=headers or None,
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
