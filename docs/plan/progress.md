# Progress: Soleil Grid Hub build

Executor: implementing agent. Guide: `docs/plan/00-executor-guide.md`. Slice in progress: **00**
(`docs/plan/slice-00-walking-skeleton.md`). Update this file after every task (status, commit,
test command and its summary line) and paste the full `make verify SLICE=00` output at the end
of the slice.

## Task log — slice 00

| Task | Scope | Status | Commit | Test command and result |
|---|---|---|---|---|
| 00.1 | Repository scaffolding, tooling and Makefile | done | `60db793` | `python -m pytest tests/test_infra_skeleton.py -q` -> `2 passed`; `make -n lint` exit 0 |
| 00.2 | Django project, settings modules, entrypoints, config checker | done | `1980883` | `cd backend && python -m pytest tests/architecture -q` -> `41 passed`; `python tools/check_django_config.py` -> exit 0 (9.4 s, runs `check --deploy` per module + `makemigrations --check`); ruff, ruff format, mypy strict clean
| 00.3 | Clock, IdGenerator, config helpers, container | done | `57d66eb` | `cd backend && python -m pytest tests/unit/shared -q` -> `12 passed`; mypy strict, ruff and ruff format clean
| 00.4 | Structured logging, request context, metrics, tracing | done | `181110d` | `cd backend && python -m pytest tests/unit/shared -q` -> `30 passed` (11 new); `pytest tests/unit tests/architecture -q` -> `71 passed`; ruff, ruff format, mypy strict clean; `python tools/check_django_config.py` -> exit 0 |
| 00.5 | Error taxonomy and problem+json handler | done | `00d1cd4` | `cd backend && python -m pytest tests/unit/shared/test_problem.py -q` -> `7 passed`; `pytest tests/unit tests/architecture -q` -> `78 passed`; mypy strict, ruff, ruff format clean |
| 00.6 | Health endpoints and readiness registry | done | `ebe2d81` | `cd backend && python -m pytest tests/integration/shared/test_health.py -q -m integration` -> `17 passed` (4 URLConfs x live/route tests); `pytest tests/unit tests/architecture -q` -> `78 passed`; mypy strict, ruff, ruff format clean; `python tools/check_django_config.py` -> exit 0 |
| 00.7 | DjangoUnitOfWork, outbox writer, worker command stubs | done | `45ba2f6` | `cd backend && python -m pytest tests/integration/shared/test_uow.py tests/unit/shared/test_commands.py -q -m integration` -> `7 passed`, `pytest tests/unit/shared/test_commands.py -q` -> `6 passed`; `pytest tests/unit tests/architecture -q` -> `85 passed`; `pytest tests/integration -q -m integration` -> `24 passed`; ruff, ruff format, mypy strict clean; `python tools/check_django_config.py` -> exit 0; `manage.py run_worker/run_relay/run_scheduler --once` -> exit 0 with `loop.started` then `loop.stopped` JSON lines |
| 00.8 | SQL baseline migration mechanism (`hubdb`) | done | `da2649c` | `cd backend && python -m pytest tests/integration/hubdb -q -m integration` -> `7 passed` (baseline byte copy, pairing rules incl. three negative trees, migrate-from-empty, catalog fingerprint equality, invariants on the migrated database, single-statement RunSQL); `pytest tests/integration -q -m integration` -> `31 passed`; `pytest tests/unit tests/architecture -q` -> `85 passed`; root `pytest tests -q` -> `36 passed`; ruff, ruff format, mypy strict clean; checker exit 0; `makemigrations --check` -> no changes |
| 00.9 | Import-linter contracts and architecture tests | done | `PENDING` | `cd backend && python -m pytest tests/architecture/test_importlinter.py -q` -> `11 passed` (skeleton clean, seven planted violations each caught, AST admin ban, generator current, module list matches packages); `make lint` -> exit 0 with `Contracts: 119 kept, 0 broken`; `pytest tests/unit tests/architecture -q` -> `96 passed`; `pytest tests/integration -q -m integration` -> `31 passed`; root `pytest tests -q` -> `36 passed` |
| 00.10 | Images, compose stack, PgBouncer, HAProxy, stubs | not started | — | `python -m pytest tests/test_infra_skeleton.py -q` |
| 00.11 | CI pipeline, security scans, verify script | not started | — | `python -m pytest tests/test_infra_skeleton.py -q` |
| 00.12 | End-to-end verification, demo and evidence | not started | — | `make up && python -m pytest tests/test_stack_up.py -q -m integration` |

