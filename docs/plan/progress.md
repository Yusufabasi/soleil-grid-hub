# Progress: Soleil Grid Hub build

Executor: implementing agent. Guide: `docs/plan/00-executor-guide.md`. Slice complete: **00**
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
| 00.9 | Import-linter contracts and architecture tests | done | `e3122fe` | `cd backend && python -m pytest tests/architecture/test_importlinter.py -q` -> `11 passed` (skeleton clean, seven planted violations each caught, AST admin ban, generator current, module list matches packages); `make lint` -> exit 0 with `Contracts: 119 kept, 0 broken`; `pytest tests/unit tests/architecture -q` -> `96 passed`; `pytest tests/integration -q -m integration` -> `31 passed`; root `pytest tests -q` -> `36 passed` |
| 00.10 | Images, compose stack, PgBouncer, HAProxy, stubs | done | `ab0fb82` | `python -m pytest tests/test_infra_skeleton.py -q` -> `6 passed` (five pools and routing order, log format without `%HU`/`%HQ`/`%r`, compose service and security matrix, one role URL per deployable, stub placeholders, PgBouncer transaction mode); `make up` -> 14 containers healthy, `migrate` and `db-logins` `Exited (0)`; `python -m pytest tests/test_stack_up.py -q -m integration` -> `1 passed`; `make lint` -> exit 0; root `pytest tests -q` -> `40 passed, 1 deselected` |
| 00.11 | CI pipeline, security scans, verify script | done | `f027004` (contract fix `b33070f`) | `python -m pytest tests/test_infra_skeleton.py -q` -> `8 passed` (2 new: required jobs, SHA pins and read-only token, oasdiff behind a tag condition, frontend guarded by `hashFiles`, security scanners, verify plan in order); `actionlint 1.7.12 .github/workflows/ci.yml` -> exit 0; `gitleaks 8.30.1 detect --config .gitleaks.toml` -> `no leaks found` (17 commits); `npx @stoplight/spectral-cli@6 lint api/openapi.yaml` -> `0 errors, 118 warnings`, exit 0; root `python -m pytest tests -q` -> `42 passed, 1 deselected`; `make lint` -> exit 0 |
| 00.12 | End-to-end verification, demo and evidence | done | `3153167` (defects fixed in `daeef46`, `938f2b3`) | `python -m pytest tests/test_stack_up.py -q -m integration` -> `5 passed` (five pools routed, request id owned by the balancer, stats UP, one JSON line per request with its trace id, no credential in the logs); `make verify SLICE=00` -> `Slice 00 verification PASSED (11 commands)`, exit 0; four failures observed first with the stack down (connection refused, empty logs) |

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

- **00.10 two image tags in docs/09 no longer exist**: `edoburu/pgbouncer:1.23.1` was never
  published (that repository uses `v1.23.1-pN`) and `minio/minio` no longer resolves at all
  (upstream withdrew its Docker Hub images), so the stack uses `pgbouncer/pgbouncer:1.23.1`
  and MinIO's own binary from Bitnami's archived catalogue, pinned to a dated release tag as
  the plan asks (`bitnamilegacy/minio:2025.7.23-debian-12-r5`). Both keep the documented
  version and the service name; the substitutions are in the compose comments.
- **00.10 hub-web stub runs unprivileged**: the stack requires every `hub-*` container to be
  read-only, `cap_drop: [ALL]` and `no-new-privileges`, and the root `nginx:1.27-alpine` needs
  `CAP_SETUID`/`CAP_SETGID` to drop to its worker user, so the stub uses
  `nginxinc/nginx-unprivileged:1.27-alpine` and listens on 3000, the port the real Next.js
  server uses. Nothing about the balancer or the health check changes when `WEB=real` swaps it.
