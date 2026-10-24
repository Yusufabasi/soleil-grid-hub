# Slice 00: Walking skeleton and infrastructure

Author's facts for the write hook: this file is read by the implementing agent and the tech lead; it holds no secrets; nothing else duplicates it; the user instruction is "have your agents stop after making all the plans and not start writing the actual code".

- **Goal:** `make up` starts the whole local stack (Postgres, PgBouncer, Redis, MinIO, HAProxy with five pools, the Django deployables, a `hub-web` stub, stub placeholders). Health endpoints answer through the balancer; CI blocks architecture and Django-configuration violations; logs, request ids, metrics and traces exist from the first request.
- **Depends on:** nothing (first slice). Contract files already present: `db/schema.sql`, `db/invariant_tests.sql`, `api/openapi.yaml`, `api/permissions.yaml`, the three root tests and `tools/check_traceability.py`.
- **Requirements served:** NFR-14 (one-command environment, alignment and migration gates), NFR-09 (logs, metrics, traces baseline), NFR-06 (Django hardening checks, secrets hygiene), NFR-01 (health and pool groundwork), NFR-12 (container based), FR-15 and FR-16 groundwork only (no behaviour).
- **Size:** L, about 12 working days for a cheaper agent (12 tasks).
- **Companion files:** frontend shell [fe-00-shell.md](frontend/fe-00-shell.md) (the Next.js app itself); test-double plans `slice-T1-provider-stub.md` and `slice-T2-meter-simulator.md` (to be written by another author; this slice only reserves compose placeholders for them).
- **Spikes or open items:** none blocking. `[open]` default used here: dev passwords are the literal `dev` values in `.env.example`, local only. `/me` (`getMe`) is not built here because it needs authentication (slice 01); see plan notes.

## Read first

- `docs/00-shared-context.md#7.7 Stack and delivery model (added after the user stated their preferred stack)` (D-42 to D-50) and `#7.1 Authority and naming` (D-04)
- `docs/plan/00-executor-guide.md#4. Working rules (non-negotiable)` and `#5. Commands you will rely on`
- `docs/03-code-architecture.md#1. Monorepo layout`, `#1.1 One codebase, deployables and how they differ`, `#1.2 Layout of a module (a Django app)`, `#2.1 shared (kernel)`
- `docs/03-code-architecture.md#3.3 Enforcement with import-linter`, `#7.1 Error taxonomy`, `#7.2 Configuration`, `#7.3 Logging, tracing, metrics`, `#9. Coding standards and definition of done`
- `docs/04-data-model.md#10.4 db_table and search_path together`, `#10.5 SQL migrations and the Django wrapper (ADR-0020)`, `#10.10 Connections: settings modules, roles, PgBouncer`, `#4.1 Roles`
- `docs/09-devops-sre.md#1.3 Compose sketch`, `#1.4 Load balancer configuration`, `#2.2 Pipeline stages`, `#2.5 Images and process types`, `#4.8 Web and application hardening in deployment`, `#5.2 Logs`, `#5.6 Correlation ids and log data hygiene`
- `docs/10-test-strategy.md#2.11 Stack-specific controls (D-42 to D-46)`, `#8. Traceability matrix`
- `docs/adr/0020-sql-first-schema-unmanaged-django-models.md`

## Global constraints

- Django `5.2` LTS or later 5.x, Python `3.12`+, psycopg 3; gunicorn for api, webhooks, telemetry; uvicorn (ASGI) for the stream pool only.
- `ATOMIC_REQUESTS = False` on every database alias of every settings module (D-42).
- `django.contrib.admin`, `auth`, `sessions`, `messages`, `contenttypes` never in `INSTALLED_APPS`; no `CsrfViewMiddleware`, no `SessionMiddleware`; no browsable API (JSON renderer only).
- Every model is `managed = False` with a bare `db_table`; `manage.py makemigrations` is never run; no `search_path` set in code (role setting, D-43).
- Settings modules: `hub.settings.api`, `.ingest`, `.worker`, `.stream`, `.migrate`, `.test`, all importing `hub.settings.base`; each reads only its own role's URL.
- Health paths exactly `/api/v1/health/live` and `/api/v1/health/ready` on every Django deployable; no other HTTP route is added (no metrics route, no test-only route, D-38).
- Balancer pools exactly `be_webhooks`, `be_telemetry`, `be_stream`, `be_api`, `be_web`; `/api/v1/webhook-events` stays on `be_api`; `/api/v1/gateway/*` goes to `be_telemetry` (D-04).
- Ids come from `IdGenerator` (UUIDv7), time from `Clock` (UTC, tz-aware); `datetime.now`, `uuid.uuid4`, `random` are banned in `domain` and `application` by ruff.
- Never log passwords, OTPs, tokens, API keys, `Authorization`/signature headers, full phone numbers, emails, message bodies, prompts, raw webhook bodies, stream tickets (D-18).
- No `.env`, secret or generated credential is committed; `.env` is git-ignored; the Next.js container receives no secrets (D-44).
- Problem documents are `application/problem+json` with `{type, title, status, code, detail, instance, request_id, errors[]}`; `Retry-After` on 429 and 503.
- Conventional commits; one commit per task; contract tests (`tests/test_alignment.py`, `tests/test_schema_in_postgres.py`, `tools/check_traceability.py`) stay green.

## Review focus

1. **A hostile or oversized `X-Request-Id` reaches logs and response headers** (log injection, header splitting). Pinned by `test_untrusted_request_id_is_replaced` in `backend/tests/unit/shared/test_request_context.py`. Owner: Task 00.4.
2. **A secret or phone number appears in a log line through a nested dict, an exception message or a URL query** (`?ticket=`). Pinned by `test_redaction_covers_nested_values_exceptions_and_ticket_query` in `backend/tests/unit/shared/test_logging_redaction.py`. Owner: Task 00.4.
3. **Readiness lies**: Postgres down still returns 200, or Redis down returns 503 (docs/09 says Redis degrades), or a draining instance stays ready. Pinned by `test_ready_503_when_postgres_down`, `test_ready_200_degraded_when_redis_down`, `test_ready_503_when_draining_file_exists` in `backend/tests/integration/shared/test_health.py`. Owner: Task 00.6.
4. **Outbox row lost or written outside the business transaction** (rollback must discard staged events; clean exit must commit them atomically). Pinned by `test_outbox_rows_roll_back_with_the_transaction` in `backend/tests/integration/shared/test_uow.py`. Owner: Task 00.7.
5. **The migration chain drifts from `db/schema.sql`** or someone adds a managed model or enables the admin. Pinned by `test_migrated_catalog_equals_schema_sql` in `backend/tests/integration/hubdb/test_migrations_consistent.py` and `test_config_checker_rejects_each_violation` in `backend/tests/architecture/test_check_django_config.py`. Owners: Tasks 00.8 and 00.2.

## Files

Paths follow `docs/03-code-architecture.md#1. Monorepo layout`. Create only what is listed.

