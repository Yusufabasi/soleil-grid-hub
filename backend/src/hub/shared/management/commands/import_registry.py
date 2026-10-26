"""``manage.py import_registry``: a CSV load that either lands whole or not at all.

Contract (Plan notes 1 and Task 02.8):

* ``--kind customers|meters --file PATH --actor-email EMAIL [--dry-run]``
* the file replaces the ``HUB_IMPORT_MAX_BYTES`` ceiling and the row list the
  ``HUB_IMPORT_MAX_ROWS`` one, both checked *before* a single insert;
* one bad row means zero rows inserted, and the report names the row and its code — never the
  value, because a bad row is usually a name or a phone number (D-21);
* the report is one JSON object on stdout: ``{kind, dry_run, inserted, skipped_existing,
  would_insert, neutralised, errors}``; ``neutralised`` (D-30) is an addition to the shape in
  the task, and it carries row numbers only;
* exit 0 when the run succeeded (including a dry run and a rerun that inserted nothing), 2 when
  the file was refused (a bad row, a bad header, too big, not UTF-8), 3 when the actor may not
  run this import at all.

The command runs in the ``hub-api`` deployable (``--settings hub.settings.api``): it writes
through the same unit of work, the same repositories and the same audit stream as the API, so
an imported row is indistinguishable from a typed one except for its audit action.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from hub.shared.api.permissions import operation_rules
from hub.shared.imports import (
    ImportOutcome,
    ImportRefused,
    ImportRowError,
    importer_for,
    neutralise_row,
    registered_kinds,
)

EXIT_REFUSED = 2
EXIT_ACTOR = 3


class Command(BaseCommand):
    """Load customers or meters from one CSV file, all or nothing."""

    help = "Import customers or meters from a CSV file; a single bad row imports nothing."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--kind", required=True, help="customers or meters")
        parser.add_argument("--file", required=True, dest="path", help="path to the CSV file")
        parser.add_argument(
            "--actor-email",
            required=True,
            help="the existing user the audit trail records as the importer",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="validate and report without writing anything",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 - Django's signature
        kind = str(options["kind"]).strip().lower()
        importer = importer_for(kind)
        if importer is None:
            raise CommandError(
                f"no importer is registered for {kind!r}; known kinds: {registered_kinds()}",
                returncode=EXIT_REFUSED,
            )
        rule = operation_rules().get(importer.operation)
        if rule is None:
            raise CommandError(
                f"the contract has no rule for {importer.operation!r}", returncode=EXIT_REFUSED
            )

        path = Path(str(options["path"]))
        if not path.is_file():
            raise CommandError(f"{path} is not a readable file", returncode=EXIT_REFUSED)

        # Narrowed for mypy: the protocol's attributes are the only ones the kernel may read.
        columns: frozenset[str] = importer.columns
        rows, header_error = self._rows(columns, self._text(path))
        dry_run = bool(options["dry_run"])
        if header_error is not None:
            # Nothing is read from a file whose shape is wrong: there is no row to blame and
            # no partial import worth reporting.
            self._report(kind, dry_run, ImportOutcome(), [header_error], [])
            raise CommandError(
                f"row {header_error.row}: {header_error.code}; nothing was imported",
                returncode=EXIT_REFUSED,
            )

        neutralised: list[int] = []
        cleaned: list[dict[str, str]] = []
        for index, row in enumerate(rows):
            # The header is row 1, so the first data row is row 2 — the number an operator
            # sees in their spreadsheet, not a zero-based offset.
            row_number = index + 2
            values, flagged = neutralise_row(row)
            if flagged:
                neutralised.append(row_number)
            cleaned.append(values)

        max_rows = int(settings.HUB_IMPORT_MAX_ROWS)
        if len(cleaned) > max_rows:
            raise CommandError(
                f"{len(cleaned)} rows is more than HUB_IMPORT_MAX_ROWS ({max_rows})",
                returncode=EXIT_REFUSED,
            )

        try:
            outcome = importer.run(
                actor_email=str(options["actor_email"]),
                allowed_roles=rule.roles,
                rows=cleaned,
                dry_run=dry_run,
            )
        except ImportRefused as refused:
            raise CommandError(str(refused), returncode=EXIT_ACTOR) from refused

        self._report(kind, dry_run, outcome, outcome.errors, neutralised)
        if outcome.errors:
            raise CommandError(
                f"{len(outcome.errors)} row error(s); nothing was imported",
                returncode=EXIT_REFUSED,
            )

    def _report(
        self,
        kind: str,
        dry_run: bool,
        outcome: ImportOutcome,
        errors: list[ImportRowError],
        neutralised: list[int],
    ) -> None:
        """One JSON object on stdout; row numbers and codes only, never a cell's value."""
        report = {
            "kind": kind,
            "dry_run": dry_run,
            "inserted": outcome.inserted,
            "skipped_existing": outcome.skipped_existing,
            "would_insert": outcome.would_insert,
            "neutralised": neutralised,
            "errors": [{"row": error.row, "code": error.code} for error in errors],
        }
        self.stdout.write(json.dumps(report, sort_keys=True))

    # --- file handling -------------------------------------------------------------------
    def _text(self, path: Path) -> str:
        """The file as text: size cap first, then UTF-8 with an optional BOM.

        ``utf-8-sig`` strips the BOM Excel writes; a file that is not UTF-8 at all is refused
        (a mis-decoded name would be stored corrupted, and the operator would only notice on
        a receipt).
        """
        size = path.stat().st_size
        limit = int(settings.HUB_IMPORT_MAX_BYTES)
        if size > limit:
            raise CommandError(
                f"{path} is {size} bytes, more than HUB_IMPORT_MAX_BYTES ({limit})",
                returncode=EXIT_REFUSED,
            )
        try:
            return path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CommandError(f"{path} is not valid UTF-8", returncode=EXIT_REFUSED) from exc

    def _rows(
        self, columns: frozenset[str], text: str
    ) -> tuple[list[dict[str, str]], ImportRowError | None]:
        """The data rows, and whether the header was what this kind expects."""
        reader = csv.DictReader(io.StringIO(text))
        header = [name.strip() for name in (reader.fieldnames or [])]
        missing = sorted(columns - set(header))
        unexpected = sorted(set(header) - columns)
        if missing or unexpected:
            return [], ImportRowError(row=1, code="bad_header")
        rows: list[dict[str, str]] = []
        for raw in reader:
            if None in raw:
                # More cells than columns: a stray comma shifts every later field.
                return [], ImportRowError(row=reader.line_num, code="bad_row_width")
            rows.append({name: (value or "") for name, value in raw.items() if name})
        if not rows:
            return [], ImportRowError(row=1, code="empty_file")
        return rows, None