- **00.10 HAProxy: three fixes to make the sketch load**: `http-request allow-l7-retry` is not
  a directive in HAProxy 3.0, so the two ingest pools get `option http-buffer-request` (the
  actual precondition for retrying a POST) beside `retry-on conn-failure empty-response 503`;
  `http-response` rules cannot evaluate `path_beg` ACLs, so the pool decision and the static
  flag are captured in `txn.pool`/`txn.static` while the request is parsed and the response
  rules read those; and the admin socket lives in `/var/lib/haproxy` instead of `/var/run`,
  because the image runs as uid 99 and `/var/run` is a root-owned symlink, with a named volume
  so it survives a restart.
- **00.10 gunicorn control socket**: gunicorn 26 opens `$HOME/.gunicorn/gunicorn.ctl` by
  default, which is a read-only path in the image and unreachable from outside a container
  anyway; `control_socket_disable = True` is set because draining is driven by SIGTERM through
  `deploy/entrypoint-drain.sh`. The setting requires gunicorn >= 26, which `uv.lock` pins.
- **00.10 uv installed from PyPI**: the builder stage installs `uv==0.13.5` from the index the
  application dependencies already come from, instead of copying it out of `ghcr.io`; a build
  then depends on one registry, not two (the first real build failed on a ghcr.io lookup).
  uv >= 0.10 writes hashes into `uv export` by default, so the task's `--require-hashes`
  intent holds: pip installs in hash-checking mode from that file.
- **00.10 `logins.sql` lives outside `pg-init/`**: everything in
  `/docker-entrypoint-initdb.d` also runs at first boot, so a `logins.sql` there executed
  without its psql variables and aborted initialisation (the first `make up` failed on it).
  Only `00-create-db.sh` is an init script; the logins step is the separate `db-logins`
  service that runs after `migrate` completed.
- **00.10 `userlist.txt` is 0644, not 0600**: PgBouncer reads the file as uid 70 inside its
  container while the host user that runs `gen_dev_userlist.sh` is a different uid, so a
  private file is simply unreadable there (the first authentication attempt failed with `no
  such user: hub_api`). The values are the `dev` passwords of a throwaway cluster, the file is
  git-ignored, and a real environment takes its userlist from the secrets manager.
