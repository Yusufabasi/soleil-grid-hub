"""`ledger` is a leaf: it imports `shared`, and of `identity` only the contracts (docs/03 3.1).

The import-linter contract in ``backend/.importlinter`` already forbids the layers of every
other module. This test adds the two things that contract cannot express:

* the *only* business module `ledger` may see is `identity`, and only through
  ``identity.contracts`` (for the ``Principal``, the audit protocol and the executor registry);
* the ledger's own modules do not import each other's layers upwards, so a repository never
  speaks for a use case.

The AST walk is deliberate: a runtime import check would pass for a module that imports lazily
inside a function, which is exactly how a boundary erodes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

LEDGER_SOURCE = Path(__file__).resolve().parents[2] / "src" / "hub" / "ledger"
BUSINESS_MODULES = (
    "customers",
    "payments",
    "meters",
    "telemetry",
    "support",
    "ai_gateway",
    "analytics",
    "notifications",
    "reconciliation",
    "admin",
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def _ledger_modules() -> list[Path]:
    return sorted(LEDGER_SOURCE.rglob("*.py"))


@pytest.mark.tc("TC-NFR-14-1")
def test_ledger_imports_no_other_business_module() -> None:
    offenders = [
        f"{path.name}: {module}"
        for path in _ledger_modules()
        for module in _imports(path)
        if module.startswith("hub.") and module.split(".")[1] in BUSINESS_MODULES
    ]
    assert not offenders, offenders


@pytest.mark.tc("TC-NFR-14-1")
def test_identity_is_reached_through_its_public_surface_only() -> None:
    """`hub.identity.contracts` and `hub.identity.wiring` are the two allowed doors.

    ``wiring`` is allowed because composition is not a layer: it is where the executors and the
    approval service are handed over, exactly as ``customers.wiring`` does for its tariff.
    """
    allowed = {"hub.identity.contracts", "hub.identity.wiring"}
    offenders = [
        f"{path.name}: {module}"
        for path in _ledger_modules()
        for module in _imports(path)
        if module.startswith("hub.identity.")
        and not any(module == door or module.startswith(f"{door}.") for door in allowed)
    ]
    assert not offenders, offenders


@pytest.mark.tc("TC-NFR-14-1")
def test_external_packages_are_the_layer_appropriate_ones() -> None:
    """The domain imports no framework; only infrastructure and api may import Django/psycopg."""
    for path in _ledger_modules():
        module = _imports(path)
        relative = path.relative_to(LEDGER_SOURCE).as_posix()
        if relative.startswith("domain/"):
            banned = {
                "django",
                "rest_framework",
                "psycopg",
                "structlog",
            } & {name.split(".")[0] for name in module}
            assert not banned, f"{relative} imports {sorted(banned)}"