**Root**
- `Makefile`: targets `up`, `down`, `reset`, `test`, `lint`, `contracts`, `verify SLICE=NN`, `env`, `gen`.
- `.gitignore`, `.env.example` (documented variable list, dev-only values), `.gitleaks.toml` (allowlist for `.env.example` only), `.github/workflows/ci.yml`, `renovate.json`.
- `tools/check_django_config.py`: CI checker (see Interfaces).
- `tools/scripts/verify_slice.sh`: runs the commands of one slice (reads section "Verification" commands for `00`).
- `tools/scripts/gen_importlinter.py`: generates the per-pair public-surface contract into `backend/.importlinter`.
- `tools/scripts/gen_dev_userlist.sh`: writes `infra/docker/pgbouncer/userlist.txt` (git-ignored) from `.env`.
- `tools/provider_stub/README.md`, `tools/meter_simulator/README.md`: one line each naming slice T1 and T2.
- `tests/test_infra_skeleton.py`: root-level infra assertions (compose, HAProxy, CI file, Makefile).

**Backend** (`backend/`)
- `pyproject.toml`, `uv.lock`, `manage.py`, `.importlinter`, `Dockerfile`, `deploy/gunicorn.py`, `deploy/entrypoint-drain.sh`.
- `src/hub/__init__.py`, `bootstrap.py` (container), `wsgi_api.py`, `wsgi_ingest.py`, `asgi_stream.py`.
- `src/hub/settings/{__init__,base,api,ingest,worker,stream,migrate,test}.py`.
- `src/hub/urls/{__init__,api,webhooks,telemetry,stream}.py`.
- `src/hub/shared/{__init__,apps,config,ids,clock,ports,errors,context,logging,metrics,tracing,middleware,uow,events,db}.py`.
- `src/hub/shared/api/{__init__,health,problem,urls}.py`.
- `src/hub/shared/management/commands/{run_worker,run_relay,run_scheduler}.py` (no-op loops).
- `src/hub/hubdb/{__init__,apps,sqlfiles}.py`, `hubdb/migrations/{__init__,0000_baseline}.py`.
- For each of the twelve other modules `identity customers payments ledger meters telemetry support ai_gateway analytics notifications reconciliation admin`: `__init__.py`, `apps.py` (label equals module name, `admin` uses `hub_admin`), `contracts.py`, `events.py` (docstring only), and empty packages `api/`, `application/`, `domain/`, `infrastructure/` with `__init__.py`. No `models.py` yet.
- `tests/{unit,integration,architecture,contract}/` with `conftest.py` (session Postgres fixture using testcontainers `postgres:16`, loads `db/schema.sql`).

**Database** (`db/`)
- `db/baseline/0000_baseline.sql` (byte copy of `db/schema.sql`), `db/baseline/SHA256` (written at first release only, not now), `db/migrations/.gitkeep`, `db/seed/.gitkeep`.

**Infrastructure** (`infra/`)
- `infra/docker/compose.yaml`, `compose.web.yaml` (override building the real `hub-web` image per fe-00), `.dockerignore`, `pg-init/00-create-db.sh`, `pg-init/logins.sql` (dev LOGIN and passwords from env), `pgbouncer/pgbouncer.ini`, `hub-web-stub/default.conf`.
- `infra/lb/haproxy.cfg`.
- `infra/observability/otel-collector.yaml`, `prometheus.yml`.

## Interfaces

**Consumes:** no earlier slice. Consumes frozen files: `db/schema.sql` (roles `hub_api`, `hub_ingest`, `hub_worker`, `ai_gateway`, `migrator`, table `hub.outbox`), `api/openapi.yaml` (`getLiveness`, `getReadiness`, schemas `Health`, `Problem`).

**Produces (later slices rely on these):**

```python
# hub/shared/ports.py
class Clock(Protocol):
    def now(self) -> datetime: ...                 # tz-aware UTC
class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...                  # UUIDv7
# hub/shared/clock.py: SystemClock, FrozenClock(now).advance(delta); hub/shared/ids.py: Uuid7Generator, SequentialIdGenerator (tests)

# hub/shared/config.py
class Secret:  # repr and str are masked "Secret(***)"
    def reveal(self) -> str: ...
env.str(name, default=None) -> str; env.int(...); env.bool(...); env.secret(name) -> Secret
env.db_url(name, *, conn_max_age=0) -> dict          # missing required -> ImproperlyConfigured at import

# hub/shared/errors.py
class DomainError(Exception):  # code, message_key, details
    code: str; message_key: str; details: dict; http_status: int
class ValidationFailed(DomainError)  # 422
class Unauthenticated(DomainError)   # 401
class PermissionDenied(DomainError)  # 403
class NotFound(DomainError)          # 404
class Conflict(DomainError)          # 409
class Duplicate(DomainError)         # 200 on webhooks, 409 elsewhere
class RateLimited(DomainError)       # 429, retry_after_seconds
class ExternalUnavailable(DomainError)  # 502 or 503
class InvariantViolation(DomainError)   # 500, never swallowed
class Unexpected(DomainError)        # 500

# hub/shared/api/problem.py
def to_problem(exc: Exception, *, instance: str, request_id: str) -> tuple[int, dict[str, Any]]: ...
def exception_handler(exc: Exception, context: Mapping[str, Any]) -> Response | None: ...  # DRF
def problem_response(status: int, body: dict) -> HttpResponse: ...                          # plain views
handler404 / handler500                                                                     # URLConf handlers

# hub/shared/uow.py (as docs/03 2.1)
class UnitOfWork(Protocol): outbox; __enter__; __exit__; savepoint(); require_active(); on_commit(fn)
class OutboxWriter(Protocol):
    def add(self, event: DomainEvent) -> None: ...
class DjangoUnitOfWork: def __init__(self, *, using: str = "default", principal: PrincipalContext | None = None): ...
@dataclass(frozen=True) class PrincipalContext: user_id: UUID | None; customer_id: UUID | None
# hub/shared/events.py: DomainEvent frozen dataclass exactly as docs/03 2.1

# hub/shared/api/health.py
def register_readiness_check(name: str, fn: Callable[[], CheckResult], *, critical: bool) -> None: ...
@dataclass(frozen=True) class CheckResult: status: Literal["ok","degraded","down"]; latency_ms: int; detail: str | None

# hub/shared/context.py
request_id_var, correlation_id_var, principal_id_var: ContextVar[str | None]
def bind_context(**kw) -> Token: ...

# hub/shared/logging.py
def configure_logging(*, service: str, level: str = "INFO") -> None: ...
def redact_processor(logger, method_name, event_dict) -> dict: ...
# hub/shared/metrics.py: REGISTRY helpers counter(name, labels), histogram(name, labels, buckets); start_metrics_server(port)
# hub/shared/tracing.py: configure_tracing(service: str) -> None
# hub/hubdb/sqlfiles.py: def sql_text(stem: str) -> str   # reads db/migrations/<stem>.sql; "0000_baseline" reads db/baseline/0000_baseline.sql
# hub/bootstrap.py: build_container(settings) -> Container; get_container() -> Container (empty attributes `clock`, `ids`)
```

**Settings names and environment variables:** `DJANGO_SETTINGS_MODULE`; `HUB_DB_API_URL`, `HUB_DB_API_REPLICA_URL`, `HUB_DB_INGEST_URL`, `HUB_DB_WORKER_URL`, `HUB_MIGRATE_URL`, `HUB_DB_AI_URL` (defined later); `HUB_INGEST_ROLE` (`webhooks` or `telemetry`, required for ingest); `HUB_REDIS_URL`; `HUB_S3_ENDPOINT`; `HUB_SECRET_KEY`; `HUB_ALLOWED_HOSTS`; `HUB_TRUST_LB_REQUEST_ID` (default false; compose sets true); `HUB_ENV` (`local|test|staging|production`); `HUB_VERSION`; `HUB_METRICS_PORT` (default 9102, internal only); `OTEL_EXPORTER_OTLP_ENDPOINT`; `SITE_ORIGIN`. `HUB_DRAIN_FILE` default `/tmp/draining`.