## Assumptions

- **00.4 `SERVICE_NAME`**: the log lines and `http_requests_total{service,...}` series need a
  per-deployable label (docs/09 uses `service="hub-ingest"`), so each settings module sets a
  plain `SERVICE_NAME` constant (`hub-api`, `hub-ingest`, `hub-worker`, `hub-stream`,
  `hub-migrate`, `hub-test`; base defaults to `hub`). It is not an environment variable, so
  the Interfaces list of slice 00 is unchanged.
- **00.4 framework 4xx/5xx lines carry no `request_id`**: Django logs `status_code >= 400` in
  `BaseHandler.get_response`, *after* the middleware chain returns, and the context must be
  cleared before the response returns (`test_context_cleared_after_request`). Application log
  lines inside the request and the `X-Request-Id` response header both carry the id; those
  framework lines are correlatable by the header alone.
- **00.2 test made hermetic (fixed in 00.4)**: `test_ingest_requires_a_role` failed whenever a
  local `.env` exists, because `env.load()` re-populates the variable it deletes from the
  environment — and `make env`/`make up` always create that file. The test now stubs the
  `.env` loader so it proves the environment check itself.
- **00.5 problem `type` base URL (slice `[open]`)**: the default
  `https://hub.soleilgrid.example/problems/` is used (from `HUB_PROBLEM_BASE_URL` in
  `settings/base`, `.env.example` documents it). A problem `type` is
  `<base><code>`, e.g. `https://hub.soleilgrid.example/problems/not_found`.
- **00.5 `DomainError.__init__` signature**: the slice shows `DomainError(code, message_key,
  details)`; implemented as `__init__(code=None, *, message_key=None, details=None)` with
  class-level defaults per subclass, so `NotFound()` alone carries its catalogue code
  (`not_found`) and a module can pass a specific code (`customer_not_found`) plus an
  optional `details["errors"]` list that becomes the problem body's `errors[]`.
- **00.5 `Retry-After` on 503**: docs/05 requires `Retry-After` on 429 and 503; any 429/503
  problem response carries it (error-provided hint, else `retry_after_seconds` on
  `RateLimited`, else 30 s).
- **00.6 test infrastructure**: `backend/tests/conftest.py` starts one `postgres:16`
  container per session, applies `db/schema.sql` once as the superuser it expects, gives the
  `hub_api` role a dev-only LOGIN, and overrides pytest-django's `django_db_setup` instead of
  running `migrate` (slice 00 has no models, ADR-0020). docs/10 6.3 also says "cloned per test
  module"; with a single integration module the clone step would add a schema load per
  module for no isolation gain, so it is deferred to the module that needs it. Redis runs in
  its own container; the database alias is mutated in place because Django treats `DATABASES`
  as a complex override and a replaced dict leaves the connection handler on the old one.
- **00.6 readiness probes**: Postgres is probed with its own short-lived psycopg connection
  (`connect_timeout=2`, `statement_timeout=2000`) rather than Django's request connection, so
  a probe cannot inherit a poisoned connection or queue behind one, and the check is bounded
  as docs/09 1.4 requires. Redis is probed with `PING` (`socket_timeout=1`) and is not
  critical: an outage reports `degraded` and 200, per docs/09 8.2.
