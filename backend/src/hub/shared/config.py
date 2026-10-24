"""Typed environment access: the single place the process reads configuration.

docs/03 section 7.2: settings modules read the environment through django-environ wrapped
in this module, so a missing required variable raises ``ImproperlyConfigured`` at import and
the process fails fast at start. Secrets come back as :class:`Secret`, whose ``repr`` and
``str`` are masked, so a credential can never reach a log line or a traceback. Database URLs
are parsed here and nowhere else.
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import environ

__all__ = ["Secret", "env"]

_ENGINE = "django.db.backends.postgresql"  # psycopg 3 is picked up automatically (docs/04 10.10)

# The facade is called str/int/bool, so inside the class body the builtins must be spelled
# out explicitly: a method named `str` shadows the builtin for type annotations.
_Text = builtins.str
_Int = builtins.int
_Bool = builtins.bool


class Secret:
    """A credential read from the environment; never printed, logged or repr'd."""

    __slots__ = ("_value",)

    def __init__(self, value: _Text) -> None:
        self._value = value

    def reveal(self) -> _Text:
        """The raw value, for the one place that needs it (a client construction)."""
        return self._value

    def __repr__(self) -> _Text:
        return "Secret(***)"

    def __str__(self) -> _Text:
        return "Secret(***)"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


class _Env:
    """Thin typed facade over ``django-environ``: no default means required."""

    def __init__(self) -> None:
        self._raw = environ.Env()

    def load(self, path: Path) -> None:
        """Read a .env file if it exists; variables already set always win."""
        if path.is_file():
            environ.Env.read_env(builtins.str(path))

    def str(self, name: _Text, default: _Text | None = None) -> _Text:
        value: _Text
        if default is None:
            value = self._raw.str(name)
        else:
            value = self._raw.str(name, default)
        return value

    def int(self, name: _Text, default: _Int | None = None) -> _Int:
        value: _Int
        if default is None:
            value = self._raw.int(name)
        else:
            value = self._raw.int(name, default)
        return value

    def bool(self, name: _Text, default: _Bool | None = None) -> _Bool:
        value: _Bool
        if default is None:
            value = self._raw.bool(name)
        else:
            value = self._raw.bool(name, default)
        return value

    def secret(self, name: _Text, default: _Text | None = None) -> Secret:
        return Secret(self.str(name, default))

    def db_url(self, name: _Text, *, conn_max_age: _Int = 0) -> dict[_Text, Any]:
        """Parse a database URL into a Django DATABASES entry fragment.

        Missing variable: ``ImproperlyConfigured`` at import (fail fast). The engine is
        pinned to psycopg 3; ``conn_max_age`` is 0 behind PgBouncer (docs/04 10.10).
        """
        url = self._raw.str(name)
        config: dict[str, Any] = environ.Env.db_url_config(url, engine=_ENGINE)
        config["CONN_MAX_AGE"] = conn_max_age
        return config


env = _Env()
