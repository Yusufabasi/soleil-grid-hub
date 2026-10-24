# Executor guide: how to build Soleil Grid Hub from this plan

You are the implementing agent. The design is finished and reviewed. Your job is to build it exactly as
specified, slice by slice, with tests, and to leave evidence that a verifier can check. Do not redesign.
If the plan seems wrong or incomplete, follow the change request procedure below instead of improvising.

## 1. What you are building

A prepaid solar mini-grid platform: Django 5 + Django REST framework backend (three deployables
`hub-api`, `hub-ingest`, `hub-worker`), a Next.js frontend (`hub-web`), PostgreSQL 16, Redis 7, an
HAProxy balancer, and Claude through one gateway module. The stack is decided (ADR-0019 to ADR-0021);
do not substitute libraries listed in the plan.

## 2. Read in this order, once

1. `docs/00-shared-context.md`, especially section 3 (invariants) and section 7 (decisions D-01 to
   D-46). Section 7 beats any other document.
2. `docs/adr/README.md`, then only the ADRs a slice file names.
3. `docs/03-code-architecture.md` sections 1 to 4 (layout, modules, dependency rules, events).
4. `docs/11-implementation-plan.md` (the slice index), `docs/plan/contract-amendments.md` (binding
   decisions that beat any slice file), then the slice file you are about to execute.

Each slice file lists the exact document sections you need under "Read first". Do not read the long
design documents end to end; read what the slice names.

## 3. Frozen contract files

You must not edit these. They define what the verifier checks you against.

| File | Why frozen |
|---|---|
| `docs/00-shared-context.md` | the contract and decisions |
| `api/openapi.yaml`, `api/permissions.yaml` | the API contract and roles |
| `db/schema.sql`, `db/invariant_tests.sql`, `db/migrations/*` already present | the tested database |
| `docs/adr/*` | recorded decisions |
| `tests/test_alignment.py` enumeration assertions, `tests/test_schema_in_postgres.py`, `tools/check_traceability.py` | the alignment gates |

New numbered SQL migrations are the one exception: you may add `db/migrations/NNNN_name.sql` files
when a slice file instructs it, following the expand then contract rule in `docs/04-data-model.md`.

### Change request procedure

If you believe a frozen file or a plan step is wrong, impossible or ambiguous:

1. Stop that task. Do not edit the frozen file and do not work around the rule.
2. Write `docs/plan/change-requests/CR-NNN-short-name.md` containing: the task and slice, what you
   expected, what you found (with command output), the smallest change you propose, and which
   requirements or tests it touches.
3. Record it in `docs/plan/progress.md` under "Open change requests" and continue with a task that does
   not depend on it. If every remaining task depends on it, stop and report.

For something merely unspecified inside a slice (a name, a default), choose the safest option, write
the assumption in `docs/plan/progress.md` under "Assumptions", and continue.

## 4. Working rules (non-negotiable)

1. **Order.** Slices run in the order of `docs/11-implementation-plan.md`, respecting "Depends on". Do
   not start a slice before its dependencies pass their verification commands.
2. **Test first.** For every task: write the failing test named in the slice file, run it and see it fail
   for the stated reason, implement, run again, see it pass. Do not delete, skip, loosen or mark expected-fail
   any test to make it pass. If a test is wrong, raise a change request.
3. **Small commits.** One commit per task, conventional message (`feat(ledger): post balanced
   transactions`), tests and code together. Never commit secrets, `.env` files or generated credentials.
4. **Forbidden shortcuts**, each enforced by a CI check you will build in slice 0:
   - installing or enabling the Django admin site;
   - `ATOMIC_REQUESTS = True`;
   - managed Django models (`managed = True` or omitted) for hub tables;
   - writing ledger, wallet or audit tables through the ORM (use the definer functions);
   - importing another module's models, repositories or internals (only `contracts.py` and `events.py`);
   - `print` or logging of personal data, tokens, keys or raw webhook bodies;
   - any model-controlled write (the AI gateway role may only insert into `ai_runs`);
   - any endpoint that is not in `api/openapi.yaml`, any role not in `api/permissions.yaml`;
   - any test-only HTTP endpoint (use the fixtures command, D-38);
   - Next.js Server Actions, the Next.js image optimiser, secrets in `NEXT_PUBLIC_*`.
5. **Contract tests stay green** at the end of every task: `tests/test_alignment.py`,
   `tests/test_schema_in_postgres.py` and `tools/check_traceability.py` run from the repository root.
6. **No invented behaviour.** If the plan, the OpenAPI file and the schema do not say it, do not build it.
7. **Security defaults.** Parameterised SQL only; deny by default; validate every input with the
   serializer; never trust a client-supplied role, customer id or site id; never log secrets.
8. **Time and money.** Money is integer minor units plus a currency, never floats. Timestamps are UTC.
   Ids are UUIDv7 from the `IdGenerator` port. Use the `Clock` port, never `datetime.now()` in domain code.
9. **Open items.** Items marked `[open]` in the contract have a default in the slice file that mentions
   them; use the default and make it a setting, not a constant.

## 5. Commands you will rely on

Slice 0 creates these; every later slice uses them. All run from the repository root.

| Command | What it does |
|---|---|
| `make up` / `make down` | start / stop the local stack (compose) |
| `make test` | backend tests, frontend unit tests, contract tests |
| `make lint` | ruff, mypy, import-linter, ESLint, TypeScript |
| `make contracts` | alignment tests, schema tests (needs Docker), traceability check, OpenAPI validation |
| `make verify SLICE=NN` | the verification commands of one slice (the same list the verifier runs) |

## 6. Evidence you must leave

Keep `docs/plan/progress.md` up to date. For each task: status, commit hash, the test command you ran
and its summary line (for example `38 passed`). For each slice: the output of `make verify SLICE=NN`
pasted in full, the demo script result, and any assumptions or change requests. The verifier will rerun
everything, but missing evidence is treated as a failed task.

## 7. Definition of done

A task is done when: its tests pass, `make lint` is clean, contract tests are green, the commit is made,
and `progress.md` is updated. A slice is done when all its tasks are done, `make verify SLICE=NN` passes,
the demo script runs, and every requirement the slice lists has its test cases (the `TC-` ids named in
the slice file) implemented and passing. The whole build is done when `docs/plan/99-verification.md`
passes in full.

## 8. When you are stuck

Try twice, then stop. Record what you tried and the exact error in `progress.md`, raise a change
request if the plan is at fault, and report. Do not weaken a control, skip a test or widen a permission
to get unstuck.

## 9. Keep the cost down

Work one slice per session. Read only the sections a slice names. Run only the tests for the files you
changed while iterating, and the full `make verify` once at the end of each task group. Do not re-read
documents you have already read in the session; take notes in `progress.md` instead.