- **00.6 `service_unavailable` and 405**: docs/03 7.1 has no class for the readiness 503, so
  the view raises `ExternalUnavailable(code="service_unavailable",
  message_key="service_unavailable")` and `problem.py` carries the en/fr sentence;
  `MethodNotAllowed` (405) lives in `problem.py` because a plain view has no DRF machinery to
  turn `HttpResponseNotAllowed` into a problem document. Both go through `to_problem`, the
  single converter.

- **00.7 logging extras (found and fixed here)**: Task 00.4's `ProcessorFormatter` setup dropped
  every `extra={...}` field, because structlog builds a foreign record's event dict from the
  message alone. `structlog.stdlib.ExtraAdder()` now runs before the redaction processor, so a
  `top_up_id` reaches the line and a secret in an extra is still masked (commit `a6a4b56`).
- **00.7 test database role (superseded by 00.8)**: the harness first connected as
  `migrator`, then 00.8's invariant test proved that is wrong — `db/invariant_tests.sql`
  pins every application role as NOLOGIN, and roles are cluster-wide, so adding LOGIN to
  `migrator` for the harness broke a designed invariant. The harness now connects as the
  container's own superuser; a test that exercises a specific role creates its own login.
  `test_principal_context_is_transaction_local` remains the module's one `transaction=True`
  test: a principal set with `set_config(..., true)` only reverts at a real transaction
  boundary, and a savepoint release would keep it.
- **00.7 command shape**: `run_worker.py` holds `LoopCommand`, and `run_relay`/`run_scheduler`
  subclass it, so one implementation serves all three no-op loops without adding a kernel
  module the slice does not list. `handle()` returns `None` (Django's contract, so the process
  exits 0), `--once` runs a single iteration, `--group` is accepted and ignored by
  `run_worker`, and the loop stops through the command's own flag because a SIGTERM handler
  can only be installed from the main thread (a test runs the loop in a thread).

- **00.8 repo root for SQL files**: `HUB_REPO_ROOT` (already documented, empty in
  `.env.example`) names the directory holding `db/`; empty means "derive it from
  `settings/base.py`'s location", which is right for a checkout, and the image sets `/app`.
  `sql_text()` accepts only `^[0-9]{4}_[a-z0-9_]+$` stems, so a stem can never escape
  `db/baseline` or `db/migrations`.
- **00.8 catalog fingerprint**: compares relations, columns, constraints, indexes, triggers
  (`tgenabled`), functions (owner, `prosecdef`, `proconfig`), policies, ACLs expanded per
  grantee, `pg_db_role_setting` and `pg_auth_members`. Three normalisations are deliberate:
  `django_migrations` and its objects are excluded (it exists only in the migrated database),
  system namespaces (`pg_%`, `information_schema`) are excluded because TOAST relation names
  embed per-database OIDs, and the current database name inside `pg_db_role_setting` is
  rewritten to `{db}` because the two databases have different names.
- **00.8 migrate in tests runs as a subprocess**: the test invokes
  `manage.py migrate hubdb 0000 --settings=hub.settings.migrate` with an explicit environment
  (never the developer's `.env`), pointed at a superuser login, which is the documented
  first-run path in `hub.settings.migrate`.

- **00.9 layer syntax**: inside a `layers` contract with `containers`, the layer names are
  *relative* to each container, so the file says `api / application / domain` rather than
  `hub.*.api`; the doc's literal snippet makes import-linter look for
  `hub.<module>.hub.*.api`. Same intent, working syntax.
- **00.9 no-django-admin**: import-linter rejects subpackages of external packages
  (`django.contrib.admin`) while `include_external_packages = True` is required for the SDK
  contracts, so the ban is enforced by an AST scan in the architecture test instead — which
  also catches an import that never reaches the graph. The generated file records why.
- **00.9 SDK ignores**: `unmatched_ignore_imports_alerting = warn` is set on the two SDK
  contracts because the `anthropic_client` adapter and the pydantic models land in later
  slices; the warning stays visible in `make lint` output without failing the build.

## Open change requests

None.

## Evidence

`make verify SLICE=00` output: pending (task 00.12).
Demo script result: pending (task 00.12).
