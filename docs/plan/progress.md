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
| 00.4 | Structured logging, request context, metrics, tracing | done | `pending` | `cd backend && python -m pytest tests/unit/shared -q` -> `30 passed` (11 new); `pytest tests/unit tests/architecture -q` -> `71 passed`; ruff, ruff format, mypy strict clean; `python tools/check_django_config.py` -> exit 0 |
| 00.5 | Error taxonomy and problem+json handler | not started | — | `cd backend && python -m pytest tests/unit/shared/test_problem.py -q` |
| 00.6 | Health endpoints and readiness registry | not started | — | `cd backend && python -m pytest tests/integration/shared/test_health.py -q` |
| 00.7 | DjangoUnitOfWork, outbox writer, worker command stubs | not started | — | `cd backend && python -m pytest tests/integration/shared/test_uow.py tests/unit/shared/test_commands.py -q` |
| 00.8 | SQL baseline migration mechanism (`hubdb`) | not started | — | `cd backend && python -m pytest tests/integration/hubdb -q -m integration` |
| 00.9 | Import-linter contracts and architecture tests | not started | — | `cd backend && python -m pytest tests/architecture/test_importlinter.py -q` |
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

## Open change requests

None.

## Evidence

`make verify SLICE=00` output: pending (task 00.12).
Demo script result: pending (task 00.12).
