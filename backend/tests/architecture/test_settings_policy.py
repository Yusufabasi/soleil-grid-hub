"""Settings policy for every deployable (TC-NFR-06-9, docs/03 section 9).

The six settings modules are loaded with a fixture environment and inspected directly:
no Django admin or session apps, ATOMIC_REQUESTS off on every alias, no CSRF or session
middleware, JSON renderer only, and exactly one database role per module (docs/04 10.10).
"""

import importlib
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
MODULES = ["api", "ingest", "worker", "stream", "migrate", "test"]

# A dev-only value: long enough and varied enough to satisfy the deploy check (security.W009).
SECRET = "test-only-secret-key-not-used-anywhere-0123456789-abcdef"

ENV = {
    "HUB_ENV": "test",
    "HUB_SECRET_KEY": SECRET,
    "HUB_ALLOWED_HOSTS": "localhost,hub-api",
    "HUB_DB_API_URL": "postgresql+psycopg://hub_api:dev@localhost:5432/hub_rw",
    "HUB_DB_API_REPLICA_URL": "postgresql+psycopg://hub_api:dev@localhost:5432/hub_ro",
    "HUB_DB_INGEST_URL": "postgresql+psycopg://hub_ingest:dev@localhost:5432/hub_rw",
    "HUB_DB_WORKER_URL": "postgresql+psycopg://hub_worker:dev@localhost:5432/hub_rw",
    "HUB_DB_AI_URL": "postgresql+psycopg://ai_gateway:dev@localhost:5432/hub_rw",
    "HUB_MIGRATE_URL": "postgresql+psycopg://migrator:dev@localhost:5432/hub",
    "HUB_TEST_DB_URL": "postgresql+psycopg://hub_api:dev@localhost:5432/hub_test",
    "HUB_INGEST_ROLE": "webhooks",
    "HUB_REDIS_URL": "redis://localhost:6379/0",
    "SITE_ORIGIN": "http://localhost:8080",
}

FORBIDDEN_APPS = (
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.contenttypes",
)


@pytest.fixture
def load(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)

    def _load(module: str):
        for cached in [m for m in list(sys.modules) if m.startswith("hub.settings")]:
            del sys.modules[cached]
        return importlib.import_module(f"hub.settings.{module}")

    return _load


@pytest.mark.parametrize("module", MODULES)
def test_no_admin_auth_sessions_messages_or_contenttypes(load, module):
    settings = load(module)

    offenders = {app for app in settings.INSTALLED_APPS if app.startswith(FORBIDDEN_APPS)}

    assert offenders == set()
    assert "rest_framework" in settings.INSTALLED_APPS


@pytest.mark.parametrize("module", MODULES)
def test_atomic_requests_is_off_everywhere(load, module):
    settings = load(module)

    assert all(database.get("ATOMIC_REQUESTS") is False for database in settings.DATABASES.values())
    assert all(database.get("AUTOCOMMIT", True) is True for database in settings.DATABASES.values())


@pytest.mark.parametrize("module", MODULES)
def test_no_csrf_or_session_middleware(load, module):
    settings = load(module)

    assert not any("Csrf" in entry or "Session" in entry for entry in settings.MIDDLEWARE), (
        settings.MIDDLEWARE
    )
    # The request context middleware runs first (docs/03 section 7.3).
    assert settings.MIDDLEWARE[0].endswith("RequestContextMiddleware")
    assert settings.MIDDLEWARE[-1].endswith("DomainErrorMiddleware")


@pytest.mark.parametrize("module", MODULES)
def test_json_renderer_only_and_no_unauthenticated_user(load, module):
    settings = load(module)

    assert settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] == [
        "rest_framework.renderers.JSONRenderer"
    ]
    assert settings.REST_FRAMEWORK["UNAUTHENTICATED_USER"] is None
    assert settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] == []
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == []
    assert settings.REST_FRAMEWORK["EXCEPTION_HANDLER"] == (
        "hub.shared.api.problem.exception_handler"
    )
    assert "browsable_api" not in str(settings.INSTALLED_APPS)


@pytest.mark.parametrize(
    ("module", "role"),
    [("api", "hub_api"), ("worker", "hub_worker"), ("ingest", "hub_ingest")],
)
def test_each_module_connects_with_its_own_role(load, module, role):
    settings = load(module)
    user = settings.DATABASES["default"]["USER"]
    port = settings.DATABASES["default"]["PORT"]
    name = settings.DATABASES["default"]["NAME"]

    assert user == role, f"{module} connects as {user}, expected {role}"
    # django-environ hands the port back as an int; Django accepts both.
    assert (str(port), name) == ("5432", "hub_rw")


def test_stream_reads_the_replica_url(load):
    settings = load("stream")
    url = ENV["HUB_DB_API_REPLICA_URL"]

    assert settings.DATABASES["default"]["HOST"] == "localhost"
    assert settings.DATABASES["default"]["NAME"] == "hub_ro"
    assert url.endswith("/hub_ro")


def test_secret_key_comes_from_the_environment_only(load):
    for module in MODULES:
        settings = load(module)

        assert settings.SECRET_KEY == SECRET
        assert settings.SECRET_KEY not in _python_literals()


def _python_literals() -> set[str]:
    """Every string literal in the backend sources, to prove SECRET_KEY is never hard-coded."""
    found: set[str] = set()
    for path in (BACKEND / "src").rglob("*.py"):
        for match in re.findall(r'"([^"\n]{6,})"', path.read_text()):
            found.add(match)
    return found


@pytest.mark.parametrize(
    ("role", "urlconf"),
    [("webhooks", "hub.urls.webhooks"), ("telemetry", "hub.urls.telemetry")],
)
def test_ingest_role_selects_the_urlconf(load, monkeypatch, role, urlconf):
    monkeypatch.setenv("HUB_INGEST_ROLE", role)
    for cached in [m for m in list(sys.modules) if m.startswith("hub.settings")]:
        del sys.modules[cached]
    settings = importlib.import_module("hub.settings.ingest")

    assert settings.ROOT_URLCONF == urlconf


def test_ingest_requires_a_role(load, monkeypatch):
    monkeypatch.delenv("HUB_INGEST_ROLE", raising=False)
    for cached in [m for m in list(sys.modules) if m.startswith("hub.settings")]:
        del sys.modules[cached]

    with pytest.raises(Exception) as excinfo:
        importlib.import_module("hub.settings.ingest")

    assert "HUB_INGEST_ROLE" in str(excinfo.value)


def test_debug_is_off_outside_local(load):
    assert load("api").DEBUG is False
    assert load("test").DEBUG is False
