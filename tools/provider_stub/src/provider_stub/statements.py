"""Daily and monthly statement CSVs, with the mismatches slice 14 reconciles against.

A statement is the provider's *side* of the ledger, and the point of a double is that its copy can
be wrong in exactly the ways a real one is. Each injection here corresponds to one
`reconciliation_items.kind` the hub knows (slice 14), one to one, so a reconciliation test that
passes is passing against a statement that actually contains the thing it is looking for (slice T1
review focus 3):

* `statement_only` — a line for money the hub never saw (the hub has no transaction for it);
* `ledger_only` — the hub has a transaction the statement forgot;
* `amount_mismatch` — the same charge, one minor unit out;
* `duplicate_in_statement` — the same charge twice (the provider billed twice, or its export did);
* `currency_mismatch` — the same charge in another currency.

`formula_cell` is not a reconciliation kind: it injects a cell starting `=HYPERLINK(` so the hub's
CSV reader can be tested for neutralising formulas rather than evaluating them. It patches
`external_reference`, because that is the column a real provider fills with whatever a payer typed.

Everything here is deterministic: the mutations are applied by row index, not at random, so the
same input gives the same bytes and a failing reconciliation test can be rerun on the exact file.
The CSV is written by the standard library with `QUOTE_MINIMAL` and `\\n` line ends, UTF-8 without
a BOM — the dialect the hub's reader is configured for.

Row size is a constraint, not a detail: the hub caps an upload at 10 MiB and 100,000 rows, and the
verification runs the largest statement through the CLI. A row is therefore written as compactly as
the columns allow (see `test_100000_rows_under_10mb`), which is also why the payer column carries
the last four digits only — a full number would be both larger and a privacy problem.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

__all__ = [
    "COLUMNS",
    "FORMULA_PREFIX",
    "KINDS",
    "Row",
    "expected_items",
    "month_days",
    "render",
    "rows_for_collections",
    "rows_for_day",
    "rows_for_month",
    "synthetic_rows",
]

# Exactly the header the hub's upload parser expects, in this order.
COLUMNS = (
    "provider_reference",
    "external_reference",
    "occurred_at",
    "amount_minor",
    "currency",
    "fee_minor",
    "status",
    "payer_msisdn_last4",
)

# The injection kinds that map to a `reconciliation_items.kind`, and the one that does not.
KINDS = (
    "statement_only",
    "ledger_only",
    "amount_mismatch",
    "duplicate_in_statement",
    "currency_mismatch",
    "formula_cell",
)
RECONCILIATION_KINDS = KINDS[:5]
FORMULA_PREFIX = '=HYPERLINK("https://statements.example.invalid/track"'


@dataclass(frozen=True, slots=True)
class Row:
    """One statement line. Money is integer minor units; never a float."""

    provider_reference: str
    external_reference: str
    occurred_at: str
    amount_minor: int
    currency: str
    fee_minor: int
    status: str
    payer_msisdn_last4: str

    def cells(self) -> list[Any]:
        return [getattr(self, name) for name in COLUMNS]


def _csv_bytes(rows: Iterable[Row]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow(row.cells())
    return buffer.getvalue().encode("utf-8")


def _statement_only(row: Row, index: int) -> Row:
    """A line the hub has never heard of: its reference is not a reference it ever issued."""
    return replace(
        row, provider_reference=f"MPSTMTONLY{index:04d}", external_reference=str(_uuid_like(index))
    )


def _uuid_like(index: int) -> str:
    """A uuid-shaped external reference, so an injected row looks like a real one."""
    return f"00000000-0000-7000-8000-{index:012d}"


def render(rows: Sequence[Row], injections: Sequence[dict[str, Any]] = ()) -> bytes:
    """The statement CSV: ``rows`` with ``injections`` applied, in order.

    `ledger_only` removes from the front, so a caller that wants a specific collection omitted
    puts it first; the rest patch or duplicate the rows they name by position. An unknown kind is
    refused rather than ignored — a typo in a test would otherwise produce a *clean* statement and
    a reconciliation test that passes against nothing.
    """
    lines: list[Row] = list(rows)
    for injection in injections:
        kind = str(injection.get("kind", ""))
        count = int(injection.get("count", 1))
        if kind not in KINDS:
            raise ValueError(f"not a statement injection kind: {kind!r}")
        if count < 0:
            raise ValueError(f"injection count must not be negative, got {count}")
        if kind == "statement_only":
            base = lines[0] if lines else _blank_row()
            lines.extend(
                _statement_only(base, index) for index in range(count)
            )
        elif kind == "ledger_only":
            lines = lines[count:]
        elif kind == "amount_mismatch":
            lines = [
                replace(row, amount_minor=row.amount_minor + 1) if index < count else row
                for index, row in enumerate(lines)
            ]
        elif kind == "duplicate_in_statement":
            lines.extend(lines[:count])
        elif kind == "currency_mismatch":
            lines = [
                replace(row, currency="CDF" if row.currency == "USD" else "USD")
                if index < count
                else row
                for index, row in enumerate(lines)
            ]
        else:  # formula_cell
            lines = [
                replace(row, external_reference=f"{FORMULA_PREFIX},{row.external_reference}\")")
                if index < count
                else row
                for index, row in enumerate(lines)
            ]
    return _csv_bytes(lines)


def expected_items(rows: Sequence[Row], injections: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """What the hub's reconciliation should find, so a test can assert it found exactly that.

    One entry per injection, naming the reconciliation kind and the provider reference it touched.
    `formula_cell` is absent on purpose: it is a neutralisation case, not a difference between two
    ledgers, and claiming a kind for it would let a reconciliation test pass on a mismatch that is
    not one.
    """
    items: list[dict[str, Any]] = []
    source = list(rows)
    for injection in injections:
        kind = str(injection.get("kind", ""))
        count = int(injection.get("count", 1))
        if kind not in RECONCILIATION_KINDS:
            continue
        for index in range(count):
            if kind == "statement_only":
                reference = f"MPSTMTONLY{index:04d}"
            else:
                reference = source[index % len(source)].provider_reference if source else ""
            items.append({"kind": kind, "provider_reference": reference})
    return items


def render_expected_json(rows: Sequence[Row], injections: Sequence[dict[str, Any]]) -> bytes:
    """The `expected_items.json` a caller writes next to an injected statement."""
    return json.dumps(expected_items(rows, injections), indent=2, sort_keys=True).encode("utf-8")


def rows_for_collections(collections: Sequence[Any]) -> list[Row]:
    """The provider's own view of what it settled: one row per successful collection.

    Failed and reversed collections are not on a statement at all — money that did not move, or
    moved back, is what slice 14's ``ledger_only`` items are made of, and putting it in the file
    would hide the difference the reconciliation is looking for.
    """
    return [
        Row(
            provider_reference=collection.provider_reference,
            external_reference=collection.reference,
            occurred_at=collection.updated_at,
            amount_minor=collection.amount_minor,
            currency=collection.currency,
            fee_minor=0,
            status=collection.status,
            # Four digits only: a statement is a file that gets mailed around, and a full MSISDN
            # has no business in one (and no column in the hub's contract).
            payer_msisdn_last4=collection.payer_msisdn[-4:],
        )
        for collection in collections
        if collection.status == "successful"
    ]


def rows_for_day(rows: Sequence[Row], day: date) -> list[Row]:
    """One day's rows, oldest first: the daily statement, and one slice of the monthly one."""
    prefix = day.isoformat()
    chosen = [row for row in rows if row.occurred_at.startswith(prefix)]
    return sorted(chosen, key=lambda row: row.occurred_at)


