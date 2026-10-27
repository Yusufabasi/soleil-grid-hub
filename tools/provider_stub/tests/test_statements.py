"""Task T1.4 and review focus 3: a statement whose wrongness is exactly the intended wrongness.

The assertions are in two directions on purpose. `test_each_injection_maps_to_one_kind` checks that
each kind produces *one* difference of that kind and nothing else — that is what stops slice 14's
reconciliation tests from passing against a statement that proves nothing. The size and determinism
tests check the two properties the hub's upload path depends on: a 100,000-row statement is under
the 10 MiB cap, and the same input produces the same bytes, so a failing reconciliation can be
reproduced from the file it failed on.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date

import pytest

from provider_stub.statements import (
    COLUMNS,
    FORMULA_PREFIX,
    KINDS,
    expected_items,
    month_days,
    render,
    rows_for_day,
    rows_for_month,
    synthetic_rows,
)

DAY = date(2026, 10, 27)


def rows(count: int = 3) -> list:
    return synthetic_rows(count, day=DAY)


def parse(payload: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload.decode("utf-8"))))


def test_daily_csv_header_and_rows() -> None:
    """The exact header, and one row per successful collection sorted by `occurred_at`."""
    document = parse(render(rows()))

    assert document[0] == list(COLUMNS)
    assert document[0] == [
        "provider_reference",
        "external_reference",
        "occurred_at",
        "amount_minor",
        "currency",
        "fee_minor",
        "status",
        "payer_msisdn_last4",
    ]
    assert len(document) == 4
    occurred = [row[2] for row in document[1:]]
    assert occurred == sorted(occurred)
    assert all(row[6] == "successful" for row in document[1:])


def test_daily_statement_is_that_day_only() -> None:
    """A day's file holds that day's rows: `rows_for_day` is the filter, not the caller's."""
    chosen = rows(3)
    other_day = date(2026, 10, 26)
    mixed = chosen + [row for row in synthetic_rows(2, day=other_day, start_index=500)]

    assert rows_for_day(mixed, DAY) == chosen
    assert rows_for_day(mixed, other_day) == rows_for_day(
        synthetic_rows(2, day=other_day, start_index=500), other_day
    )


def test_monthly_csv_equals_concatenated_days() -> None:
    """October is every October day's rows, in order, under one header."""
    mixed = rows(2) + synthetic_rows(2, day=date(2026, 10, 28), start_index=100)
    monthly = parse(render(rows_for_month(mixed, "2026-10")))
    daily_rows = [
        row
        for day in month_days("2026-10")
        for row in parse(render(rows_for_day(mixed, day)))[1:]
    ]

    assert monthly[0] == list(COLUMNS)
    assert monthly[1:] == daily_rows
    assert len(monthly) == 5  # the two days that have rows, plus the header


def test_each_injection_maps_to_one_kind() -> None:
    """One injection, one difference, of exactly the kind the item says."""
    base = rows(4)
    expected_by_kind = {
        "statement_only": "a line the hub never saw is added",
        "ledger_only": "the hub's line is missing",
        "amount_mismatch": "one amount differs by a minor unit",
        "duplicate_in_statement": "one charge appears twice",
        "currency_mismatch": "one currency differs",
    }

    for kind in expected_by_kind:
        injected = render(base, [{"kind": kind, "count": 1}])
        items = expected_items(base, [{"kind": kind, "count": 1}])

        assert [item["kind"] for item in items] == [kind]
        assert len(items) == 1
        _assert_only_that_difference(kind, base, parse(injected))

    assert set(expected_by_kind) <= set(KINDS)
    # `formula_cell` is not a reconciliation kind: it would otherwise let a reconciliation test
    # pass on something that is not a difference between two ledgers.
    assert expected_items(base, [{"kind": "formula_cell", "count": 1}]) == []


def _assert_only_that_difference(kind: str, base: list, document: list[list[str]]) -> None:
    body = document[1:]
    if kind == "statement_only":
        assert len(body) == len(base) + 1
        assert body[-1][0].startswith("MPSTMTONLY")
        assert body[:-1] == parse(render(base))[1:]
    elif kind == "ledger_only":
        assert len(body) == len(base) - 1
        assert body == parse(render(base))[1:][1:]
    elif kind == "amount_mismatch":
        before = parse(render(base))[1:]
        assert len(body) == len(before)
        changed = [
            index for index, (old, new) in enumerate(zip(before, body, strict=True)) if old != new
        ]
        assert len(changed) == 1
        old, new = before[changed[0]], body[changed[0]]
        assert new[3] == str(int(old[3]) + 1)
        assert new[:3] == old[:3] and new[4:] == old[4:]
    elif kind == "duplicate_in_statement":
        before = parse(render(base))[1:]
        assert len(body) == len(before) + 1
        assert body[-1][0] == before[0][0]
    else:  # currency_mismatch
        before = parse(render(base))[1:]
        assert len(body) == len(before)
        changed = [
            index for index, (old, new) in enumerate(zip(before, body, strict=True)) if old != new
        ]
        assert len(changed) == 1
        assert body[changed[0]][4] != before[changed[0]][4]
        assert body[changed[0]][:4] == before[changed[0]][:4]


def test_duplicate_line_has_same_provider_reference() -> None:
    document = parse(render(rows(2), [{"kind": "duplicate_in_statement", "count": 1}]))
    references = [row[0] for row in document[1:]]

    assert len(references) == 3
    assert references[0] == references[-1]
    assert len(set(references)) == 2


def test_currency_mismatch_changes_only_currency() -> None:
    document = parse(render(rows(2), [{"kind": "currency_mismatch", "count": 1}]))
    before = parse(render(rows(2)))

    assert document[1][4] == "CDF"
    assert document[1][:4] == before[1][:4]
    assert document[1][5:] == before[1][5:]
    assert document[2] == before[2]


def test_formula_cell_present_for_neutralisation_test() -> None:
    """A cell starting `=HYPERLINK(` is in the file: the hub's reader must neutralise it."""
    document = parse(render(rows(2), [{"kind": "formula_cell", "count": 1}]))

    assert document[1][1].startswith(FORMULA_PREFIX)
    assert document[1][1].startswith("=")
    # The rest of the row is untouched, so only the neutralisation is under test.
    assert document[2] == parse(render(rows(2)))[2]


def test_unknown_injection_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        render(rows(1), [{"kind": "wishful_thinking", "count": 1}])


def test_100000_rows_under_10mb() -> None:
    """The hub caps an upload at 10 MiB; the double's largest statement has to fit."""
    payload = render(synthetic_rows(100_000, day=DAY))

    assert payload.count(b"\n") == 100_001  # header plus one line per row
    assert len(payload) < 10 * 1024 * 1024


def test_statement_is_deterministic_for_seed() -> None:
    """Same input, same bytes: a failing reconciliation is reproducible from its file."""
    first = render(synthetic_rows(50, day=DAY), [{"kind": "amount_mismatch", "count": 2}])
    second = render(synthetic_rows(50, day=DAY), [{"kind": "amount_mismatch", "count": 2}])
    other = render(synthetic_rows(50, day=DAY, start_index=1))

    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert first == second
    assert first != other


def test_expected_items_are_json_serialisable_for_the_caller() -> None:
    """`expected_items.json` is what a slice 14 test reads; it must be plain JSON."""
    items = expected_items(rows(2), [{"kind": "statement_only", "count": 2}])

    assert len(items) == 2
    assert json.loads(json.dumps(items)) == items
    assert all(item["kind"] == "statement_only" for item in items)
