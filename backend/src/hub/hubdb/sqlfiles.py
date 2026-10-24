"""Reading the SQL files the migration chain applies (docs/04 10.5, ADR-0020).

The authority for the schema is a ``.sql`` file, never a Django model. The wrapper in
``hubdb/migrations`` only carries the file into Django's migration table, so this module is
the single place that knows where the files live: ``db/baseline/0000_baseline.sql`` for the
byte copy of ``db/schema.sql`` and ``db/migrations/NNNN_name.sql`` for everything after it.

``HUB_REPO_ROOT`` names that directory. It defaults to the repository root derived from this
file's location (``backend/src/hub/hubdb/sqlfiles.py`` -> four parents up); the container
image sets it to ``/app``, where ``db/`` sits next to ``src/``.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

__all__ = ["BASELINE_STEM", "repo_root", "sql_text"]

BASELINE_STEM = "0000_baseline"

# A stem is a lowercase identifier: it can only ever name a file in the two directories below.
_STEM_RE = re.compile(r"^[0-9]{4}_[a-z0-9_]+$")


def repo_root() -> Path:
    """The directory that contains ``db/`` (``HUB_REPO_ROOT``, set by settings)."""
    return Path(str(settings.HUB_REPO_ROOT))


def sql_text(stem: str) -> str:
    """The SQL of ``stem``: the baseline lives in ``db/baseline``, the rest in ``db/migrations``."""
    if not _STEM_RE.fullmatch(stem):
        raise ValueError(f"not a migration stem: {stem!r}")
    directory = "baseline" if stem == BASELINE_STEM else "migrations"
    return (repo_root() / "db" / directory / f"{stem}.sql").read_text(encoding="utf-8")
