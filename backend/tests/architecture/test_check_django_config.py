"""The CI configuration checker (docs/03 section 9, NFR-14 gate).

Each violation the slice names is built in a temporary tree and must make
``check_django_config.main`` return a non-zero exit code; a clean tree must return 0.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))

import check_django_config  # noqa: E402

CLEAN_SETTINGS = """\
SECRET_KEY = "x" * 60
DEBUG = False

INSTALLED_APPS = [
    "hub.shared",
    "hub.hubdb",
    "rest_framework",
    "django.contrib.postgres",
]

MIDDLEWARE = [
    "hub.shared.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "hub.shared.middleware.DomainErrorMiddleware",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
    }
}
"""


def build(root: Path, *, settings: str = CLEAN_SETTINGS, files: dict[str, str] | None = None):
    (root / "backend" / "src" / "hub" / "settings").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "src" / "hub" / "settings" / "base.py").write_text(settings)
    (root / "backend" / "uv.lock").write_text('name = "hub"\n')
    for name, content in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def run(root: Path) -> int:
    return check_django_config.main(["--root", str(root)])


def test_violation_admin_in_installed_apps(tmp_path):
    settings = CLEAN_SETTINGS.replace(
        '    "rest_framework",\n', '    "rest_framework",\n    "django.contrib.admin",\n'
    )

    assert run(build(tmp_path, settings=settings)) != 0


def test_violation_atomic_requests_true(tmp_path):
    settings = CLEAN_SETTINGS.replace('"ATOMIC_REQUESTS": False', '"ATOMIC_REQUESTS": True')

    assert run(build(tmp_path, settings=settings)) != 0


def test_violation_model_without_managed_false(tmp_path):
    model = """\
from django.db import models

class Customer(models.Model):
    id = models.AutoField(primary_key=True)

    class Meta:
        db_table = "customers"
"""
    files = {"backend/src/hub/customers/infrastructure/models.py": model}

    assert run(build(tmp_path, files=files)) != 0


def test_violation_model_outside_infrastructure(tmp_path):
    model = """\
from django.db import models

class Customer(models.Model):
    id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = "customers"
"""
    files = {"backend/src/hub/customers/models.py": model}

    assert run(build(tmp_path, files=files)) != 0


def test_violation_use_server_in_frontend(tmp_path):
    files = {"frontend/src/app/page.tsx": '"use server";\nexport default function P() {}\n'}

    assert run(build(tmp_path, files=files)) != 0


def test_violation_next_public_secret_name(tmp_path):
    files = {"frontend/src/config.ts": "export const k = process.env.NEXT_PUBLIC_SECRET_X;\n"}

    assert run(build(tmp_path, files=files)) != 0


def test_violation_celery_in_lock_file(tmp_path):
    root = build(tmp_path)
    (root / "backend" / "uv.lock").write_text('name = "celery"\nversion = "5.4.0"\n')

    assert run(root) != 0


def test_clean_tree_passes(tmp_path):
    assert run(build(tmp_path)) == 0
