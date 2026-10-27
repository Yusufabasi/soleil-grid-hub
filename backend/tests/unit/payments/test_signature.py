"""The webhook signature as a pure function (Task 04.4, FR-03).

Two properties carry the requirement, and both are asserted here rather than inferred from a
passing webhook: the string that is signed is exactly ``{timestamp}.{event_id}.{body}`` over the
raw bytes, and a forged signature is always the *first* refusal — even when the timestamp is
also stale, because the two answers tell the sender different things.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from hub.payments.domain.errors import InvalidSignature, StaleTimestamp
from hub.payments.domain.signature import compute_signature, signed_string, verify

SECRET = b"k"
TIMESTAMP = "1700000000"
EVENT_ID = "evt_1"
BODY = b'{"a":1}'


def test_signed_string_is_timestamp_event_id_body() -> None:
    """The header is the HMAC of ``1700000000.evt_1.{"a":1}``, prefixed ``v1=``."""
    expected = hmac.new(SECRET, b'1700000000.evt_1.{"a":1}', hashlib.sha256).hexdigest()

    assert signed_string(TIMESTAMP, EVENT_ID, BODY) == b'1700000000.evt_1.{"a":1}'
    assert compute_signature(SECRET, TIMESTAMP, EVENT_ID, BODY) == f"v1={expected}"


def test_valid_signature_passes() -> None:
    header = compute_signature(SECRET, TIMESTAMP, EVENT_ID, BODY)

    verify([SECRET], header, TIMESTAMP, EVENT_ID, BODY, now=int(TIMESTAMP))


def test_tampered_body_fails() -> None:
    header = compute_signature(SECRET, TIMESTAMP, EVENT_ID, BODY)

    with pytest.raises(InvalidSignature):
        verify([SECRET], header, TIMESTAMP, EVENT_ID, b'{"a":2}', now=int(TIMESTAMP))


def test_tampered_event_id_fails() -> None:
    header = compute_signature(SECRET, TIMESTAMP, EVENT_ID, BODY)

    with pytest.raises(InvalidSignature):
        verify([SECRET], header, TIMESTAMP, "evt_2", BODY, now=int(TIMESTAMP))


def test_previous_secret_accepted_during_overlap() -> None:
    """Both secrets of a rotation verify, so a rotation never drops an honest delivery."""
    old = b"previous"
    header = compute_signature(old, TIMESTAMP, EVENT_ID, BODY)

    verify([SECRET, old], header, TIMESTAMP, EVENT_ID, BODY, now=int(TIMESTAMP))


def test_300_seconds_accepted_301_stale() -> None:
    header = compute_signature(SECRET, TIMESTAMP, EVENT_ID, BODY)
    sent_at = int(TIMESTAMP)

    verify([SECRET], header, TIMESTAMP, EVENT_ID, BODY, now=sent_at + 300)
    verify([SECRET], header, TIMESTAMP, EVENT_ID, BODY, now=sent_at - 300)

    with pytest.raises(StaleTimestamp):
        verify([SECRET], header, TIMESTAMP, EVENT_ID, BODY, now=sent_at + 301)


def test_a_forgery_is_401_even_when_it_is_also_stale() -> None:
    """The order matters: a bad signature must never be reported as a clock problem."""
    header = compute_signature(b"wrong", TIMESTAMP, EVENT_ID, BODY)

    with pytest.raises(InvalidSignature):
        verify([SECRET], header, TIMESTAMP, EVENT_ID, BODY, now=int(TIMESTAMP) + 10_000)


def test_missing_headers_are_refused() -> None:
    header = compute_signature(SECRET, TIMESTAMP, EVENT_ID, BODY)

    for signature, timestamp, event_id in (
        (None, TIMESTAMP, EVENT_ID),
        (header, None, EVENT_ID),
        (header, TIMESTAMP, None),
        (header, "not-a-number", EVENT_ID),
        (header.replace("v1=", "v2="), TIMESTAMP, EVENT_ID),
    ):
        with pytest.raises(InvalidSignature):
            verify([SECRET], signature, timestamp, event_id, BODY, now=int(TIMESTAMP))