def rows_for_month(rows: Sequence[Row], month: str) -> list[Row]:
    """Every day of the month, in order: the monthly statement is the daily ones concatenated."""
    chosen: list[Row] = []
    for day in month_days(month):
        chosen.extend(rows_for_day(rows, day))
    return chosen


def month_days(month: str) -> list[date]:
    """Every day of ``YYYY-MM``, so a monthly statement is the concatenation of the daily ones."""
    year, _, month_number = month.partition("-")
    if not year.isdigit() or not month_number.isdigit():
        raise ValueError(f"not a month: {month!r}")
    first = date(int(year), int(month_number), 1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    days: list[date] = []
    current = first
    while current < next_month:
        days.append(current)
        current += timedelta(days=1)
    return days


def synthetic_rows(
    count: int, *, provider: str = "mpesa_cd", day: date | None = None, start_index: int = 0
) -> list[Row]:
    """``count`` evenly spaced successful rows, for a statement with no live collections.

    The shape is deliberate: uuid-shaped external references, six-digit amounts, `successful`,
    and four digits of payer. That keeps a 100,000-row statement inside the hub's 10 MiB cap while
    every cell still looks like the real thing.
    """
    chosen_day = day or date(2026, 10, 27)
    rows: list[Row] = []
    for index in range(count):
        number = start_index + index
        seconds = number % 86_400
        occurred = datetime(
            chosen_day.year, chosen_day.month, chosen_day.day, tzinfo=UTC
        ) + timedelta(seconds=seconds)
        rows.append(
            Row(
                provider_reference=f"MP{number:010X}"[-12:],
                external_reference=_uuid_like(number),
                occurred_at=occurred.isoformat().replace("+00:00", "Z"),
                amount_minor=100_000 + (number % 900_000),
                currency="USD",
                fee_minor=0,
                status="successful",
                payer_msisdn_last4=f"{number % 10_000:04d}",
            )
        )
    _ = provider  # the columns carry no provider column: the route names it
    return rows


def _blank_row() -> Row:
    return Row(
        provider_reference="MP0000000000",
        external_reference=_uuid_like(0),
        occurred_at="1970-01-01T00:00:00Z",
        amount_minor=0,
        currency="USD",
        fee_minor=0,
        status="successful",
        payer_msisdn_last4="0000",
    )


def row_fields(row: Row) -> dict[str, Any]:  # pragma: no cover - diagnostics helper
    return asdict(row)
