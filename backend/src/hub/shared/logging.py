"""Structured logging with redaction (docs/03 section 7.4, docs/09 section 6).

JSON to stdout for every logger, stdlib included, through one structlog
``ProcessorFormatter``. Two lines of defence before a record leaves the process: a
deny-list of key names, then value patterns (JWT, API key, MSISDN, e-mail) and the query
string of a logged URL, because a stream ticket must never reach a log sink (D-18).
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
import traceback
from typing import Any

import structlog
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from structlog.stdlib import ProcessorFormatter
from structlog.typing import EventDict, WrappedLogger

from hub.shared.context import correlation_id_var, entrypoint_var, request_id_var
from hub.shared.tracing import add_trace_id

__all__ = ["DENY_KEYS", "REDACTED", "configure_logging", "redact_processor", "redact_text"]

REDACTED = "[redacted]"

# Never logged, by key name (docs/03 7.4 "Never log"). Matched as substrings of the key so
# that variants such as `password_hash`, `totp` or `token_count` are masked too.
DENY_KEYS = frozenset(
    {
        "password",
        "otp",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "secret",
        "signature",
        "x_hub_signature",
        "body",
        "prompt",
        "email",
        "phone",
    }
)

_JWT_RE = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+")
_API_KEY_RE = re.compile(r"\bsgh_[\w]+")
_MSISDN_RE = re.compile(r"\+\d{7,15}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _mask_msisdn(match: re.Match[str]) -> str:
    # The full number never leaves the process: a stable 8-hex prefix of its sha256 lets an
    # operator correlate a customer across lines without learning the number (docs/09 6).
    digits = match.group(0)[1:]
    return f"msisdn:{hashlib.sha256(digits.encode()).hexdigest()[:8]}"


def _mask_email(match: re.Match[str]) -> str:
    local, _, domain = match.group(0).partition("@")
    return f"{local[0]}***@{domain}"


def _strip_query(text: str) -> str:
    """Record a URL as its path: the stream ticket lives in the query string (D-18)."""
    if text.startswith("/") or re.match(r"^[a-z][a-z0-9+.-]*://", text):
        return text.split("?", 1)[0]
    return text


def redact_text(text: str) -> str:
    """Apply the value patterns: JWT, API key, MSISDN, e-mail, then drop any query string."""
    text = _JWT_RE.sub("[jwt]", text)
    text = _API_KEY_RE.sub("[api_key]", text)
    text = _MSISDN_RE.sub(_mask_msisdn, text)
    text = _EMAIL_RE.sub(_mask_email, text)
    return _strip_query(text)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (REDACTED if _is_denied(key) else _redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _is_denied(key: Any) -> bool:
    return isinstance(key, str) and any(word in key.lower() for word in DENY_KEYS)


def _format_exception(exc_info: Any) -> str:
    if isinstance(exc_info, BaseException):
        return "".join(traceback.format_exception(exc_info))
    if isinstance(exc_info, tuple) and len(exc_info) == 3:
        return "".join(traceback.format_exception(*exc_info))
    return str(exc_info)


def redact_processor(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor: mask deny-listed keys, redact every value, render ``exc_info``.

    ``exc_info`` is converted to a redacted ``exception`` string here rather than by
    structlog's own renderer, so an exception message can never carry a phone number out.
    """
    redacted: EventDict = {}
    for key, value in event_dict.items():
        if key == "exc_info" and value:
            redacted["exception"] = redact_text(_format_exception(value))
        elif _is_denied(key):
            redacted[key] = REDACTED
        else:
            redacted[key] = _redact_value(value)
    return redacted


def _add_level(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("level", method_name)
    return event_dict


def _make_static_fields(
    service: str, version: str, env: str
) -> Any:  # structlog.Processor, spelled out to keep the closure simple
    def add_static_fields(
        logger: WrappedLogger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        event_dict.setdefault("service", service)
        event_dict.setdefault("version", version)
        event_dict.setdefault("env", env)
        return event_dict

    return add_static_fields


def _add_request_context(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    event_dict.setdefault("request_id", request_id_var.get())
    event_dict.setdefault("correlation_id", correlation_id_var.get())
    event_dict.setdefault("entrypoint", entrypoint_var.get())
    return event_dict


_installed_handlers: list[logging.Handler] = []

# Loggers a host server configures for itself before the application is imported.
_HOST_LOGGERS = ("gunicorn.error", "gunicorn.access")


def _route_host_loggers() -> None:
    """Route the host server's loggers through the same JSON pipeline (docs/03 7.3).

    Gunicorn attaches its own handler to ``gunicorn.error`` and stops propagation, so without
    this its lines would stay plain text next to the application's JSON. Only loggers the host
    has already configured are touched, and they are left alone in a plain Django process.
    """
    for name in _HOST_LOGGERS:
        host_logger = logging.getLogger(name)
        if not host_logger.handlers:
            continue
        for handler in list(host_logger.handlers):
            host_logger.removeHandler(handler)
        host_logger.propagate = True


def configure_logging(*, service: str, level: str = "INFO") -> None:
    """Configure JSON logging for the process: structlog plus a stdlib root handler.

    Called once from every entrypoint; a second call replaces the handler this function
    installed and leaves the host application's handlers alone.
    """
    try:
        version = str(getattr(settings, "HUB_VERSION", "dev"))
        env = str(getattr(settings, "HUB_ENV", "local"))
    except ImproperlyConfigured:  # settings are not loaded yet: the entrypoint sets them next
        version, env = "dev", "local"

    shared: list[Any] = [
        _make_static_fields(service, version, env),
        _add_level,
        structlog.processors.TimeStamper(fmt="iso", key="ts", utc=True),
        _add_request_context,
        # Business context arrives through ``extra=``; ProcessorFormatter does not copy it
        # by itself, and it must be merged before redaction so a secret in an extra is
        # masked like any other value (docs/03 7.4).
        structlog.stdlib.ExtraAdder(),
        add_trace_id,
        redact_processor,
    ]

    formatter = ProcessorFormatter(
        processors=[ProcessorFormatter.remove_processors_meta, structlog.processors.JSONRenderer()],
        foreign_pre_chain=shared,
    )
    structlog.configure(
        processors=[*shared, ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    for handler in _installed_handlers:
        root.removeHandler(handler)
    _installed_handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)
    _installed_handlers.append(handler)

    _route_host_loggers()