- **00.10 local host names**: `HUB_ALLOWED_HOSTS` gained `127.0.0.1` (the compose probes) and
  `hub` (the Host the balancer's health checks send, docs/09 1.4). `.env.example` also gained
  `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` (both `dev`), because the MinIO container needs
  credentials and the task 00.1 rule is that every documented variable is in that file.
- **00.10 `migrate` runs as the superuser locally**: `0000_baseline.sql` creates the roles,
  ownership and grants, so the compose service overrides `HUB_MIGRATE_URL` with the cluster
  superuser and says so in a comment; `.env.example` keeps the `migrator` value a real
  environment uses after that first step (docs/04 10.5).
- **00.10 default `make up` and the two stubs**: `provider-stub` and `meter-sim` sit on the
  `stubs` profile, so a plain `make up` starts 16 containers (14 running, `migrate` and
  `db-logins` exited 0) and stays quiet until slice-T1/T2 replace them. `make up` builds with
  `--build` so an edited Dockerfile is always picked up; the layer cache keeps a warm start
  to a few seconds.
- **00.10 Prometheus has a scrape file but no server**: the plan adds the Prometheus, Tempo,
  Loki and Grafana services with the dashboards (plan note 10), so `prometheus.yml` is the
  contract that slice mounts and the collector keeps the `debug` exporter in the meantime.

- **00.11 spectral needs a ruleset file**: the CLI does not default to the OpenAPI ruleset —
  without a `.spectral.yaml` it exits with "No ruleset has been found", and `--ruleset
  spectral:oas` is read as a path. A two-line `.spectral.yaml` (`extends: [spectral:oas]`) is
  the documented way, so the gate is `spectral lint api/openapi.yaml` exactly as the plan
  words it.
- **00.11 the OpenAPI gate found four real contract defects** (commit `b33070f`): the media
  examples of staff login, MFA verify, customer OTP request and the telemetry batch were the
  bare payload while their schema requires `{data: ...}`. Fixed in the specification rather
  than by lowering the rule: an example that contradicts its own schema is what a consumer
  copies. The document still reports 118 warnings (`operation-description`, unused
  components), which spectral does not fail on.
- **00.11 a seventh job: `stack`**: TC-NFR-14-1 needs "one command brings a clean clone to a
  healthy environment" proved, and docs/09 section 6 says the CI carries it; the job runs
  `make up` and `tests/test_stack_up.py` on the runner's Docker, dumping the stack's state and
  logs when it fails. It is independent of `images` (both build the same image, in parallel).
- **00.11 a skip fails the verification**: `verify_slice.sh` fails when a suite reports
  `N skipped`, because docs/10 section 3 forbids a green run built on skipped requirements —
  the integration suite must reach real Postgres and Redis. This is enforced by the script's
  runner, not by an extra command, so `--print` stays the plan's own list.
- **00.11 step 8 is two commands**: `make up` then the stack test, so a failing `make up` is
  reported on its own line instead of inside one long `&&` chain.
- **00.11 gitleaks allowlist**: `.gitleaks.toml` extends the default ruleset and allowlists
  four paths, each documented in the file — `.env.example` (throwaway `dev` values),
  `api/openapi.yaml` and `docs/` (fabricated examples that match the entropy and JWT rules by
  design) and `tests/` (the literals the redaction tests prove are masked). History is
  scanned whole; nothing was excluded by commit.
- **00.11 the frontend guard is per step, not per job**: a job-level `if` is evaluated before
  the checkout step, so `hashFiles('frontend/package.json')` is always empty there and the
  job would never run even after slice fe-00 lands. Every npm step carries the condition
  instead, with one step saying why the job did nothing.
- **00.11 action SHAs were taken from the GitHub API** on 2026-10-25 (checkout `v7.0.1`,
  setup-python and setup-node `v7.1.0`, setup-uv `v10.3.2`, buildx `v4.4.1`, build-push
  `v7.4.0`, trivy-action `v0.36.0`), and each line keeps its tag as a comment. Renovate
  proposes the next pin.
- **00.11 oasdiff and gitleaks are release binaries, not actions**: `gitleaks/gitleaks-action`
  needs a licence for organisations and `oasdiff` publishes no action, so both are downloaded
  from a pinned release tag into `$RUNNER_TEMP` and run directly, with no `curl | sh` step.
  The oasdiff comparison is skipped until a `v*` tag exists, as the task requires.

- **00.12 the stream pool runs one uvicorn process per container** (`daeef46`): with
  `--workers 2` the supervisor killed the first worker mid-startup at the cold start, because
  it pings a worker every 0.5 s and kills it after a 5 s silence, and the two processes also
  raced for the metrics port — the loser died with `EADDRINUSE` on the read-only root. One
  process per container, scaled by replicas (`server-template stream 2`), is the same shape as
  the rest of the stack and has no supervisor to lose a race against.
- **00.12 the metrics port is bound once, and losing it is not fatal**: `start_metrics_server`
  logs and returns on `EADDRINUSE` (regression test binds a socket first), and the compose
  environment now sets `PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus`, which `metrics.py` already
  promised, so the process that holds the port serves every worker's series.
- **00.12 healthchecks have a 45 s `start_period`**: a cold start imports Django in eight
  containers at once, and without the grace a slow first import counted as a failed check, so
  `up --wait` gave up on a stream container that was already coming up (the first cold start
  failed on exactly that).
- **00.12 MinIO needs an 8-character root password**: `.env.example` had `MINIO_ROOT_PASSWORD=dev`,
  which MinIO refuses; the entrypoint retried for a minute and exited 1, so `make up` failed on
  a container that used to be healthy because it predated the variable. The example and the
  compose default are now `dev-dev-dev`, and the local `.env` gained both MinIO lines.
- **00.12 one access line per request**: demo step 5 needs a JSON line carrying `request_id`,
  `trace_id` and `service`, and docs/09 8 assumes an application access log. `RequestContextMiddleware`
  now writes `request.completed` (method, path, status, route, duration_ms) while the context is
  still bound, and `request.path` never carries a query string (D-18). Health probes are logged
  at INFO too, which is ~2 lines/s on this stack; a later slice can sample them.
- **00.12 gunicorn's own lines are JSON as well**: gunicorn attaches its own handler and stops
  propagation, so `configure_logging` re-points `gunicorn.error`/`gunicorn.access` at the root
  structlog handler once the application is loaded (docs/03 7.3: "one format").
- **00.12 the tracing middleware must be outermost**: `middleware_position` moved 1 -> 0.
  Inside the application middleware the span is not current when the access line is written and
  `trace_id` was null; verified end to end — the line's trace id appears once in the collector.
- **00.12 a fresh pool answers 503 for a few seconds**: `docker compose up --wait` returns as
  soon as the container's own healthcheck passes, but HAProxy needs `rise 3` (~6 s) before it
  routes, so the stack tests poll for up to 20 s before asserting instead of reading the first
  response as the verdict.
- **00.12 the second `server-template` slot is `MAINT (resolution)`**: two slots are reserved
  for scale-out and only one container runs, so the stats test requires at least one `UP`
  server per pool and accepts a template slot with no container behind it as a placeholder.
- **00.12 the verify script calls the checker instead of four bare checks**: `manage.py check
  --deploy` loads the checkout's `.env` (`HUB_ENV=local`) and fails on DEBUG for every module,
  which says nothing about the deployable; `tools/check_django_config.py` runs the same check
  for all six modules with the fallback deployment environment.
- **00.12 demo step 8 as observed**: adding `django.contrib.admin` makes `make lint` fail inside
  mypy's Django plugin before the AST rule prints, so the rule is demonstrated with
  `python tools/check_django_config.py`, which names it (`D-42: never installed`); both gates
  are part of `make lint`.

## Open change requests

None.

## Evidence

### `make verify SLICE=00`, 2026-10-25 (tree of `3153167`)

`tools/scripts/verify_slice.sh 00` ran eleven commands in order and printed
`Slice 00 verification PASSED (11 commands).` with exit 0. Transcript below, one result line per
command; the docker build progress of `make up` (about 430 lines of layer output) is elided and
its result kept.

```
$ make verify SLICE=00
==> make lint
All checks passed!                                       # ruff check
160 files already formatted                              # ruff format --check
Success: no issues found in 158 source files             # mypy strict
Contracts: 119 kept, 0 broken.                           # import-linter
Django configuration checks passed
==> cd backend && python -m pytest tests/unit tests/architecture -q
98 passed in 18.67s
==> cd backend && python -m pytest tests/integration -q -m integration -p no:cacheprovider --strict-markers -rs
31 passed in 37.24s
==> python -m pytest tests -q
42 passed, 5 deselected in 112.10s (0:01:52)
==> python tools/check_traceability.py
0 error(s), 0 warning(s)
==> python tools/check_django_config.py
Django configuration checks passed
==> cd backend && python manage.py makemigrations --check --dry-run --settings=hub.settings.api
No changes detected
==> make up
    ... 14 containers Healthy, migrate and db-logins Exited (0)
==> python -m pytest tests/test_stack_up.py -q -m integration
5 passed in 6.01s
==> docker compose --env-file .env -f infra/docker/compose.yaml config -q
    (no output, exit 0)
==> if command -v actionlint >/dev/null 2>&1; then actionlint; else ... fi
actionlint is not installed: workflow lint skipped
Slice 00 verification PASSED (11 commands).
```

Two differences from the slice's Verification list, both recorded as assumptions above: the
four per-module `manage.py check --deploy` commands are executed by `tools/check_django_config.py`
with its deployment fallback environment, and `actionlint` is optional in `make lint` — it is not
installed on this machine, so it was run by hand (`actionlint 1.7.12` -> exit 0).

### Demo script, as observed

| # | Command | Observed |
|---|---|---|
| 1 | `make reset && make up` (empty volume) | exit 0 in 67 s with the image already built (the 00.10 build adds about two minutes); 14 containers `Healthy`, `migrate` and `db-logins` `Exited (0)` — TC-NFR-14-1 well inside ten minutes |
| 2 | `curl -si localhost:8080/api/v1/health/live` | `HTTP/1.1 200 OK`, `x-request-id: 2eed5a0c-a9d3-46f1-a7a7-c2ba5200308b`, `{"data":{"status":"ok","version":"dev",...}}` |
| 3 | `curl -si localhost:8080/api/v1/health/ready` | `200`, `{"data":{"status":"ok",...,"checks":[{"name":"postgres","status":"ok","latency_ms":96},{"name":"redis",...,13}]}}` |
| 4 | `curl -si localhost:8080/` | `200` from the `hub-web` stub |
| 5 | `docker compose logs hub-api \| tail -1` | one JSON line: `{"method":"GET","path":"/api/v1/health/live","status":200,"route":"api/v1/health/live","duration_ms":0.683,"event":"request.completed","service":"hub-api","version":"dev","env":"local","level":"info","ts":"2026-10-25T02:38:37.528361Z","request_id":"2eed5a0c-...","correlation_id":"2eed5a0c-...","entrypoint":"hub-api","trace_id":"ae6d1045675b4a0fb6f35023ee0050af"}` |
| 6 | `docker compose logs otel-collector \| grep ae6d1045...` | one span: the trace id in the log line is the trace id the collector received |
| 7 | `docker compose stop hub-ingest-webhooks`, 9 s, stats; then `start`, 33 s | `be_webhooks DOWN` while `be_api UP` and `be_telemetry UP`; after the restart `be_webhooks UP` again and a POST through the balancer reaches the pool |
| 8 | `django.contrib.admin` in `INSTALLED_APPS`; `import anthropic` in `hub.payments.domain` | `python tools/check_django_config.py`: `INSTALLED_APPS installs django.contrib.admin (D-42: never installed)`; `lint-imports`: `hub is not allowed to import anthropic ... Only the ai_gateway adapter imports the AI SDK`. Both reverted; `git status` clean before the next run |

### Task 00.10: the stack, as observed

```
$ make up
... Container soleil-lb-1 Healthy
... 13 further containers Healthy (hub-api, both ingest pools, hub-stream, hub-web,
    hub-worker/relay/scheduler, pg-primary, pgbouncer, redis, minio, otel-collector)
... soleil-migrate-1 Exited (0)      soleil-db-logins-1 Exited (0)

$ curl -si localhost:8080/api/v1/health/live
HTTP/1.1 200 OK
x-request-id: 607bb2bd-6af9-4c2d-88fe-dad73ba2c3c3
content-security-policy: default-src 'none'; frame-ancestors 'none'

$ curl -s localhost:8080/api/v1/health/ready
{"data":{"status":"ok","version":"dev","time":"2026-10-25T01:36:06.517227+00:00",
 "checks":[{"name":"postgres","status":"ok","latency_ms":22},
           {"name":"redis","status":"ok","latency_ms":3}]}}

$ curl -si localhost:8080/
HTTP/1.1 200 OK
content-type: text/html

$ python -m pytest tests/test_stack_up.py -q -m integration
1 passed in 0.10s
```

The 503 that the first start produced was real and instructive: the readiness view answered
`service_unavailable` because PgBouncer could not read its userlist (see the assumption above),
which is exactly the failure the `postgres: ok` check is there to surface.