**Make targets:** `up`, `down`, `reset`, `env`, `test`, `lint`, `contracts`, `gen`, `verify SLICE=NN`.

## Tasks

### Task 00.1: Repository scaffolding, tooling and Makefile

- [ ] 1. Write failing test `tests/test_infra_skeleton.py::test_makefile_declares_required_targets` and `::test_env_example_has_no_real_secrets`: parse `Makefile` with a regex `^([a-z-]+):` and assert `{"up","down","reset","test","lint","contracts","verify","env","gen"} <= targets`; assert `.env` is listed in `.gitignore`; assert every value in `.env.example` is either empty, `dev`, a `localhost`/service-name URL, or a `dev`-prefixed string.
- [ ] 2. Run `python -m pytest tests/test_infra_skeleton.py -q`. Expected: fails (`Makefile` not found).
- [ ] 3. Implement. `backend/pyproject.toml` (uv, one distribution `hub`, `src` layout) pinning Django `>=5.2,<5.3`, djangorestframework, psycopg[binary], gunicorn, uvicorn, redis, django-environ, structlog, prometheus-client, opentelemetry-api/sdk/exporter-otlp and instrumentation for django, psycopg, redis, PyYAML, uuid-utils (exact version pinned). Dev group: pytest, pytest-django, pytest-xdist, hypothesis, testcontainers, ruff, mypy, django-stubs, djangorestframework-stubs, import-linter, pip-audit, bandit. Configure ruff (rule sets E, F, I, B, UP, S, DJ, PL subset; ban `datetime.now`, `uuid.uuid4`, `random` in `domain` and `application` through per-file `flake8-banned-api`-style `lint.flake8-tidy-imports.banned-api`), mypy strict with the django plugin and `DJANGO_SETTINGS_MODULE=hub.settings.test`, pytest markers `integration`, `tc(id)`. `Makefile`: `up` = `env`, compose `up -d --wait` with `-f infra/docker/compose.yaml`, then migrate and logins (Task 00.10 supplies the services; until then `up` is declared and documented); `test` = pytest unit and architecture plus frontend unit if `frontend/package.json` exists; `lint` = ruff check, ruff format --check, mypy, `lint-imports`, `python tools/check_django_config.py`, frontend lint when present; `contracts` = root pytest of the three alignment files, `python tools/check_traceability.py`, OpenAPI validation (`python -c` loading with `openapi-spec-validator`); `verify` = `tools/scripts/verify_slice.sh $(SLICE)`; `env` copies `.env.example` to `.env` if absent; `gen` = frontend client generation when present. `.env.example` lists every variable of Interfaces with dev values.
- [ ] 4. Run `python -m pytest tests/test_infra_skeleton.py -q`; expect `2 passed`. Run `make -n lint` to confirm syntax.
- [ ] 5. Commit: `chore(repo): scaffold backend tooling, Makefile and env template`

### Task 00.2: Django project, settings modules, entrypoints and configuration checker

- [ ] 1. Write failing tests in `backend/tests/architecture/test_settings_policy.py`: parametrised over `["api","ingest","worker","stream","migrate","test"]` with `importlib.import_module("hub.settings."+name)` under a fixture env (`monkeypatch.setenv` of the required variables): `assert not {a for a in s.INSTALLED_APPS if a.startswith(("django.contrib.admin","django.contrib.auth","django.contrib.sessions","django.contrib.messages","django.contrib.contenttypes"))}`; `assert all(d["ATOMIC_REQUESTS"] is False for d in s.DATABASES.values())`; `assert not any("Csrf" in m or "Session" in m for m in s.MIDDLEWARE)`; `assert s.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] == ["rest_framework.renderers.JSONRenderer"]`; `assert s.REST_FRAMEWORK["UNAUTHENTICATED_USER"] is None`; per module role: api `DATABASES["default"]["USER"]` equals `hub_api` parsed from `HUB_DB_API_URL`, worker equals `hub_worker`, ingest `hub_ingest`, stream URL equals `HUB_DB_API_REPLICA_URL`; `s.SECRET_KEY` never equals a literal in the repo. Also `test_ingest_requires_role` (missing `HUB_INGEST_ROLE` raises `ImproperlyConfigured`; `webhooks` gives `ROOT_URLCONF == "hub.urls.webhooks"`, `telemetry` gives `hub.urls.telemetry`). In `backend/tests/architecture/test_check_django_config.py`: `test_config_checker_rejects_each_violation` builds temp trees and asserts `check_django_config.main([...]) != 0` for each: admin in INSTALLED_APPS, `ATOMIC_REQUESTS=True`, a model without `managed = False`, a model class outside `infrastructure/models.py`, `"use server"` in a frontend file, a `NEXT_PUBLIC_SECRET_X` name, `celery` in the lock file; and `== 0` for a clean tree.
- [ ] 2. Run `cd backend && python -m pytest tests/architecture -q`. Expected: import errors (`hub.settings` missing).
- [ ] 3. Implement. `base.py`: all 13 apps (`hub.shared`, `hub.hubdb`, the twelve modules) plus `rest_framework` and `django.contrib.postgres`; `AUTH_USER_MODEL` is not set yet (set in slice 01 with the identity model); `USE_TZ=True`, `TIME_ZONE="UTC"`, `DEBUG=False` except `HUB_ENV=local`; `SECURE_PROXY_SSL_HEADER`, `SECURE_HSTS_SECONDS`, `SECURE_CONTENT_TYPE_NOSNIFF`, `SILENCED_SYSTEM_CHECKS=["security.W008"]` plus the CSRF/session middleware warnings (documented in comments); `MIDDLEWARE` = `RequestContextMiddleware`, `django.middleware.security.SecurityMiddleware`, `DomainErrorMiddleware`; `REST_FRAMEWORK` with `EXCEPTION_HANDLER="hub.shared.api.problem.exception_handler"`, JSON renderer only, empty authentication and permission classes (slice 01 installs `PermissionsYamlPermission`), `UNAUTHENTICATED_USER=None`; a helper `build_database(url_var, *, app_name)` returning `ENGINE=django.db.backends.postgresql`, `ATOMIC_REQUESTS=False`, `AUTOCOMMIT=True`, `CONN_MAX_AGE=0`, `DISABLE_SERVER_SIDE_CURSORS=True`, `OPTIONS={"connect_timeout":5,"application_name":app_name}`; plus the `connection_created` handler setting `prepare_threshold=None` (docs/04 10.10). `api.py`, `ingest.py`, `worker.py`, `stream.py`, `migrate.py`, `test.py` per the docs/03 1 table (stream: `default` = replica URL; migrate: `HUB_MIGRATE_URL`, no URLConf; test: extends api, DB from `HUB_TEST_DB_URL`). `urls/*.py` mount only `hub.shared.api.urls` (filled in Task 00.6). Entrypoints set `DJANGO_SETTINGS_MODULE` and use `get_wsgi_application()` / `get_asgi_application()`. `tools/check_django_config.py`: `main(argv=None) -> int` with `--root`; for each of the six settings modules import and check the INSTALLED_APPS, ATOMIC_REQUESTS and middleware rules; AST-scan every `backend/src/hub/*/infrastructure/models.py` for `class Meta` with `managed = False`; AST-scan all other `.py` for `models.Model` subclasses; scan `frontend/` for `use server` and `NEXT_PUBLIC_` names matching `SECRET|KEY|TOKEN|PASSWORD|PRIVATE`; check `uv.lock` for `celery`, `sqlalchemy`, `fastapi`; run `manage.py makemigrations --check --dry-run` and `manage.py check --deploy --fail-level WARNING` per module via subprocess; print one line per violation. Create the twelve module packages as listed in Files.
- [ ] 4. Run `cd backend && python -m pytest tests/architecture -q` until green; run `python tools/check_django_config.py` from root (expect exit 0).
- [ ] 5. Commit: `feat(shared): django project, settings modules and config checker`

