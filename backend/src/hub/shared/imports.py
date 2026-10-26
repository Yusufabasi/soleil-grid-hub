"""The bulk-import seam: one command, one importer per kind, no module reading another's tables.

``manage.py import_registry`` is the only entry point ``docs/plan/slice-02-registry.md`` allows
for a CSV load (Plan notes 1: no HTTP route exists), and it has to import two kinds that live in
two modules. Neither module may import the other, and ``hub.shared`` may not import either
(docs/03 3.1), so the command owns the file handling and the modules register an importer here
from their ``ready()`` hook — the same registration pattern the denial recorder and the problem
catalogues use.

What the kernel owns is what is genuinely shared: reading the bytes (cap, BOM, UTF-8),
neutralising spreadsheet formulas (D-30), the report shape, and the exit codes. What a module
owns is its columns, its validation and its insert, because only it knows what a valid row is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

__all__ = [
    "FORMULA_PREFIXES",
    "NEUTRALISED_PREFIX",
    "CsvImporter",
    "ImportOutcome",
    "ImportRefused",
    "ImportRowError",
    "importer_for",
    "neutralise_row",
    "register_importer",
    "registered_kinds",
]

# A spreadsheet treats a cell starting with one of these as a formula (D-30).
FORMULA_PREFIXES = ("=", "+", "-", "@")
NEUTRALISED_PREFIX = "'"
# A phone number legitimately starts with '+'; quoting it would corrupt the value, and it is
# validated as E.164 rather than re-exported as free text.
UNNEUTRALISED_FIELDS = frozenset({"phone"})


@dataclass(frozen=True, slots=True)
class ImportRowError:
    """One refused row and its code — never the cell's value (a name or a phone is PII)."""

    row: int
    code: str


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """What one run did, or would do: the command turns this into the JSON report."""

    inserted: int = 0
    skipped_existing: int = 0
    errors: list[ImportRowError] = field(default_factory=list)
    would_insert: int = 0


class ImportRefused(Exception):
    """The run may not start: an unknown actor, an inactive one, or a missing role.

    Separate from a row error because the exit code differs (3, not 2): nothing was read into
    the database and no row is at fault, so an operator fixes the actor, not the file.
    """


class CsvImporter(Protocol):
    """What a module registers: its columns, its operation and its all-or-nothing insert."""

    kind: str
    columns: frozenset[str]
    #: The `createCustomer` / `createMeter` rule whose roles may run this import.
    operation: str

    def run(
        self,
        *,
        actor_email: str,
        allowed_roles: frozenset[str],
        rows: Sequence[Mapping[str, str]],
        dry_run: bool,
    ) -> ImportOutcome:
        """Validate every row, then insert all of them or none.

        ``rows`` are the file's data rows with the header already checked and formula cells
        neutralised. A refusal to run at all raises :class:`ImportRefused`.
        """
        ...


_importers: dict[str, CsvImporter] = {}


def register_importer(importer: CsvImporter) -> None:
    """Install one kind's importer; called by the module that owns the rows."""
    _importers[importer.kind] = importer


def importer_for(kind: str) -> CsvImporter | None:
    return _importers.get(kind)


def registered_kinds() -> list[str]:
    return sorted(_importers)


def neutralise_row(row: Mapping[str, str]) -> tuple[dict[str, str], bool]:
    """Prefix a formula-looking cell with ``'`` (D-30); answer whether any cell changed.

    The value stored is the neutralised one: an export that re-opens the file must show text,
    not execute it, and a round trip through the same importer then stays byte-stable.
    """
    cleaned: dict[str, str] = {}
    flagged = False
    for name, value in row.items():
        if name not in UNNEUTRALISED_FIELDS and value[:1] in FORMULA_PREFIXES:
            cleaned[name] = NEUTRALISED_PREFIX + value
            flagged = True
        else:
            cleaned[name] = value
    return cleaned, flagged
