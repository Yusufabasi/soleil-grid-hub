"""Redaction must never let a credential, MSISDN, JWT or ticket id reach a log line.

docs/03 section 7.4 and docs/10: the denylist and the value patterns are the last line of
defence before a record leaves the process.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging

from hub.shared.logging import configure_logging, redact_processor, redact_text

DENY_KEYS = [
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
]


def test_redaction_masks_denylisted_keys():
    event = {key: f"raw-{key}" for key in DENY_KEYS}

    redacted = redact_processor(None, "info", dict(event))

    for key in DENY_KEYS:
        assert redacted[key] == "[redacted]"


def test_redaction_covers_nested_values_exceptions_and_ticket_query():
    nested = {"a": {"b": [{"password": "x", "keep": "visible"}]}}

    out = redact_processor(None, "info", dict(nested))

    assert out["a"]["b"][0]["password"] == "[redacted]"
    assert out["a"]["b"][0]["keep"] == "visible"

    exc = ValueError("lookup failed for +243812345678")
    out_exc = redact_processor(None, "info", {"exc_info": exc})

    assert "exception" in out_exc
    assert "+243812345678" not in out_exc["exception"]
    assert "msisdn:" in out_exc["exception"]

    out_url = redact_processor(None, "info", {"url": "/api/v1/dashboard/stream?ticket=abc123"})

    assert out_url["url"] == "/api/v1/dashboard/stream"


def test_value_patterns():
    assert redact_text("eyJhbGciOi.payloadpart.sigpart") == "[jwt]"
    assert redact_text("sgh_prod_k1_secretvalue") == "[api_key]"

    digest = hashlib.sha256(b"243812345678").hexdigest()[:8]
    assert redact_text("+243812345678") == f"msisdn:{digest}"

    assert redact_text("anna@example.org") == "a***@example.org"


def test_json_output_has_required_fields():
    configure_logging(service="hub-test", level="INFO")
    stream = io.StringIO()
    root = logging.getLogger()
    for handler in root.handlers:
        if hasattr(handler, "stream"):
            handler.stream = stream

    logging.getLogger("hub.test").info("hello")
    for handler in root.handlers:
        handler.flush()

    payload = json.loads(stream.getvalue().strip().splitlines()[-1])

    assert {"service", "version", "env", "level", "ts", "request_id", "trace_id"} <= set(payload)
    assert payload["service"] == "hub-test"


def test_extra_fields_survive_into_the_json_line_and_are_redacted():
    """``logger.info(..., extra={...})`` is how business context is logged: it must arrive."""
    configure_logging(service="hub-test", level="INFO")
    stream = io.StringIO()
    root = logging.getLogger()
    for handler in root.handlers:
        if hasattr(handler, "stream"):
            handler.stream = stream

    logging.getLogger("hub.test").info(
        "with_context",
        extra={"top_up_id": "abc", "authorization": "Bearer secret", "phone": "+243812345678"},
    )
    for handler in root.handlers:
        handler.flush()

    payload = json.loads(stream.getvalue().strip().splitlines()[-1])

    assert payload["top_up_id"] == "abc"
    assert payload["authorization"] == "[redacted]"
    assert payload["phone"] == "[redacted]"
    assert "+243812345678" not in json.dumps(payload)