### Task 00.3: Clock, IdGenerator, config helpers, container

- [ ] 1. Tests in `backend/tests/unit/shared/test_kernel_ports.py`: `test_uuid7_is_version_7_and_time_ordered` (generate 1000 with a monotonic clock stub, `u.version == 7`, `sorted(ids) == ids`); `test_frozen_clock_advances` (`FrozenClock(t).advance(timedelta(seconds=5)).now() == t + 5s`, tz is UTC); `test_system_clock_is_tz_aware`; in `test_config.py`: `test_secret_repr_is_masked` (`"abc" not in repr(Secret("abc")) and "abc" not in str(Secret("abc"))`), `test_missing_required_raises_improperly_configured`, `test_bool_parsing` (`"true","1","yes"` true; `"false","0",""` false), `test_db_url_parses_role_and_host`.
- [ ] 2. Run `cd backend && python -m pytest tests/unit/shared -q`. Expected: ImportError.
- [ ] 3. Implement `ports.py`, `clock.py`, `ids.py` (wraps the one pinned UUIDv7 library; ordering within a millisecond guaranteed by the library, test stubs time), `config.py` (wrapper over django-environ; `env.secret` returns `Secret`; `db_url` returns the Django dict and is the only place URLs are parsed), `bootstrap.py` (`Container` dataclass with `clock: Clock`, `ids: IdGenerator`; `build_container` is called from `shared` `AppConfig.ready()` once; second call returns the same object).
- [ ] 4. Run until `passed`.
- [ ] 5. Commit: `feat(shared): clock, id generator, typed config and container`

### Task 00.4: Structured logging with redaction, request context, metrics, tracing

- [ ] 1. Tests. `backend/tests/unit/shared/test_logging_redaction.py`: `test_redaction_masks_denylisted_keys` (keys `password, otp, token, access_token, refresh_token, authorization, api_key, secret, signature, x_hub_signature, body, prompt, email, phone` become `"[redacted]"`); `test_redaction_covers_nested_values_exceptions_and_ticket_query` (event dict `{"a":{"b":[{"password":"x"}]}}` masked; `exc_info` text containing `+243812345678` masked; a URL `/api/v1/dashboard/stream?ticket=abc123` logged as `/api/v1/dashboard/stream`); `test_value_patterns` (JWT `eyJhbGciOi.payloadpart.sigpart` becomes `[jwt]`; `sgh_prod_k1_secretvalue` becomes `[api_key]`; `+243812345678` becomes `msisdn:` plus 8 hex chars of its sha256; `anna@example.org` becomes `a***@example.org`); `test_json_output_has_required_fields` (`service, version, env, level, ts, request_id, trace_id`). `test_request_context.py`: `test_untrusted_request_id_is_replaced` (with `HUB_TRUST_LB_REQUEST_ID=false`, header `X-Request-Id: evil\r\nX-A: b` or 200 chars gives a fresh UUID in the response header; with true and a valid `^[A-Za-z0-9-]{8,64}$` value it is echoed; invalid even when trusted is replaced), `test_response_always_carries_request_id`, `test_context_cleared_after_request` (contextvar is `None` after the response). `test_metrics_and_tracing.py` (integration marker not needed): `test_request_metrics_recorded` (call the health URL via `Client`, assert `http_requests_total{service,route,code}` incremented with templated route and no id labels) and `test_span_trace_id_matches_log_trace_id` using `InMemorySpanExporter` (TC-NFR-09-1).
- [ ] 2. Run `cd backend && python -m pytest tests/unit/shared -q -k "redaction or request_context or metrics"`. Expected: ImportError / failures.
- [ ] 3. Implement `context.py`, `logging.py` (structlog `ProcessorFormatter` for stdlib loggers, JSON renderer, processors: contextvars merge, add service/version/env, `redact_processor`; denylist and regex tables are module constants; hashing the number uses `hashlib.sha256`), `middleware.py` `RequestContextMiddleware` (first middleware; accepts the inbound id only when `settings.HUB_TRUST_LB_REQUEST_ID` and the pattern matches; binds `request_id`, `correlation_id`, `entrypoint`; sets `X-Request-Id`; records the request counter and histogram with buckets 0.1, 0.2, 0.3, 0.5, 1, 2, 5, 8; unbinds in `finally`), `metrics.py` (prometheus_client; `start_metrics_server(port)` starts a thread server on `HUB_METRICS_PORT` serving `MultiProcessCollector` when `PROMETHEUS_MULTIPROC_DIR` is set; called from `deploy/gunicorn.py` `when_ready`, `asgi_stream.py` and the worker commands; not a Django route), `tracing.py` (OTel SDK, OTLP exporter from `OTEL_EXPORTER_OTLP_ENDPOINT`, Django, psycopg and redis instrumentors, drop the `ticket` query parameter by recording `url.path` only; W3C `traceparent` from HAProxy is honoured; processor copies `trace_id` into the structlog event).
- [ ] 4. Run until green.
- [ ] 5. Commit: `feat(shared): structured logging with redaction, request ids, metrics and tracing`

### Task 00.5: Error taxonomy and problem+json handler

- [ ] 1. Tests `backend/tests/unit/shared/test_problem.py`: table-driven `test_domain_error_maps_to_status_and_code` over the docs/03 7.1 classes (ValidationFailed 422 `validation_error`, Unauthenticated 401, PermissionDenied 403, NotFound 404, Conflict 409, RateLimited 429 with `Retry-After` header equal to `retry_after_seconds`, ExternalUnavailable 503, InvariantViolation 500, Unexpected 500); `test_body_shape` (keys `type,title,status,code,detail,instance,request_id`; `type` starts with `https://hub.soleilgrid.example/problems/`; content type `application/problem+json`); `test_unexpected_exception_hides_detail` (a `RuntimeError("secret sql")` yields 500 `internal_error`, `"secret" not in body`); `test_drf_validation_error_has_errors_list` (422, `errors[0]` has `field, code, message`); `test_plain_view_domain_error_via_middleware`; `test_404_handler_is_problem_json`.
- [ ] 2. Run `cd backend && python -m pytest tests/unit/shared/test_problem.py -q`. Expected: ImportError.
- [ ] 3. Implement `errors.py` (classes as in Interfaces; `InvariantViolation` also logs at ERROR with metric `invariant_violations_total`), `api/problem.py` (`to_problem` is the single mapping used by the DRF handler, the middleware `process_exception` and `handler404/500`; DRF exceptions map per docs/03 7.7 item 6; `detail` localisation hook reads `Accept-Language` but only `en` and `fr` message keys exist now, default `en`; the problem `type` base URL comes from setting `HUB_PROBLEM_BASE_URL`, default `https://hub.soleilgrid.example/problems/` **[open]** default used), `DomainErrorMiddleware` in `middleware.py`.
- [ ] 4. Run until green.
- [ ] 5. Commit: `feat(shared): error taxonomy and problem+json handler`

