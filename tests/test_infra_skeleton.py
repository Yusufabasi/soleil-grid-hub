"""Root-level infrastructure assertions for the walking skeleton (slice 00).

Task 00.1: repository scaffolding, tooling and Makefile. Task 00.10 adds the balancer,
compose, PgBouncer and stub assertions; Task 00.11 adds the CI and verify-script ones.
"""

import configparser
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

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


# --- Task 00.10: balancer, compose stack, PgBouncer, stubs ------------------------------

# The five pools of docs/09 1.4: one per deployable family, no more.
HAPROXY_BACKENDS = {"be_webhooks", "be_telemetry", "be_stream", "be_api", "be_web"}

# The Python deployables: one image, one process type each (docs/03 1.1, docs/09 2.5).
PYTHON_SERVICES = {
    "hub-api",
    "hub-ingest-webhooks",
    "hub-ingest-telemetry",
    "hub-stream",
    "hub-worker",
    "hub-relay",
    "hub-scheduler",
    "migrate",
}

# Every variable that carries a connection string (docs/04 10.10). `migrate` is spelled
# HUB_MIGRATE_URL rather than HUB_DB_*, so the family is listed instead of prefix-matched.
DB_URL_VARIABLES = {
    "HUB_DB_API_URL",
    "HUB_DB_API_REPLICA_URL",
    "HUB_DB_INGEST_URL",
    "HUB_DB_WORKER_URL",
    "HUB_DB_AI_URL",
    "HUB_MIGRATE_URL",
}

# Each deployable reads its own role's connection string and no other (docs/04 10.10).
ROLE_URLS = {
    "hub-api": {"HUB_DB_API_URL", "HUB_DB_API_REPLICA_URL"},
    "hub-ingest-webhooks": {"HUB_DB_INGEST_URL"},
    "hub-ingest-telemetry": {"HUB_DB_INGEST_URL"},
    "hub-stream": {"HUB_DB_API_REPLICA_URL"},
    "hub-worker": {"HUB_DB_WORKER_URL"},
    "hub-relay": {"HUB_DB_WORKER_URL"},
    "hub-scheduler": {"HUB_DB_WORKER_URL"},
    "migrate": {"HUB_MIGRATE_URL"},
}

COMPOSE_SERVICES = {
    "lb",
    "hub-api",
    "hub-ingest-webhooks",
    "hub-ingest-telemetry",
    "hub-stream",
    "hub-web",
    "hub-worker",
    "hub-relay",
    "hub-scheduler",
    "migrate",
    "pg-primary",
    "pgbouncer",
    "redis",
    "minio",
    "otel-collector",
    "provider-stub",
    "meter-sim",
}


def parse_haproxy(text: str) -> dict[str, object]:
    """The parts of the balancer configuration the routing contract depends on."""
    acls: dict[str, tuple[str, list[str]]] = {}
    routes: list[tuple[str, str]] = []
    backends: list[str] = []
    default: str | None = None
    log_format: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("acl "):
            _, name, kind, *values = stripped.split()
            acls[name] = (kind, values)
        elif stripped.startswith("use_backend "):
            _, backend, condition, *names = stripped.split()
            assert condition == "if", stripped
            routes.append((backend, names[0]))
        elif stripped.startswith("default_backend "):
            default = stripped.split()[1]
        elif stripped.startswith("log-format "):
            log_format = stripped.split(" ", 1)[1]
        elif stripped.startswith("backend "):
            backends.append(stripped.split()[1])
    return {
        "acls": acls,
        "routes": routes,
        "backends": backends,
        "default": default,
        "log_format": log_format,
    }


def test_haproxy_has_exactly_five_pools_and_routes():
    config = parse_haproxy((ROOT / "infra/lb/haproxy.cfg").read_text())
    acls = config["acls"]
    assert isinstance(acls, dict)

    assert set(config["backends"]) == HAPROXY_BACKENDS  # type: ignore[arg-type]
    assert len(config["backends"]) == 5  # type: ignore[arg-type]

    assert acls["is_webhooks"] == ("path_beg", ["/api/v1/webhooks/providers/"])
    assert acls["is_telemetry"][0] == "path_beg"
    assert set(acls["is_telemetry"][1]) == {"/api/v1/telemetry/", "/api/v1/gateway/"}
    assert acls["is_sse"] == ("path", ["/api/v1/dashboard/stream"])
    assert acls["is_api"] == ("path_beg", ["/api/"])

    routes = config["routes"]
    # First match wins, so ingest must be decided before the /api/ catch-all and the long
    # lived stream before the api pool (docs/09 1.4).
    assert routes == [  # type: ignore[comparison-overlap]
        ("be_webhooks", "is_webhooks"),
        ("be_telemetry", "is_telemetry"),
        ("be_stream", "is_sse"),
        ("be_api", "is_api"),
    ]
    assert config["default"] == "be_web"

    # D-18: the access log carries the path only. A single-use stream ticket lives in the
    # query string, so the raw request line (%r), the full URL (%HU) and the query string
    # (%HQ) must never be logged.
    log_format = config["log_format"]
    assert isinstance(log_format, str)
    assert "%HP" in log_format, log_format
    for forbidden in ("%HQ", "%HU", "%r"):
        assert forbidden not in log_format, (forbidden, log_format)
    assert "%ST" in log_format


