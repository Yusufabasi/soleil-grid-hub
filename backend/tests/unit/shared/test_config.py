"""Typed environment access (docs/03 section 7.2)."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from hub.shared.config import Secret, env


def test_secret_repr_is_masked():
    secret = Secret("abc")

    assert "abc" not in repr(secret)
    assert "abc" not in str(secret)
    assert secret.reveal() == "abc"
    assert repr(secret) == "Secret(***)"


def test_missing_required_raises_improperly_configured(monkeypatch):
    monkeypatch.delenv("HUB_NOT_DEFINED_ANYWHERE", raising=False)

    with pytest.raises(ImproperlyConfigured) as excinfo:
        env.str("HUB_NOT_DEFINED_ANYWHERE")

    assert "HUB_NOT_DEFINED_ANYWHERE" in str(excinfo.value)


def test_secret_of_a_missing_variable_is_required(monkeypatch):
    monkeypatch.delenv("HUB_NOT_DEFINED_ANYWHERE", raising=False)

    with pytest.raises(ImproperlyConfigured):
        env.secret("HUB_NOT_DEFINED_ANYWHERE")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
    ],
)
def test_bool_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("HUB_TEST_BOOL", raw)

    assert env.bool("HUB_TEST_BOOL", default=False) is expected


def test_defaults_are_returned_when_the_variable_is_absent(monkeypatch):
    monkeypatch.delenv("HUB_TEST_UNSET", raising=False)

    assert env.str("HUB_TEST_UNSET", default="fallback") == "fallback"
    assert env.int("HUB_TEST_UNSET", default=9102) == 9102
    assert env.bool("HUB_TEST_UNSET", default=True) is True


def test_db_url_parses_role_and_host(monkeypatch):
    monkeypatch.setenv("HUB_TEST_DB", "postgresql+psycopg://hub_api:dev@pgbouncer:6432/hub_rw")

    config = env.db_url("HUB_TEST_DB")

    assert config["USER"] == "hub_api"
    assert config["HOST"] == "pgbouncer"
    assert config["NAME"] == "hub_rw"
    assert config["PORT"] == 6432
    assert config["ENGINE"] == "django.db.backends.postgresql"
    assert config["CONN_MAX_AGE"] == 0