### Task 00.6: Health endpoints and readiness registry

- [ ] 1. Tests. `backend/tests/integration/shared/test_health.py` (uses the Postgres fixture and a Redis testcontainer; hits the Django test `Client` on each of `hub.urls.api`, `webhooks`, `telemetry`, `stream` via `override_settings(ROOT_URLCONF=...)`): `test_live_returns_ok_without_dependencies` (200, body `{"data":{"status":"ok","version":...,"time":...}}`, `X-Request-Id` present, no DB query: `assertNumQueries(0)`); `test_ready_200_all_ok` (`checks` contain `postgres` and `redis` with `status: ok`); `test_ready_503_when_postgres_down` (point `default` at a closed port; 503 problem+json code `service_unavailable`, `Retry-After: 5`); `test_ready_200_degraded_when_redis_down` (status `degraded`); `test_ready_503_when_draining_file_exists` (create the file named by `HUB_DRAIN_FILE`); `test_response_validates_against_openapi_health_schema` (load `api/openapi.yaml` `components.schemas.Health` and validate with `jsonschema`); `test_only_health_routes_exist` (walk `get_resolver(urlconf).url_patterns` flattened; the set equals the two paths) for TC-NFR-06-7 groundwork.
- [ ] 2. Run `cd backend && python -m pytest tests/integration/shared/test_health.py -q`. Expected: fails (404 or import error).
- [ ] 3. Implement `api/health.py` (plain Django views, GET only, other methods 405 problem; `register_readiness_check` registry; postgres check `SELECT 1` on `default` with 2 s timeout, critical; redis `PING` with 1 s timeout, not critical so failure gives `degraded`; draining file existence gives 503 before running checks; the `Health.checks[].name` values must come from the OpenAPI enum), `api/urls.py` exposing `health_urlpatterns` under `api/v1/health/`; the four URLConfs include them and set `handler404/handler500`. `deploy/entrypoint-drain.sh`: on SIGTERM `touch /tmp/draining`, sleep 6, forward SIGTERM. `deploy/gunicorn.py`: bind `0.0.0.0:8000`, `graceful_timeout 30`, `timeout 30`, `keepalive 5`, workers/threads from env (`GUNICORN_WORKERS`, `GUNICORN_THREADS`, `GUNICORN_WORKER_CLASS`), `when_ready` starts the metrics server.
- [ ] 4. Run until green.
- [ ] 5. Commit: `feat(shared): liveness and readiness endpoints with drain support`

### Task 00.7: DjangoUnitOfWork, outbox writer, worker command stubs

- [ ] 1. Tests `backend/tests/integration/shared/test_uow.py` (database role `migrator` URL in the fixture because role grants for outbox arrive with later slices): `test_outbox_rows_roll_back_with_the_transaction` (add an event, raise inside `with uow:`, assert `count(*) from hub.outbox == 0`); `test_clean_exit_commits_outbox_row` (row has `event_type == "payment.confirmed"`, `id == event_id`, `payload` contains envelope keys `event_id,name,version,occurred_at,correlation_id,causation_id,aggregate_id` plus the payload); `test_require_active_outside_transaction_raises`; `test_savepoint_partial_rollback` (inner failure discards only inner staged events); `test_on_commit_runs_after_commit_not_after_rollback`; `test_principal_context_is_transaction_local` (inside: `current_setting('app.user_id', true)` equals the id; after exit: empty string or null; identical on a second `with` with no principal) using `set_config(..., true)` as docs/04 10.9 and the schema read `app.user_id` / `app.customer_id`; `test_atomic_requests_not_used` (`connection.settings_dict["ATOMIC_REQUESTS"] is False`). `backend/tests/unit/shared/test_commands.py`: `test_run_worker_relay_scheduler_exit_cleanly_on_sigterm` (each command runs with `--once` and returns 0 after logging `loop.started`; without `--once` it loops with a 1 s sleep until SIGTERM, tested with a thread flag).
- [ ] 2. Run `cd backend && python -m pytest tests/integration/shared/test_uow.py tests/unit/shared/test_commands.py -q`. Expected: ImportError.
- [ ] 3. Implement `events.py` (`DomainEvent` frozen dataclass, `to_row()` building `(id, aggregate_type, aggregate_id, event_type, payload_json)` where `payload_json` = envelope plus payload), `uow.py` (`DjangoUnitOfWork.__enter__` opens `transaction.atomic(using=...)`, then runs `SELECT set_config('app.user_id', %s, true), set_config('app.customer_id', %s, true)` only when a principal is given; `outbox.add` stages in a list per savepoint level; on clean exit of the outermost block it runs one parameterised raw `INSERT INTO hub.outbox (id, aggregate_type, aggregate_id, event_type, payload) VALUES ...` inside the still-open transaction, then commits; never sets `search_path`), `db.py` (`call_function(name, *args)` helper declared with schema-qualified call and a documented SQLSTATE map placeholder raising `NotImplementedError` is forbidden, so implement only `call_function` executing `SELECT hub.<name>(%s,...)` with name validated against `^[a-z_]+$`; error mapping arrives with slice 03), management commands (`--once` flag, `--group` accepted by `run_worker` and ignored, log `loop.started`, call `start_metrics_server`).
- [ ] 4. Run until green.
- [ ] 5. Commit: `feat(shared): unit of work, outbox writer and no-op worker commands`

### Task 00.8: SQL baseline migration mechanism (`hubdb`)

