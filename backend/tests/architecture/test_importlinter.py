"""Import-linter is the build gate for the module boundaries (docs/03 3.3).

A contract that only passes is worthless: each rule is proven here by planting a module that
breaks it in a throwaway copy of the package and asserting that ``lint-imports`` fails. The
generated file is checked for staleness too, so the matrix and the contracts cannot drift.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"
HUB_SRC = BACKEND / "src" / "hub"
CONFIG = BACKEND / ".importlinter"
GEN = REPO_ROOT / "tools" / "scripts" / "gen_importlinter.py"

# The venv's console script sits next to the interpreter running the tests.
LINT_IMPORTS = Path(sys.executable).with_name("lint-imports")

sys.path.insert(0, str(REPO_ROOT / "tools"))
from check_traceability import MODULES  # noqa: E402

# Packages under src/hub that are not business modules: the kernel has its own contract, the
# rest are wiring (settings, URLConfs) or the migration chain.
TECHNICAL_PACKAGES = {"shared", "hubdb", "settings", "urls"}

VIOLATIONS = {
    "domain imports django": (
        "identity/domain/leak.py",
        "import django\n",
    ),
    "application imports another module's infrastructure": (
        "payments/application/leak.py",
        "from hub.ledger.infrastructure import models\n",
    ),
    "api imports another module's domain": (
        "customers/api/leak.py",
        "from hub.payments.domain import services\n",
    ),
    "kernel imports a business module": (
        "shared/leak.py",
        "from hub.identity.contracts import Principal\n",
    ),
    "code imports the AI SDK outside the gateway": (
        "ledger/domain/leak.py",
        "import anthropic\n",
    ),
    "domain imports its own models": (
        "ledger/domain/leak.py",
        "from hub.ledger.models import LedgerTransaction\n",
    ),
    "business layer imports the wiring": (
        "payments/application/leak.py",
        "from hub.bootstrap import get_container\n",
    ),
}


def run_lint(
    cwd: Path, *, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(LINT_IMPORTS), "--config", str(CONFIG)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        env={**_base_env(), **(env_extra or {})},
    )


def _base_env() -> dict[str, str]:
    return {**os.environ, "DJANGO_SETTINGS_MODULE": "hub.settings.test"}


def test_lint_imports_passes_on_skeleton() -> None:
    done = run_lint(BACKEND)

    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("case", sorted(VIOLATIONS))
def test_contracts_catch_violations(case: str, tmp_path: Path) -> None:
    relative, source = VIOLATIONS[case]
    copy_src = tmp_path / "src"
    shutil.copytree(HUB_SRC, copy_src / "hub", ignore=shutil.ignore_patterns("__pycache__"))
    # A forbidden module only becomes a graph edge when it resolves inside the package:
    # `hub.ledger.models` arrives with a later slice, so the copy needs the stub for the
    # "domain imports its own models" case to be meaningful at all.
    (copy_src / "hub" / "ledger" / "models.py").write_text(
        "class LedgerTransaction:\n    pass\n", encoding="utf-8"
    )
    leak = copy_src / "hub" / relative
    leak.parent.mkdir(parents=True, exist_ok=True)
    leak.write_text(source, encoding="utf-8")

    done = run_lint(tmp_path, env_extra={"PYTHONPATH": str(copy_src)})

    assert done.returncode != 0, f"{case} was not caught:\n{done.stdout}"
    assert "Contracts: 0 kept" in done.stdout or "broken" in done.stdout.lower(), done.stdout


def _admin_imports(root: Path) -> list[str]:
    """Every ``django.contrib.admin`` import under ``root`` (AST, so it cannot be dodged)."""
    found: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(
                name == "django.contrib.admin" or name.startswith("django.contrib.admin.")
                for name in names
            ):
                found.append(f"{path}:{node.lineno}")
    return found


def test_no_module_imports_django_admin(tmp_path: Path) -> None:
    """The admin ban import-linter cannot express: only the root of an external package is allowed."""
    assert _admin_imports(HUB_SRC) == []

    copy = tmp_path / "hub"
    shutil.copytree(HUB_SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "identity" / "domain" / "leak.py").write_text(
        "from django.contrib.admin import site\n", encoding="utf-8"
    )
    assert _admin_imports(copy) != []


def test_generated_file_is_current() -> None:
    done = subprocess.run(
        [sys.executable, str(GEN), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr


def test_every_module_in_check_traceability_exists_as_package() -> None:
    directories = {path.name for path in HUB_SRC.iterdir() if path.is_dir()}
    business = directories - TECHNICAL_PACKAGES - {"__pycache__"}

    assert set(MODULES) - {"shared"} == business
    assert "shared" in MODULES and "shared" in directories
