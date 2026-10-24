"""Root-level infrastructure assertions for the walking skeleton (slice 00).

Task 00.1: repository scaffolding, tooling and Makefile. Later tasks add the HAProxy,
compose, CI and verify-script assertions to this file.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_TARGETS = {"up", "down", "reset", "test", "lint", "contracts", "verify", "env", "gen"}

# Names whose value is a credential or key material.
SECRET_NAME = re.compile(r"SECRET|PASSWORD|_PW$|TOKEN|API_KEY|PRIVATE|CREDENTIAL|PEPPER|SALT", re.I)

# Non-secret literal values: booleans, ports, environment names, log levels, process types.
LITERAL = re.compile(
    r"(true|false|yes|no|on|off|"
    r"[0-9]+|"
    r"local|test|staging|production|"
    r"webhooks|telemetry|"
    r"fr|en|"
    r"INFO|WARNING|ERROR|CRITICAL|"
    r"gthread|sync|uvicorn|"
    r"/[A-Za-z0-9._/-]*)"
)

HOSTNAME = re.compile(r"(localhost|127\.0\.0\.1|[a-z][a-z0-9-]*)")


def makefile_targets(path: Path) -> set[str]:
    return set(re.findall(r"^([a-z-]+):", path.read_text(), re.MULTILINE))


def env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip()
    return values


def is_service_url(value: str) -> bool:
    """A URL that points at localhost or a compose service (or the reserved .example TLD)."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    if parts.scheme not in {"http", "https", "redis", "postgresql", "postgresql+psycopg", "s3"}:
        return False
    host = parts.hostname or ""
    if host.endswith(".example"):
        return True
    return HOSTNAME.fullmatch(host) is not None


def is_dev_value(name: str, value: str) -> bool:
    """A value is acceptable when it carries no credential: it is empty, the literal ``dev``
    (or a ``dev``-prefixed name), a URL into the local compose network, a host list of local
    service names, or a non-secret literal (boolean, port, enum, path)."""
    if value == "":
        return True
    if value == "dev" or value.startswith("dev"):
        return True
    if is_service_url(value):
        return True
    if SECRET_NAME.search(name):
        return False
    if "HOST" in name:
        return all(HOSTNAME.fullmatch(part) for part in value.split(","))
    return LITERAL.fullmatch(value) is not None


def test_makefile_declares_required_targets():
    targets = makefile_targets(ROOT / "Makefile")

    assert REQUIRED_TARGETS <= targets, sorted(REQUIRED_TARGETS - targets)


def test_env_example_has_no_real_secrets():
    gitignore = (ROOT / ".gitignore").read_text()
    assert re.search(r"^\.env$", gitignore, re.MULTILINE), ".env must be git-ignored"

    values = env_values(ROOT / ".env.example")
    offending = {
        name: value for name, value in values.items() if not is_dev_value(name, value)
    }

    assert offending == {}, f".env.example carries non dev values: {offending}"