- [ ] 1. Tests `backend/tests/integration/hubdb/test_migrations_consistent.py` (disposable Postgres 16 per test; superuser URL for the baseline step): `test_baseline_is_byte_copy_of_schema_sql` (`Path("db/baseline/0000_baseline.sql").read_bytes() == Path("db/schema.sql").read_bytes()` while no numbered migration exists); `test_pairing_rules` (each `db/migrations/NNNN_name.sql` has a wrapper of the same stem; numbers contiguous from 0001; first line `-- phase: expand|contract`; `-- transaction: none` agrees with `atomic = False`; trivially true with zero files, and tested against a temporary tree with a gap, a missing wrapper and a bad phase line to prove the checker fails); `test_migrate_from_empty_applies_baseline` (`manage.py migrate hubdb 0000 --settings=hub.settings.migrate` with the superuser URL, then `select count(*) from hub.users` works and `django_migrations` records `hubdb 0000_baseline`); `test_migrated_catalog_equals_schema_sql` (database X migrated, database Y loaded with `db/schema.sql`; compare a catalog fingerprint of columns, constraints, indexes, triggers with `tgenabled`, functions with owner and `prosecdef` and `proconfig`, policies, ACLs via `aclexplode`, `pg_db_role_setting`, memberships, excluding `django_migrations`; assert equal and print the diff on failure) for TC-NFR-14-7; `test_invariants_also_pass_on_migrated_database` (reuse the statement parser of `tests/test_schema_in_postgres.py` by importing its helper functions, run `db/invariant_tests.sql` on X); `test_sql_text_runs_as_single_statement_list` (wrapper uses a one-element `RunSQL` list).
- [ ] 2. Run `cd backend && python -m pytest tests/integration/hubdb -q -m integration`. Expected: fails (`db/baseline/0000_baseline.sql` missing, app missing).
- [ ] 3. Implement: copy `db/schema.sql` to `db/baseline/0000_baseline.sql` (`cp`, never edit); `hubdb/apps.py` (label `hubdb`, no models); `hubdb/sqlfiles.py` `sql_text(stem)` (resolves the repo root from the `HUB_REPO_ROOT` setting, default four parents above the file, and in images `/app`); `hubdb/migrations/0000_baseline.py` with `initial = True`, `atomic = True`, one `RunSQL(sql=[sql_text("0000_baseline")], reverse_sql=RunSQL.noop)`. Because `schema.sql` creates roles and grants, the first run needs a superuser: the `hub.settings.migrate` module reads `HUB_MIGRATE_URL` and the runbook comment in the module says to point it at a superuser for `migrate hubdb 0000` only (docs/04 10.5). Add `tests/integration/hubdb/fingerprint.py` with the catalog fingerprint query helper. Do not add the checksum file (written at first release).
- [ ] 4. Run until green (needs Docker).
- [ ] 5. Commit: `feat(hubdb): RunSQL baseline migration and consistency tests`

### Task 00.9: Import-linter contracts and architecture tests

- [ ] 1. Tests `backend/tests/architecture/test_importlinter.py`: `test_lint_imports_passes_on_skeleton` (subprocess `lint-imports --config backend/.importlinter`, return code 0); `test_contracts_catch_violations` (write temporary modules into a copy of `src/hub` for each case and assert non-zero: `hub.identity.domain` importing `django`; `hub.payments.application` importing `hub.ledger.infrastructure`; `hub.customers.api` importing `hub.payments.domain`; `hub.shared` importing `hub.identity.contracts`; any module importing `anthropic`; `hub.ledger.domain` importing `hub.ledger.models`; a business layer importing `hub.bootstrap`; `import django.contrib.admin`); `test_generated_file_is_current` (`gen_importlinter.py --check` returns 0); `test_every_module_in_check_traceability_exists_as_package` (names from `tools/check_traceability.MODULES` equal the directories under `src/hub` plus `shared`).
- [ ] 2. Run `cd backend && python -m pytest tests/architecture/test_importlinter.py -q`. Expected: `.importlinter` missing.
- [ ] 3. Implement `tools/scripts/gen_importlinter.py` with `--check`; it writes `backend/.importlinter` containing the contracts of `docs/03-code-architecture.md#3.3` verbatim (layers, domain-pure, application-no-outer, models-private, module-dag, anthropic-only-in-gateway, pydantic-only-in-gateway, no-django-admin, wiring-only) and the generated per ordered pair `public-surface` contract using the allowed-direction matrix of docs/03 section 3.1 (including `reconciliation -> meters`); the exclusions listed in 3.3 (`hub.bootstrap`, `hub.urls`, `hub.settings`, `hub.wsgi_*`, `hub.asgi_stream`, `hub.*.models`) are applied. Keep `include_external_packages = True`. Note that `hub.shared` must not import business modules (add a `forbidden` contract named `shared-is-kernel`).
- [ ] 4. Run until green; run `make lint` from the root, expect exit 0.
- [ ] 5. Commit: `feat(arch): import-linter contracts generated from the dependency matrix`

### Task 00.10: Images, compose stack, PgBouncer, HAProxy, stubs

- [ ] 1. Tests `tests/test_infra_skeleton.py`: `test_haproxy_has_exactly_five_pools_and_routes` (parse `infra/lb/haproxy.cfg`: backends `{be_webhooks,be_telemetry,be_stream,be_api,be_web}`; `path_beg /api/v1/webhooks/providers/` routes to `be_webhooks`; `/api/v1/telemetry/` and `/api/v1/gateway/` to `be_telemetry`; `path /api/v1/dashboard/stream` to `be_stream`; `/api/` to `be_api`; default `be_web`; `log-format` contains neither `%HU` nor `%r` nor `%ST`-less query logging: assert it contains `%HPO`? use `%HP` and not `%HQ`, `%HU`, `%r`); `test_compose_services_and_security` (YAML: services include `lb, hub-api, hub-ingest-webhooks, hub-ingest-telemetry, hub-stream, hub-web, hub-worker, hub-relay, hub-scheduler, migrate, pg-primary, pgbouncer, redis, minio, otel-collector, provider-stub, meter-sim`; each hub-* Python service sets `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`; `hub-web` environment keys all start with `NEXT_PUBLIC_` or are `HOSTNAME`, `PORT`, `OTEL_*`; no service other than `pg-primary`, `pgbouncer`, `migrate`, `hub-*` Python containers contains `HUB_DB_`; each Python service has only its own role URL variable); `test_stub_placeholders_reference_slices` (`provider-stub` and `meter-sim` have profile `stubs` and a command mentioning `slice-T1` and `slice-T2`); `test_pgbouncer_ini` (`pool_mode = transaction`, `max_prepared_statements` absent or 0 matching the prepare_threshold decision, databases `hub_rw`, `hub_ro`, `hub_analytics`). Integration `tests/test_stack_up.py` marked `integration` and run only by `make verify`: after `make up`, `curl -fs localhost:8080/api/v1/health/ready` returns 200 for each of the pools reached via paths (see Demo).
- [ ] 2. Run `python -m pytest tests/test_infra_skeleton.py -q`. Expected: files missing.
- [ ] 3. Implement. `backend/Dockerfile`: two stages from `python:3.12-slim` (digest pinned by the implementer at build time and recorded in the file), deps installed from `uv export --require-hashes` output, `USER 10001`, `ENV PYTHONPATH=/opt/deps:/app/src`, entrypoint script copied; one image `soleil/hub`. Compose (`infra/docker/compose.yaml`, project name `soleil`) follows the docs/09 1.3 sketch with these deliberate differences (docs/03 and docs/04 win): commands `gunicorn hub.wsgi_api:application -c deploy/gunicorn.py`, `hub.wsgi_ingest:application` (two services, env `HUB_INGEST_ROLE=webhooks|telemetry`), `uvicorn hub.asgi_stream:application`, `python manage.py run_worker`, `run_relay`, `run_scheduler`; env per role as in Interfaces (`HUB_DB_API_URL` and `HUB_DB_API_REPLICA_URL` both through PgBouncer database `hub_rw` and `hub_ro`, and `hub_ro` maps to the primary because no replica exists locally); no `hub-api` `deploy.replicas` above 1 in local (compose `--scale` is documented); `migrate` runs `python manage.py migrate --noinput --settings=hub.settings.migrate` with `HUB_MIGRATE_URL` pointing at `pg-primary` with the superuser for the first run, followed by service `db-logins` (image `postgres:16`, runs `pg-init/logins.sql` with psql to give the NOLOGIN roles `LOGIN` and the dev passwords from `.env`; idempotent); app services depend on `db-logins` completed. `pg-primary`: `postgres:16`, database `hub` created by `pg-init/00-create-db.sh`. `pgbouncer`: config file as docs/04 10.10 (transaction pool mode, one pool per role, `ignore_startup_parameters = extra_float_digits,options`), `userlist.txt` generated by `gen_dev_userlist.sh`. `redis:7-alpine` as sketched; `minio/minio` pinned to a dated release tag. `lb`: `haproxy:3.0-alpine` with `infra/lb/haproxy.cfg` copied from docs/09 1.4 (five backends, `X-Request-ID` and `traceparent` set when absent, log format without query string, `/healthz` and `/metrics` on 8404) with `server-template` sizes reduced to 2. `hub-web`: default image `nginx:1.27-alpine` mounting `infra/docker/hub-web-stub/default.conf` (returns 200 on `/web/health` and a static "hub-web stub" page on `/`); `make up WEB=real` adds `-f infra/docker/compose.web.yaml`, which builds `frontend/` per [fe-00-shell.md](frontend/fe-00-shell.md) and replaces the stub. `provider-stub` and `meter-sim`: image `busybox:1.36`, profile `stubs`, command prints `placeholder: see slice-T1 (provider stub)` or `slice-T2 (meter simulator)` and sleeps. `otel-collector`: `otel/opentelemetry-collector-contrib` pinned, config `infra/observability/otel-collector.yaml` (OTLP receiver, `debug` exporter, Prometheus scrape of app port 9102 handled by `prometheus.yml`); Tempo, Loki and Grafana are deferred to slice 17 (plan note). `make up` runs `env`, `gen_dev_userlist.sh`, `docker compose ... up -d --wait`; `make down` runs `down`; `make reset` runs `down -v`.
- [ ] 4. Run `python -m pytest tests/test_infra_skeleton.py -q` until green, then `make up` and the commands in the Demo script steps 1 to 4.
- [ ] 5. Commit: `feat(infra): compose stack, PgBouncer, HAProxy five pools and stub placeholders`