def compose() -> dict[str, object]:
    return yaml.safe_load((ROOT / "infra/docker/compose.yaml").read_text())


def services() -> dict[str, dict[str, object]]:
    loaded = compose()
    assert isinstance(loaded, dict)
    found = loaded["services"]
    assert isinstance(found, dict)
    return found


def environment_of(service: dict[str, object]) -> dict[str, str]:
    """The merged environment block as a plain mapping (compose allows list or mapping)."""
    raw = service.get("environment") or {}
    if isinstance(raw, list):
        pairs = [item.partition("=")[::2] for item in raw]
        return {name: value for name, value in pairs}
    return {str(name): str(value) for name, value in raw.items()}


def test_compose_services_and_security():
    stack = services()

    assert COMPOSE_SERVICES <= set(stack), sorted(COMPOSE_SERVICES - set(stack))

    # Every hub-* container runs from a read-only root with no capabilities at all: a
    # compromised process cannot write its image or gain one back through a setuid binary.
    for name, service in stack.items():
        if not name.startswith("hub-"):
            continue
        assert service.get("read_only") is True, name
        assert service.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in (service.get("security_opt") or []), name

    # hub-web holds no database credential and no secret at all (D-44).
    web_env = environment_of(stack["hub-web"])
    assert web_env, "hub-web needs its environment block"
    for key in web_env:
        assert key.startswith("NEXT_PUBLIC_") or key.startswith("OTEL_") or key in {
            "HOSTNAME",
            "PORT",
        }, key

    # Only the database containers and the Python deployables may see a connection string.
    allowed = PYTHON_SERVICES | {"pg-primary", "pgbouncer"}
    for name, service in stack.items():
        if name in allowed:
            continue
        keys = set(environment_of(service))
        assert "HUB_DB_" not in str(service), name
        assert not keys & DB_URL_VARIABLES, (name, sorted(keys & DB_URL_VARIABLES))

    # One role URL per deployable, and no other role's URL.
    for name in PYTHON_SERVICES:
        keys = set(environment_of(stack[name])) & DB_URL_VARIABLES
        assert keys == ROLE_URLS[name], (name, sorted(keys))

    # Application containers start only after the schema exists and the roles can log in.
    for name in PYTHON_SERVICES - {"migrate"}:
        depends = stack[name].get("depends_on") or {}
        assert depends.get("db-logins", {}).get("condition") == (
            "service_completed_successfully"
        ), name
    logins = stack["db-logins"]
    assert logins.get("depends_on", {}).get("migrate", {}).get("condition") == (
        "service_completed_successfully"
    )


def test_stub_placeholders_reference_slices():
    stack = services()

    for name, slice_name in (("provider-stub", "slice-T1"), ("meter-sim", "slice-T2")):
        service = stack[name]
        assert service.get("profiles") == ["stubs"], name
        assert slice_name in " ".join(str(part) for part in service["command"]), name


def test_pgbouncer_ini():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ROOT / "infra/docker/pgbouncer/pgbouncer.ini")

    pgbouncer = parser["pgbouncer"]
    assert pgbouncer["pool_mode"] == "transaction"
    # Prepared statements stay off in transaction pooling (docs/04 10.10, A-13).
    assert int(pgbouncer.get("max_prepared_statements", "0")) == 0
    assert set(parser["databases"]) == {"hub_rw", "hub_ro", "hub_analytics"}


def test_env_example_has_no_real_secrets():
    gitignore = (ROOT / ".gitignore").read_text()
    assert re.search(r"^\.env$", gitignore, re.MULTILINE), ".env must be git-ignored"

    values = env_values(ROOT / ".env.example")
    offending = {
        name: value for name, value in values.items() if not is_dev_value(name, value)
    }

    assert offending == {}, f".env.example carries non dev values: {offending}"
