#!/usr/bin/env python3
"""CI configuration checker (docs/03 section 9, executor guide forbidden shortcuts).

Exits non-zero, printing one line per violation, when any of these is true:

* ``django.contrib.admin``, ``auth``, ``sessions``, ``messages`` or ``contenttypes``
  appears in ``INSTALLED_APPS`` of a settings module;
* ``ATOMIC_REQUESTS`` is true for any database alias;
* ``CsrfViewMiddleware`` or ``SessionMiddleware`` is configured;
* a model lacks ``managed = False``, or a concrete model is declared outside
  ``infrastructure/models.py``;
* the frontend contains ``"use server"`` (Next.js Server Actions, D-46);
* a ``NEXT_PUBLIC_`` variable name looks like a secret (D-44);
* the lock file lists ``celery``, ``sqlalchemy`` or ``fastapi`` (ADR-0019, ADR-0020);
* ``manage.py makemigrations --check --dry-run`` would create a migration;
* ``manage.py check --deploy --fail-level WARNING`` warns for a settings module.

Usage: ``python tools/check_django_config.py [--root PATH]``.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

SETTINGS_MODULES = ("api", "ingest", "worker", "stream", "migrate", "test")

FORBIDDEN_APPS = (
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.contenttypes",
)

FORBIDDEN_MIDDLEWARE = ("CsrfViewMiddleware", "SessionMiddleware")

FORBIDDEN_LOCK_PACKAGES = ("celery", "sqlalchemy", "fastapi")

FRONTEND_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

USE_SERVER = re.compile(r"""["']use server["']""")

SECRETISH_PUBLIC_NAME = re.compile(
    r"NEXT_PUBLIC_[A-Z0-9_]*(SECRET|KEY|TOKEN|PASSWORD|PRIVATE)"
)

MODEL_BASE_NAMES = frozenset({"Model"})

# Fallbacks so the Django subprocess checks can run in a checkout without a .env file.
# Values are development placeholders; nothing here is a credential.
FALLBACK_ENV = {
    "HUB_ENV": "production",
    "HUB_SECRET_KEY": "checker-only-not-a-secret-0123456789-abcdefghijklmno",
    "HUB_ALLOWED_HOSTS": "localhost",
    "SITE_ORIGIN": "http://localhost:8080",
    "HUB_REDIS_URL": "redis://localhost:6379/0",
    "HUB_DB_API_URL": "postgresql+psycopg://hub_api:dev@localhost:5432/hub_rw",
    "HUB_DB_API_REPLICA_URL": "postgresql+psycopg://hub_api:dev@localhost:5432/hub_ro",
    "HUB_DB_INGEST_URL": "postgresql+psycopg://hub_ingest:dev@localhost:5432/hub_rw",
    "HUB_DB_WORKER_URL": "postgresql+psycopg://hub_worker:dev@localhost:5432/hub_rw",
    "HUB_DB_AI_URL": "postgresql+psycopg://ai_gateway:dev@localhost:5432/hub_rw",
    "HUB_MIGRATE_URL": "postgresql+psycopg://migrator:dev@localhost:5432/hub",
    "HUB_TEST_DB_URL": "postgresql+psycopg://hub_api:dev@localhost:5432/hub_test",
    "HUB_INGEST_ROLE": "webhooks",
}


# --------------------------------------------------------------------------- settings
def _assignment_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _value(node: ast.AST) -> ast.AST | None:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return node.value
    return None


def _string_constants(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def check_settings(root: Path) -> list[str]:
    problems: list[str] = []
    settings_dir = root / "backend" / "src" / "hub" / "settings"
    for path in sorted(settings_dir.glob("*.py")):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                problems.extend(_dict_atomic_requests(path, node))
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "ATOMIC_REQUESTS" and _is_true(keyword.value):
                        problems.append(
                            f"{path}: ATOMIC_REQUESTS = True (D-42 requires False)"
                        )

            names = _assignment_names(node)
            value = _value(node)
            if value is None:
                continue

            if "INSTALLED_APPS" in names:
                for app in _string_constants(value):
                    if app in FORBIDDEN_APPS:
                        problems.append(
                            f"{path}: INSTALLED_APPS installs {app} (D-42: never installed)"
                        )

            if "MIDDLEWARE" in names:
                for entry in _string_constants(value):
                    for forbidden in FORBIDDEN_MIDDLEWARE:
                        if forbidden in entry:
                            problems.append(
                                f"{path}: MIDDLEWARE configures {forbidden} (docs/03 1.2 rule 3)"
                            )

            if "ATOMIC_REQUESTS" in names and _is_true(value):
                problems.append(f"{path}: ATOMIC_REQUESTS = True (D-42 requires False)")
    return problems


def _dict_atomic_requests(path: Path, node: ast.Dict) -> list[str]:
    problems: list[str] = []
    for key, value in zip(node.keys, node.values, strict=False):
        if (
            isinstance(key, ast.Constant)
            and key.value == "ATOMIC_REQUESTS"
            and _is_true(value)
        ):
            problems.append(f"{path}: ATOMIC_REQUESTS = True (D-42 requires False)")
    return problems


def _is_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


# ---------------------------------------------------------------------------- models
def _derives_from_model(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Attribute) and base.attr in MODEL_BASE_NAMES:
            return True
        if isinstance(base, ast.Name) and base.id in MODEL_BASE_NAMES:
            return True
    return False


def _meta_flag(node: ast.ClassDef, flag: str) -> bool:
    for child in node.body:
        if isinstance(child, ast.ClassDef) and child.name == "Meta":
            for statement in child.body:
                names = _assignment_names(statement)
                if flag in names:
                    value = _value(statement)
                    if isinstance(value, ast.Constant):
                        return bool(value.value)
    return False


def check_models(root: Path) -> list[str]:
    problems: list[str] = []
    package = root / "backend" / "src" / "hub"
    if not package.is_dir():
        return problems
    for path in sorted(package.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        tree = _parse(path)
        if tree is None:
            continue
        in_infrastructure = "infrastructure" in path.parts and path.name == "models.py"
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _derives_from_model(node):
                continue
            abstract = _meta_flag(node, "abstract")
            if abstract:
                continue
            if not _meta_flag(node, "managed"):
                problems.append(
                    f"{path}:{node.lineno}: model {node.name} has no "
                    "`class Meta: managed = False` (ADR-0020)"
                )
            if not in_infrastructure:
                problems.append(
                    f"{path}:{node.lineno}: model {node.name} is declared outside "
                    "infrastructure/models.py (docs/03 section 1.2 rule 2)"
                )
    return problems


# -------------------------------------------------------------------------- frontend
def check_frontend(root: Path) -> list[str]:
    problems: list[str] = []
    frontend = root / "frontend"
    if not frontend.is_dir():
        return problems
    for path in sorted(frontend.rglob("*")):
        if not path.is_file() or path.suffix not in FRONTEND_SUFFIXES:
            continue
        if any(part in {"node_modules", ".next"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - binary or vanished
            continue
        if USE_SERVER.search(text):
            problems.append(
                f"{path}: Server Actions are forbidden (D-46); use a route handler or the API"
            )
        for name in SECRETISH_PUBLIC_NAME.findall(text):
            problems.append(
                f"{path}: NEXT_PUBLIC_ variable looks like a secret ({name}) (D-44)"
            )
    return problems


# --------------------------------------------------------------------------- lockfile
def check_lockfile(root: Path) -> list[str]:
    problems: list[str] = []
    for name in (Path("backend") / "uv.lock", Path("uv.lock")):
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for package in FORBIDDEN_LOCK_PACKAGES:
            if re.search(rf"\b{package}\b", text):
                problems.append(
                    f"{path}: {package} is not part of the decided stack (ADR-0019, ADR-0020)"
                )
    return problems


# ------------------------------------------------------------------ django subprocesses
def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _child_env(root: Path) -> dict[str, str]:
    """Fallbacks < .env < process environment; HUB_ENV is pinned so DEBUG stays off."""
    env = {**FALLBACK_ENV, **_read_dotenv(root / ".env"), **os.environ}
    env["HUB_ENV"] = "production"
    return env


def _run(args: list[str], *, cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        args, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    return done.returncode, (done.stdout + done.stderr).strip()


def check_django(root: Path) -> list[str]:
    """Run Django's own gates for every settings module (real repository only)."""
    backend = root / "backend"
    if not (backend / "manage.py").is_file():
        return []

    problems: list[str] = []
    env = _child_env(root)
    python = sys.executable

    for module in SETTINGS_MODULES:
        code, output = _run(
            [
                python,
                "manage.py",
                "check",
                "--deploy",
                "--fail-level",
                "WARNING",
                f"--settings=hub.settings.{module}",
            ],
            cwd=backend,
            env=env,
        )
        if code != 0:
            problems.append(
                f"manage.py check --deploy failed for hub.settings.{module}:\n{output}"
            )

    code, output = _run(
        [
            python,
            "manage.py",
            "makemigrations",
            "--check",
            "--dry-run",
            "--settings=hub.settings.api",
        ],
        cwd=backend,
        env=env,
    )
    if code != 0:
        problems.append(f"manage.py makemigrations --check reported drift:\n{output}")
    return problems


# ------------------------------------------------------------------------------ main
def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover
        print(f"{path}: cannot be parsed: {exc}")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[1]),
        help="repository root (default: this checkout)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)

    problems = [
        *check_settings(root),
        *check_models(root),
        *check_frontend(root),
        *check_lockfile(root),
        *check_django(root),
    ]
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} Django configuration violation(s)")
        return 1
    print("Django configuration checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
