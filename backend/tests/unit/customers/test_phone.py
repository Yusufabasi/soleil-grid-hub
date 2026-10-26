"""Every shape a person types a number in must become one E.164 value (Review focus 2)."""

from __future__ import annotations

import pytest

from hub.customers.domain.errors import InvalidPhoneNumber
from hub.customers.domain.phone import normalise_e164


@pytest.mark.tc("TC-FR-01-1")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+243 81 234 5678", "+243812345678"),
        ("+243812345678", "+243812345678"),
        ("  +243.812.345.678  ", "+243812345678"),
        ("+243-812-345-678", "+243812345678"),
        ("+243 (81) 234 5678", "+243812345678"),
        ("00243812345678", "+243812345678"),
        ("0812345678", "+243812345678"),
        ("081 234 5678", "+243812345678"),
    ],
)
def test_normalise_e164_cases(raw: str, expected: str) -> None:
    assert normalise_e164(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "   ", "abc", "12345", "+243812", "+0123456789", "+243812345678901234"]
)
def test_rejects_what_is_not_a_number(raw: str) -> None:
    with pytest.raises(InvalidPhoneNumber):
        normalise_e164(raw)


def test_default_prefix_is_the_deployments_own() -> None:
    assert normalise_e164("0812345678", "+33") == "+33812345678"
