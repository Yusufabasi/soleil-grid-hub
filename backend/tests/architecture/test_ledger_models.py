"""The ledger's unmanaged models, and the fact that nothing in Python writes them (ADR-0020).

Two claims:

* the six shims describe exactly the columns ``db/schema.sql`` creates, in both directions —
  drift shows up here rather than in a query at 3am;
* the module has no write path to those tables. The only statements it issues against
  ``ledger_*`` are reads, and the only writes go through the definer functions (D-22), which is
  what makes "no ORM write" a property of the code rather than a promise in a document.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from hub.ledger.infrastructure import models

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from support.db_alignment import check_db_alignment  # noqa: E402

LEDGER_MODELS = (
    models.LedgerAccount,
    models.LedgerTransaction,
    models.LedgerEntry,
    models.WalletBalance,
    models.LedgerBalanceSnapshot,
    models.AccountingPeriod,
)

LEDGER_SOURCE = Path(__file__).resolve().parents[2] / "src" / "hub" / "ledger"


@pytest.mark.django_db
@pytest.mark.tc("TC-NFR-14-6")
def test_models_match_database() -> None:
    check_db_alignment(*LEDGER_MODELS)


@pytest.mark.tc("TC-NFR-14-6")
def test_ledger_models_unmanaged_and_read_only() -> None:
    """No migration, no table of Django's, and no schema-qualified name."""
    for model in LEDGER_MODELS:
        assert model._meta.managed is False, model.__name__
        assert "." not in model._meta.db_table, model.__name__


@pytest.mark.tc("TC-FR-04-3")
def test_module_has_no_orm_write_of_a_ledger_row() -> None:
    """No ``.save()``, no ``objects.create``, no UPDATE/DELETE statement in the module."""
    offenders: list[str] = []
    for path in sorted(LEDGER_SOURCE.rglob("*.py")):
        # Unparsed, so a docstring that *names* the forbidden calls is not the crime: only
        # code is scanned, and the scan sees through line breaks inside a call.
        code = ast.unparse(ast.parse(path.read_text(encoding="utf-8")))
        for needle in (".save(", "objects.create(", "bulk_create(", "objects.update("):
            if needle in code:
                offenders.append(f"{path.name}: {needle}")
    assert not offenders, offenders


@pytest.mark.tc("TC-FR-04-3")
def test_only_the_writer_calls_the_posting_functions() -> None:
    """One caller per definer function, so the write path can be reviewed in one file."""
    callers: dict[str, list[str]] = {}
    for path in sorted(LEDGER_SOURCE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for function in ("post_ledger_transaction", "reverse_ledger_transaction"):
            if f'"{function}"' in text:
                callers.setdefault(function, []).append(path.name)
    assert callers == {
        "post_ledger_transaction": ["writer.py"],
        "reverse_ledger_transaction": ["writer.py"],
    }, callers