### Task 00.11: CI pipeline, security scans, verify script

- [ ] 1. Tests `tests/test_infra_skeleton.py::test_ci_workflow_has_required_jobs` (YAML of `.github/workflows/ci.yml`: jobs `python`, `frontend`, `sql`, `contracts`, `security`, `images` exist; `python` steps contain `ruff check`, `mypy`, `lint-imports`, `check_django_config.py`; `sql` contains `tests/test_schema_in_postgres.py` and `test_migrations_consistent`; `contracts` contains `tests/test_alignment.py`, `check_traceability.py`, an OpenAPI lint (`spectral lint api/openapi.yaml`) and `oasdiff` skipped until a release tag exists via a condition; `security` contains `gitleaks`, `pip-audit`, `semgrep`, `bandit`, `trivy`; `frontend` guarded by `hashFiles('frontend/package.json') != ''` and calls `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`; every `uses:` is pinned to a full commit SHA (regex `@[0-9a-f]{40}`); `permissions: contents: read` at top level); `test_verify_script_runs_slice_00_commands` (`tools/scripts/verify_slice.sh 00 --print` lists the commands of the Verification section).
- [ ] 2. Run `python -m pytest tests/test_infra_skeleton.py -q -k "ci_workflow or verify_script"`. Expected: files missing.
- [ ] 3. Implement `.github/workflows/ci.yml` (triggers pull_request and push to main; concurrency group per ref; Postgres and Docker via the runner's Docker; caches uv and npm; `images` job builds `soleil/hub` and, when `frontend/Dockerfile` exists, `soleil/hub-web`, runs `hadolint` and `trivy image` failing on fixable HIGH and CRITICAL; `security` also greps for forbidden log format strings in `infra/lb` and fails on `$request_uri`, `%HU`, `ticket=`), `renovate.json` (weekly grouped, security immediate, majors not auto-merged), `.gitleaks.toml`, `tools/scripts/verify_slice.sh` (`set -euo pipefail`; case on slice number; for `00` runs the Verification commands in order; `--print` echoes them).
- [ ] 4. Run `python -m pytest tests/test_infra_skeleton.py -q`; run `actionlint` if installed (`make lint` calls it when present).
- [ ] 5. Commit: `ci: github actions pipeline with python, frontend, sql, contracts and security jobs`

### Task 00.12: End-to-end verification, demo and progress evidence

- [ ] 1. Write `tests/test_stack_up.py::test_health_through_balancer_for_every_pool` (marked `integration`; uses `urllib` against `http://localhost:8080`): GET `/api/v1/health/live` is 200 with `X-Request-Id`; a request carrying `traceparent` header returns the same `X-Request-Id` the balancer generated when none was sent; `GET /` returns 200 from the stub (pool `be_web`); `GET /api/v1/health/ready` is 200 with `checks` containing `postgres`; `docker compose exec hub-ingest-webhooks python -c ...` reading `/api/v1/health/ready` on `127.0.0.1:8000` is 200; HAProxy stats `http://localhost:8404/stats;csv` lists all five backends with status `UP` (webhooks and telemetry backends are checked on the ready path, so they are UP only when the ingest services are healthy); `test_no_secret_in_container_logs` (collect `docker compose logs` and assert no match of the password literals from `.env`, and no `Authorization`).
- [ ] 2. Run it before `make up` (stack down). Expected: connection refused failures.
- [ ] 3. Implement nothing new unless a defect appears; fix defects in the owning files, add a regression test for each. Write the evidence into `docs/plan/progress.md` (task status, commit hashes, summary lines, full `make verify SLICE=00` output, demo result, assumptions).
- [ ] 4. Run `make up` then the test until `passed`; run `make verify SLICE=00`.
- [ ] 5. Commit: `test(infra): stack health through the balancer and slice 00 evidence`

## Verification

`make verify SLICE=00` runs, in order, from the repository root (`tools/scripts/verify_slice.sh 00`):

1. `make lint` passes with exit 0 (ruff, ruff format check, mypy strict, `lint-imports`, `tools/check_django_config.py`).
2. `cd backend && python -m pytest tests/unit tests/architecture -q` passes with 0 failures.
3. `cd backend && python -m pytest tests/integration -q -m integration` passes with 0 failures (Docker required; no skips allowed, the command adds `-p no:cacheprovider --strict-markers` and fails on any skipped test via `-rs` check).
4. `python -m pytest tests -q` (root tests including `test_alignment.py`, `test_schema_in_postgres.py`, `test_traceability.py`, `test_infra_skeleton.py`) passes with 0 failures.
5. `python tools/check_traceability.py` exits 0.
6. `python tools/check_django_config.py` exits 0; `cd backend && python manage.py check --deploy --fail-level WARNING --settings=hub.settings.<m>` exits 0 for `api`, `ingest` (with `HUB_INGEST_ROLE=webhooks`), `worker`, `stream`.
7. `cd backend && python manage.py makemigrations --check --dry-run --settings=hub.settings.api` reports `No changes detected` (no models exist; guards against future drift).
8. `make up && python -m pytest tests/test_stack_up.py -q -m integration` passes (all five pools UP, health 200).
9. `docker compose -f infra/docker/compose.yaml config -q` exits 0; `actionlint` (if installed) exits 0.

## Demo script

1. `git clone` into an empty directory (or `make reset`), run `make up`. Observe: completes without error; `docker compose ps` shows every service `healthy` or `exited (0)` for `migrate` and `db-logins`; target under 10 minutes cold (TC-NFR-14-1).
2. `curl -si localhost:8080/api/v1/health/live`. Observe: `200`, header `X-Request-Id` (a UUID), body `{"data":{"status":"ok",...}}`.
3. `curl -si localhost:8080/api/v1/health/ready`. Observe: `200`, `checks` list with `postgres` and `redis` `ok`.
4. `curl -si localhost:8080/`. Observe: `200` from the `hub-web` stub (replaced by the Next.js shell when fe-00 is built, with `make up WEB=real`).
5. `docker compose -f infra/docker/compose.yaml logs hub-api | tail -3`. Observe JSON lines with `request_id`, `trace_id`, `service`, no secrets.
6. `docker compose ... logs otel-collector | grep -m1 trace_id`. Observe a span for the health request whose trace id equals the `trace_id` in the api log line (the balancer supplied the `traceparent`).
7. `docker compose ... stop hub-ingest-webhooks`, wait 6 s, open `localhost:8404/stats`. Observe `be_webhooks` goes down while `be_api` stays UP; restart it and see it recover.
8. Temporarily add `"django.contrib.admin"` to `INSTALLED_APPS` in `hub/settings/base.py` and run `make lint`. Observe the failure naming the rule; revert. Add `import anthropic` to `hub/payments/domain/x.py`; `lint-imports` fails; revert.

## Done criteria

Requirements covered: NFR-14 (groundwork and gates), NFR-09 (baseline), NFR-06 (hardening checks, secrets hygiene), NFR-01 (health and pools groundwork), NFR-12 (container deployables), FR-15 and FR-16 (skeleton only).

Test cases implemented and passing in this slice (ids exist in `docs/10-test-strategy.md#8. Traceability matrix`):
- TC-NFR-06-9 (admin not installed, ATOMIC_REQUESTS off: Task 00.2)
- TC-NFR-06-10 (`check --deploy` clean per deployable: Verification step 6)
- TC-NFR-06-6 (partly: no secret in logs and container output: Tasks 00.4 and 00.12; the image and environment-dump parts follow in slice 17)
- TC-NFR-09-1 (trace id in logs, metrics and traces: Task 00.4)
- TC-NFR-14-1 (one-command environment healthy from a clean clone: Demo step 1 and the CI `images` plus stack job)
- TC-NFR-14-4 (alignment tests run in CI: Task 00.11)
- TC-NFR-14-5 (traceability checker in CI: Task 00.11)
- TC-NFR-14-6 (managed-model and drift gate: `check_django_config.py` AST rule in Task 00.2; the introspection comparison activates when the first model appears in slice 01)
- TC-NFR-14-7 (baseline plus migrations equal `db/schema.sql`: Task 00.8)

Mark the tests with `@pytest.mark.tc("TC-...")` for the ids above.

## Plan notes for the tech lead

1. **Settings module count.** `docs/03#10` row 0 says "five settings modules"; D-43 lists `api, ingest, worker, stream, migrate` plus `base`; D-50 adds `test`. I built `base` plus six (`api, ingest, worker, stream, migrate, test`). `docs/09#Stack change` still says the migrate job reuses `hub.settings.worker`; I followed D-49 (own `migrate` module).
2. **Migration chain location.** `docs/03#7.4` puts the Django wrappers in `hub/shared/migrations/` with `apply_sql_file`; `docs/04#10.5` uses a `hubdb` app, `db/baseline/0000_baseline.sql`, `db/migrations/NNNN_name.sql`, `sql_text`. `docs/03#1` mentions `0001_baseline.sql` in `db/migrations`. I followed docs/04 (most detailed, same precedence tier). The assignment's phrase "three baseline migrations" is not defined anywhere; I implemented the baseline plus the mechanism for numbered files (zero numbered files exist today).
3. **Database environment variable names.** `docs/03#7.2` uses `HUB_DB_URL`; `docs/04#10.10` uses `HUB_DB_API_URL`, `HUB_DB_INGEST_URL`, `HUB_DB_WORKER_URL`, `HUB_MIGRATE_URL`; `docs/09` uses `DATABASE_URL`. I followed docs/04.
4. **Identity setting for RLS.** `docs/03#2.1` says `SET LOCAL hub.principal_id`; `db/schema.sql` and `docs/04#10.9` read `app.user_id` and `app.customer_id`. Followed the schema.
5. **Process names and entrypoints.** `docs/09` uses `hub.wsgi:application`, `hub.asgi:application`, `run_consumers`, `run_outbox_relay`; `docs/03` uses `wsgi_api`, `wsgi_ingest`, `asgi_stream`, `run_worker`, `run_relay`. Followed docs/03.
6. **Repository layout.** `docs/09#2.1` uses `web/` and `deploy/compose/`; `docs/03#1` uses `frontend/`, `infra/docker/`, `infra/lb/`. Followed docs/03.
7. **CONN_MAX_AGE and prepared statements.** `docs/09#1.2` says `CONN_MAX_AGE = 60` and `max_prepared_statements = 200`; `docs/03#7.4` and `docs/04#10.10` say `CONN_MAX_AGE = 0` and prepared statements always disabled (`prepare_threshold=None`). Followed docs/04.
8. **Readiness semantics.** `api/openapi.yaml` (`getReadiness`) says 503 removes the instance and lists Redis among checks; `docs/09#1.4` notes say a Redis failure degrades without making the service unready. I followed docs/09 (Redis gives `degraded`, 200); the OpenAPI text is not contradicted for Postgres.
9. **Health and metrics routes.** OpenAPI marks both health operations `x-pool: be_api`, yet D-04 and docs/03 require them on every Django deployable; I serve them from all four URLConfs. No `/metrics` route is allowed (executor guide: no endpoint outside the OpenAPI file), so Prometheus metrics use a separate internal port (`HUB_METRICS_PORT`, default 9102) not routed by HAProxy; docs/09 does not say how.
10. **"Trace spans balancer".** `docs/03#10` row 0 demo implies a balancer span; HAProxy emits none. The plan has HAProxy create the `traceparent` and the Django spans continue it. Tempo, Loki and Grafana (docs/09 compose) are deferred to slice 17; the collector uses a `debug` exporter.
11. **`GET /me`.** `docs/03#10` row 0 lists a trivial `GET /me` vertical, but `getMe` is permitted to five authenticated principals (`api/permissions.yaml`) and authentication is slice 01; the assignment text omits it too. Not built here.
12. **No read replica locally.** `hub_ro` and the stream pool's replica URL map to the primary in compose (docs/09 has `pg-replica`); the replica service is left to the slice that introduces replica routing.
13. **Admin app label.** `docs/04#10.2` names the Django app `administration`; D-50 and `docs/03` say label `hub_admin`. Followed D-50. `docs/04#10.3` says ids use `hub.shared.ids.uuid7()` as a plain function while docs/03 uses the `IdGenerator` port; the plan provides both (`ids.py` exposes the adapter and `uuid7()`).
14. **Links.** Companion files `slice-T1-provider-stub.md` and `slice-T2-meter-simulator.md` do not exist yet and are therefore not linked; `docs/11-implementation-plan.md` (named by the executor guide) and `docs/plan/99-verification.md` also do not exist yet.
15. **Money.** `docs/03#2.1` places `Money` in `shared`; the assignment's list for slice 00 omits it, so it is left to the first slice that needs it (slice 03).
