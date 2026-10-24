# Slice T4: Verification tooling

- **Goal:** the tools the verifier runs: `tools/verify_forbidden_patterns.py` (the forbidden shortcuts of the executor guide section 4), the models-versus-database introspection test, the migration-equivalence test, the schemathesis conformance run, the generated authorization matrix test, and the `make verify` wiring with one registry of commands per slice.
- **Depends on:** slice 0 (Makefile, settings modules, `tools/check_django_config.py`, compose). Task T4.7 also needs slice 1 (real tokens); it is built after slice 1 passes.
- **Requirements served:** NFR-06, NFR-14, FR-15 (as gates); supports FR-03, FR-04, FR-12 invariants by scanning for shortcuts.
- **Size:** L, 7 days.
- **Companion files:** [slice-T1-provider-stub.md](slice-T1-provider-stub.md), [slice-T2-meter-simulator.md](slice-T2-meter-simulator.md), [slice-T3-fixtures-and-seed.md](slice-T3-fixtures-and-seed.md), [slice-17-hardening.md](slice-17-hardening.md), [99-verification.md](99-verification.md).
- **Spikes or open items:** the schemathesis major version (the plan pins the 3.x line; see plan notes). No business item is open.

## Read first

- `docs/plan/00-executor-guide.md#4. Working rules (non-negotiable)` and `#5. Commands you will rely on`
- `docs/00-shared-context.md#7.7 Stack and delivery model (added after the user stated their preferred stack)` (D-42, D-43, D-46) and `#7.1 Authority and naming` (D-01)
- `docs/03-code-architecture.md#9. Coding standards and definition of done` (CI configuration checks paragraph) and `#3.3 Enforcement with import-linter`
- `docs/09-devops-sre.md#2.2 Pipeline stages` (rows Django policy checks, Migration check, Contract, Alignment and permissions)
- `docs/10-test-strategy.md#2.11 Stack-specific controls (D-42 to D-46)` and `#7.1 CI gates`
- `api/permissions.yaml` (header comment and shape) and `api/openapi.yaml#/components/securitySchemes`
- `docs/03-code-architecture.md#7.7 API layer conventions (Django REST framework)` item 10 (conformance test)

## Global constraints

- Forbidden shortcuts (executor guide section 4, rule 4): Django admin installed; `ATOMIC_REQUESTS = True`; managed models; ORM writes to ledger, wallet or audit tables; importing another module's internals (only `contracts.py` and `events.py`); `print` or logging of personal data, tokens, keys or raw webhook bodies; model-controlled writes (the AI gateway role may only insert into `ai_runs`); an endpoint not in `api/openapi.yaml` or a role not in `api/permissions.yaml`; any test-only HTTP endpoint; Next.js Server Actions, the Next.js image optimiser, secrets in `NEXT_PUBLIC_*`.
- Models are `managed = False`, mapped to the `hub` schema; `ATOMIC_REQUESTS` is false for every alias; `django.contrib.admin` is in no settings module (D-42, D-43).
- `api/openapi.yaml` is the authority and is not generated from code; conformance is proven with schemathesis against the running app (D-42).
- Schemathesis checks: `not_a_server_error`, `status_code_conformance`, `content_type_conformance`, `response_schema_conformance`; stateful links off; examples used first; setup by the fixtures command, never a test-only endpoint (docs/03 section 7.7 item 10, D-38).
- Unlisted role on an operation gets 403 (401 when unauthenticated); every operation in OpenAPI is in `api/permissions.yaml` and the reverse (TC-FR-15-1, TC-FR-15-18).
- Frozen files are never edited by these tools; the tools only read them.
- Exit codes are the contract between tools and `make`: 0 pass, 1 findings or failures, 2 usage or configuration error, 3 scanner self-test failure, 4 timeout.
- A suppression is allowed only through `tools/verify/allowlist.yaml` (path, rule id, reason, expiry date within 30 days); the file starts empty and a non-empty file fails the verifier gate unless the tech lead approved it in `docs/plan/progress.md`.
- Required environment: `HUB_ALIGN_DATABASE_URL` (a scratch PostgreSQL 16 server where the tools may create databases; never a shared database).

## Review focus

1. A scanner rule that matches nothing silently (the pattern is wrong), so a forbidden shortcut passes the gate. Pinned by `test_every_rule_fires_on_its_bad_corpus` and `--self-test` in Task T4.3.
2. The migration-equivalence comparison hides differences (grants, role settings, triggers, partitions) because it compares only table columns. Pinned by `test_detects_missing_grant`, `test_detects_changed_trigger_timing` and `test_detects_missing_role_setting` in Task T4.4.
3. The model-versus-database test passes when a model is missing for a table, or when a column type changes within a compatible family. Pinned by `test_table_without_model_fails_unless_listed` and `test_type_mismatch_detected` in Task T4.5.
4. Schemathesis runs unauthenticated on every operation and passes on 401 responses. Pinned by `test_runner_refuses_when_hook_cannot_mint_all_roles` in Task T4.6.
5. The authorization matrix skips operations or roles it cannot build principals for. Pinned by `test_matrix_cell_count_equals_operations_times_principals` and `test_no_skips_allowed` in Task T4.7.

## Files

- `tools/verify_forbidden_patterns.py`: command line entry; loads rules, scans, reports.
- `tools/verify/__init__.py`, `tools/verify/rules/__init__.py`: package.
- `tools/verify/rules/python_rules.py`: rules FP-01 to FP-06, FP-11 (AST based).
- `tools/verify/rules/web_rules.py`: rules FP-07 to FP-09 (file and JSON based).
- `tools/verify/rules/route_rules.py`: rule FP-10 (routes versus OpenAPI).
- `tools/verify/report.py`: text and JSON report.
- `tools/verify/allowlist.yaml`: empty list.
- `tools/verify/corpus/bad/FP-NN/*` and `tools/verify/corpus/good/FP-NN/*`: one or more files per rule that must trigger or must not trigger it.
- `tools/verify/schema_snapshot.py`: catalog snapshot of a database to JSON.
- `tools/verify/compare_snapshots.py`: diff of two snapshots.
- `tools/verify/migration_equivalence.py`: builds two scratch databases and compares them.
- `tests/test_django_alignment.py`: models versus database test and migration-equivalence test (the file docs/10 section 7.1 names).
- `tests/alignment/unmodelled_tables.yaml`: tables allowed to have no model, each with a reason.
- `tools/verify/run_schemathesis.py`: runner with the settings below.
- `tools/verify/schemathesis_hooks.py`: authentication hook and operation exclusions.
- `tools/verify/principals.py`: mints a principal per role and scheme using the fixtures command and the real login endpoints.
- `tools/verify/route_inventory.py`: dumps method and path of every URLConf of each pool.
- `backend/tests/security/test_authz_matrix.py`: generated matrix (parametrised at collection time from `api/permissions.yaml`).
- `backend/tests/security/test_route_inventory.py`: routes equal OpenAPI operations.
- `tools/verify/registry.py`, `tools/verify/run.py`, `tools/verify/slices.yaml`: the `make verify` dispatcher and its registry.
- `tools/verify/tests/`: `test_run.py`, `test_rules_python.py`, `test_rules_web.py`, `test_rules_routes.py`, `test_snapshot.py`, `test_schemathesis_runner.py`, `test_report.py`.
- `Makefile` (modify): targets `verify`, `verify-global`, `verify-all`, `forbidden`, `schemathesis`, `authz-matrix`, `alignment`.
- `tools/verify/requirements.txt`: pinned `schemathesis`, `pyyaml`, `pytest`, `psycopg[binary]`.

## Interfaces

**Consumes:** the repository tree, `api/openapi.yaml`, `api/permissions.yaml`, `db/schema.sql`, `db/migrations/*.sql`, a running `hub-api`, `hub-ingest` (both Deployments) for schemathesis (`HUB_BASE_URL`, default `http://localhost:8080`), Django settings modules, `HUB_ALIGN_DATABASE_URL`.

**Produces**

- `python tools/verify_forbidden_patterns.py [--root .] [--paths P ...] [--json build/verify/forbidden.json] [--self-test]`. Output lines `RULE FP-04 path:line message`; exit 0 clean, 1 findings, 2 bad usage, 3 self-test failed.
- `python tools/verify/schema_snapshot.py --dsn DSN --out FILE`; `python tools/verify/compare_snapshots.py A B` prints the first 20 differences as `object-kind name: a != b`, exit 0 or 1.
- `python tools/verify/migration_equivalence.py` exit 0 or 1; leaves nothing behind (drops both scratch databases).
- `python tools/verify/run_schemathesis.py [--profile merge|nightly] [--base-url URL] [--report build/verify/schemathesis.json]`; exit 0 or 1, 2 when a role token cannot be minted.
- `python tools/verify/run.py (--slice ID | --global | --all) [--registry tools/verify/slices.yaml] [--report-dir build/verify] [--allow-skip-docker]`; `make verify SLICE=NN` calls it with `--slice NN` (`T1` to `T4` and `17` accepted).
- Registry entry shape in `slices.yaml`: `slices: {"04": [{id, cmd, expect, timeout_s, needs: [docker]}]}` and a `global:` list. Each slice executor adds its commands to this file exactly as written in its own Verification section.
- Rule catalogue (ids are stable):

| Id | Shortcut | Detection |
|---|---|---|
| FP-01 | Django admin installed | `django.contrib.admin` string in any `INSTALLED_APPS` of `backend/src/hub/settings/*.py` or in any URLConf `admin.site` use |
| FP-02 | `ATOMIC_REQUESTS` true | assignment or dict key `ATOMIC_REQUESTS` with a value other than literal `False` |
| FP-03 | Managed model | class in `*/infrastructure/models.py` without `Meta.managed = False`, or `managed = True` |
| FP-04 | ORM or raw write to ledger, wallet, audit tables | `.objects.create/update/delete/bulk_*`, `.save()`, `.delete()` on models mapped to `ledger_*`, `wallet_balances`, `audit_*`; SQL strings with INSERT, UPDATE, DELETE, TRUNCATE on those tables outside `db/` and the SQL definer functions |
| FP-05 | Cross-module import | `from hub.<a>...` inside module `b` where the target is not `hub.<a>.contracts` or `hub.<a>.events` (a second line of defence beside import-linter) |
| FP-06 | Logging or printing personal data | `print(` anywhere in `backend/src`; logger calls whose arguments name `phone`, `msisdn`, `email`, `token`, `secret`, `api_key`, `body`, `raw_body`, `authorization`, or format strings matching a phone pattern |
| FP-07 | Secret in `NEXT_PUBLIC_*` | any `NEXT_PUBLIC_` name in `frontend/**`, `.env*`, compose and Dockerfiles matching `SECRET|KEY|TOKEN|PASSWORD|PRIVATE` |
| FP-08 | Server Actions | `"use server"` or `'use server'` directive, `serverActions` set truthy in `next.config.*` |
| FP-09 | Image optimiser | `images.unoptimized` not `true` in `next.config.*`, or `/_next/image` referenced |
| FP-10 | Test-only route | a URLConf route, route handler or Next.js route file whose method and path is not an operation of `api/openapi.yaml` (health and static excluded by explicit list), or whose path segment is `test`, `fixtures`, `reset`, `debug`, `__test__` |
| FP-11 | Model-controlled write | any write statement in `backend/src/hub/ai_gateway/**` to a table other than `ai_runs`, and any import of a repository from another module there |

## Tasks

### Task T4.1: Runner, registry and Makefile wiring

- [ ] Step 1, failing tests in `tools/verify/tests/test_run.py`: `test_unknown_slice_exit_2`, `test_all_commands_pass_exit_0` (registry with two `true` commands), `test_failing_command_exit_1_and_named` (stdout contains `FAIL <id>`), `test_timeout_exit_4` (command `sleep 5`, `timeout_s: 1`), `test_docker_needed_without_docker_exit_1` (unless `--allow-skip-docker`), `test_report_json_written` (keys `slice`, `results[{id,status,seconds}]`), `test_registry_rejects_duplicate_ids`, `test_make_verify_passes_slice_through` (runs `make -n verify SLICE=04` and asserts the output contains `--slice 04`).
- [ ] Step 2: `python -m pytest tools/verify/tests/test_run.py -q`; expect `ModuleNotFoundError: tools.verify.run`.
- [ ] Step 3: implement `registry.load(path) -> dict`, `run.main(argv) -> int`; commands run with `subprocess.run(shell=False)` through `shlex.split`, working directory the repository root, one process at a time, output streamed and tee'd to `build/verify/<slice>.log`. Add Makefile targets; `verify` without `SLICE` fails with a message.
- [ ] Step 4: rerun until `8 passed`.
- [ ] Step 5: commit `feat(verify): make verify dispatcher and registry`.

### Task T4.2: Forbidden patterns, Python rules

- [ ] Step 1, failing tests in `tools/verify/tests/test_rules_python.py` using the bad and good corpus: `test_fp01_admin_in_installed_apps`, `test_fp02_atomic_requests_true_and_variable_value`, `test_fp03_missing_meta_and_managed_true`, `test_fp04_orm_save_on_ledger_model`, `test_fp04_raw_sql_insert_into_ledger_entries`, `test_fp04_select_is_allowed`, `test_fp05_import_of_other_module_models`, `test_fp05_contracts_import_allowed`, `test_fp06_print_flagged`, `test_fp06_logger_with_msisdn_argument`, `test_fp06_logger_with_plain_message_allowed`, `test_fp11_write_to_other_table_in_ai_gateway`, `test_fp11_insert_into_ai_runs_allowed`. Each asserts `findings == [("FP-04", "corpus/bad/FP-04/orm_write.py", 7)]` style tuples.
- [ ] Step 2: run; expect import error.
- [ ] Step 3: implement `python_rules.py` with `ast` visitors (`scan_python(path, tree) -> list[Finding]`); `Finding` is a frozen dataclass `(rule, path, line, message)`. Model-to-table mapping comes from a first pass that reads `db_table` in `Meta` of each models file. SQL strings are checked by a regex over string constants and f-strings (INSERT INTO or UPDATE or DELETE FROM followed by a protected table name, case-insensitive, optional `hub.` prefix). Write the corpus files while writing the tests.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(verify): forbidden-pattern rules for Python`.

### Task T4.3: Forbidden patterns, web and route rules, report, self-test

- [ ] Step 1, failing tests in `test_rules_web.py`, `test_rules_routes.py`, `test_report.py`: `test_fp07_secret_named_public_var` (`NEXT_PUBLIC_API_KEY` flagged; `NEXT_PUBLIC_API_BASE` allowed), `test_fp07_in_dockerfile_and_env_file`, `test_fp08_use_server_directive`, `test_fp08_server_actions_enabled_in_config`, `test_fp09_images_not_unoptimized`, `test_fp09_unoptimized_true_allowed`, `test_fp10_route_not_in_openapi`, `test_fp10_path_segment_reset`, `test_fp10_health_allowed`, `test_json_report_shape`, `test_exit_code_1_on_finding_0_on_clean`, `test_every_rule_fires_on_its_bad_corpus` (loops all rule ids, asserts at least one finding in `corpus/bad/<id>` and none in `corpus/good/<id>`), `test_self_test_exit_3_when_rule_disabled` (monkeypatch removes FP-08, `--self-test` exits 3), `test_allowlist_entry_suppresses_and_expires` (expired entry does not suppress), `test_nonempty_allowlist_reported`.
- [ ] Step 2: run the three files; expect failures.
- [ ] Step 3: implement `web_rules.py` (regex over text files; JSON or TypeScript config read with a tolerant regex for `unoptimized\s*:\s*true` and `serverActions`), `route_rules.py` (walks the `route_inventory` JSON when given `--routes FILE`, or reads `api/openapi.yaml` and Next.js `route.ts` files), `report.py`, allowlist handling and `--self-test`. The CLI scans `backend/src`, `frontend`, `infra`, `tools` by default and excludes `tools/verify/corpus` unless `--self-test`.
- [ ] Step 4: rerun until pass; run `python tools/verify_forbidden_patterns.py` on the real tree: exit 0 (slice 0 content must be clean; if not, fix the offending file, never the rule).
- [ ] Step 5: commit `feat(verify): web and route rules, report and self-test`.

### Task T4.4: Schema snapshot and migration equivalence

- [ ] Step 1, failing tests in `tools/verify/tests/test_snapshot.py` (scratch server): `test_snapshot_is_deterministic` (twice equal), `test_detects_missing_grant` (revoke one privilege in database B), `test_detects_changed_trigger_timing` (recreate a trigger as AFTER instead of BEFORE), `test_detects_missing_role_setting` (`ALTER ROLE analytics_support RESET statement_timeout`), `test_detects_extra_index`, `test_detects_changed_function_body` (function source differs by one character), `test_identical_databases_exit_0`, `test_partition_definitions_compared`.
- [ ] Step 2: run; expect import errors.
- [ ] Step 3: implement `schema_snapshot.py`: queries ordered by name for tables and columns (type, nullability, default), constraints (`pg_get_constraintdef`), indexes (`pg_get_indexdef`), triggers (`pg_get_triggerdef`, plus `tgenabled`), functions (`pg_get_functiondef`, owner, `prosecdef`, `proconfig`), views (`pg_get_viewdef`), policies (`pg_policy`), RLS flags (`relrowsecurity`, `relforcerowsecurity`), partition bounds (`pg_get_expr(relpartbound)`), grants (`relacl`, `proacl`, `nspacl`, column ACLs), `pg_db_role_setting` rows and `pg_roles.rolconnlimit`, default privileges, schema ownership. `migration_equivalence.py` creates `align_a_<pid>` from `db/schema.sql` and `align_b_<pid>` by running `python backend/manage.py migrate --settings=hub.settings.migrate` against it, snapshots both, compares, and also compares a normalised `pg_dump --schema-only` (drop comment lines, `SET` lines and `\restrict` lines; sort `CREATE` statements) as a second signal. Note: roles are cluster-wide, so both databases share them; role settings are database-scoped entries and so are compared.
- [ ] Step 4: rerun until pass; run `python tools/verify/migration_equivalence.py`: exit 0 on the repository state.
- [ ] Step 5: commit `feat(verify): catalog snapshot and migration-equivalence check`.

### Task T4.5: Models versus database

- [ ] Step 1, failing tests in `tests/test_django_alignment.py` (run with `DJANGO_SETTINGS_MODULE=hub.settings.test`, database loaded from `db/schema.sql`) plus meta tests in `tools/verify/tests/test_models_alignment_meta.py`: `test_every_model_is_unmanaged`, `test_every_model_table_exists_in_hub_schema`, `test_columns_match_name_nullability_and_type_family` (type families: uuid, text including varchar and char, int2/int4/int8, numeric, bool, timestamptz, date, jsonb, bytea, inet, interval), `test_table_without_model_fails_unless_listed` (a database table absent from every model and from `tests/alignment/unmodelled_tables.yaml` fails), `test_unmodelled_list_entries_have_reasons_and_exist`, `test_type_mismatch_detected` (meta test: a fake model declares `IntegerField` for a `bigint` column and the checker reports it), `test_primary_key_matches`, `test_partition_children_ignored_but_parent_modelled_or_listed`.
- [ ] Step 2: run `cd backend && python -m pytest ../tests/test_django_alignment.py -q`; expect failures for the missing helper.
- [ ] Step 3: implement the helper `tools/verify/models_alignment.py` (`compare(models, introspection) -> list[Difference]`) using `information_schema.columns` and `pg_index` for the primary key, mapping Django fields to families by `field.get_internal_type()`. Models in the unmodelled list: partitions, the default partition, and any table the executor justifies in one line (for example `schema_migrations`). The test file imports the helper and also hosts the migration-equivalence test that calls `migration_equivalence.main()` and asserts 0.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(verify): model-versus-database alignment test`.

### Task T4.6: Schemathesis runner

- [ ] Step 1, failing tests in `tools/verify/tests/test_schemathesis_runner.py`: `test_command_line_contains_required_checks` (built argument list contains the four checks and the example-first phase), `test_profiles_set_max_examples` (merge 100, nightly 500), `test_runner_refuses_when_hook_cannot_mint_all_roles` (fake principals missing `finance_officer`: exit 2, message names the role), `test_excluded_operations_are_listed_and_justified` (the exclusion list is exactly `streamDashboard` and each entry has a reason string), `test_report_written_with_counts`, `test_nonzero_schemathesis_exit_maps_to_1`.
- [ ] Step 2: run; expect failures.
- [ ] Step 3: implement `run_schemathesis.py`. Settings: `schemathesis run api/openapi.yaml --base-url $HUB_BASE_URL --checks not_a_server_error,status_code_conformance,content_type_conformance,response_schema_conformance --hypothesis-max-examples N --hypothesis-deadline 10000 --hypothesis-derandomize` (merge profile) with stateful links disabled, examples first, the version pinned in `requirements.txt` (3.x CLI; with 4.x the options keep their names except `--hypothesis-max-examples` becomes `--max-examples`). Operations are fuzzed once per allowed role from `api/permissions.yaml` through a custom authentication provider in `schemathesis_hooks.py` (token or key or HMAC minted by `principals.py` for the role); the HMAC provider signs the serialised request body bytes after generation. The `streamDashboard` operation is excluded from fuzzing (event stream) and covered by TC-FR-13-5 to TC-FR-13-7; no other exclusion is allowed. Setup: the runner first calls `python manage.py load_fixtures --profile e2e` (slice T3).
- [ ] Step 4: rerun until pass; against the running skeleton (`make up`) `python tools/verify/run_schemathesis.py --profile merge` exits 0 for the operations that exist; operations not yet implemented return 404 and fail `status_code_conformance`: this is expected to fail until the slice owning them is done, so the registry runs this command only in the global gate.
- [ ] Step 5: commit `feat(verify): schemathesis conformance runner`.

### Task T4.7: Authorization matrix and route inventory (after slice 1)

- [ ] Step 1, failing tests in `backend/tests/security/test_authz_matrix.py` and `test_route_inventory.py`: `test_matrix_cell_count_equals_operations_times_principals` (cells = number of OpenAPI operations x 9 principals: `customer`, `support_agent`, `finance_officer`, `operations_engineer`, `administrator`, `integrator`, `gateway`, `provider`, `anonymous`), parametrised `test_unlisted_principal_denied[operationId-principal]` (expects 403, or 401 for `anonymous` on non-public operations), `test_listed_principal_not_denied[operationId-principal]` (response status not in 401, 403 and below 500; 400, 404, 409, 422 accepted), `test_permission_yaml_and_openapi_operation_sets_equal`, `test_no_skips_allowed` (the run reports zero skipped), `test_permission_class_reads_yaml` (importing `hub.shared.drf.permissions` loads `api/permissions.yaml`; the module source contains no role-name literal), `test_every_url_route_is_an_openapi_operation`, `test_no_openapi_operation_without_route`.
- [ ] Step 2: run `cd backend && python -m pytest tests/security -q`; expect collection errors.
- [ ] Step 3: implement `principals.py` (staff: login through `/api/v1/auth` with fixtures users and the TOTP secret file, then step-up where an operation needs it; customer: OTP flow with a fixtures-seeded challenge; integrator and gateway: keys created through the fixtures command, bound to a fixtures site; provider: HMAC signer; anonymous: no credentials) and `route_inventory.py` (imports each pool URLConf under its settings module and lists `(method, path)` from DRF routers and `urlpatterns`). The matrix reads `api/permissions.yaml` and `api/openapi.yaml` at collection time and generates parameters; no generated file is committed. Operations that need a path id use ids from `--out-ids` of the fixtures command.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(verify): generated authorization matrix and route inventory`.

## Verification

`make verify SLICE=T4` runs:

1. `python -m pytest tools/verify/tests -q` : exit 0, at least 70 tests passed.
2. `python tools/verify_forbidden_patterns.py --self-test` : exit 0.
3. `python tools/verify_forbidden_patterns.py` : exit 0 on the real tree.
4. `python tools/verify/migration_equivalence.py` : exit 0.
5. `cd backend && python -m pytest ../tests/test_django_alignment.py -q` : exit 0.
6. `python tools/verify/run.py --slice T4 --report-dir build/verify` : exit 0, report file exists.
7. `python tools/verify/run_schemathesis.py --profile merge --base-url http://localhost:8080` and `cd backend && python -m pytest tests/security -q` : run in the global gate (they need slices 1 and later); at slice T4 time only the unit tests of these runners are required.

## Demo script

1. Add `print(msisdn)` to a backend file; run `python tools/verify_forbidden_patterns.py`: exit 1 with `RULE FP-06`. Remove it.
2. Revoke one grant in the scratch database copy and run `python tools/verify/compare_snapshots.py` on two snapshots: the difference names the grant.
3. `make verify SLICE=T1` runs the T1 commands from the registry and prints PASS lines.
4. Show the matrix cell count and one denied cell for `administrator` on `createLedgerAdjustment`.

## Done criteria

- All tasks committed, `make verify SLICE=T4` green, progress file updated; the registry contains an entry for slice T1 to T4 and 00.
- Test cases this slice implements or enforces: TC-NFR-06-7, TC-NFR-06-9, TC-NFR-06-10, TC-NFR-06-12, TC-NFR-06-13, TC-NFR-14-4, TC-NFR-14-6, TC-NFR-14-7, TC-FR-15-1, TC-FR-15-2, TC-FR-15-18, TC-FR-04-6 (scanner part).

## Plan notes for the tech lead

1. docs/10 section 7.1 names `tests/test_django_alignment.py` at the repository root; docs/03 places such checks in `backend/tests/architecture/`. Followed docs/10 for the alignment file and docs/03 for `backend/tests/security/`.
2. docs/09 lists a model-versus-database test and a migration-equivalence test as pipeline stages; docs/03 section 9 lists `tools/check_django_config.py` for the admin, atomic and managed checks. The scanner repeats FP-01 to FP-03 on purpose as a second signal; slice 0 owns `check_django_config.py`.
3. The executor guide says `make verify SLICE=NN`; this plan also accepts `T1` to `T4`. Slice 0's Makefile must declare `verify` so that T4.1 can replace its body.
4. Schemathesis 3.x and 4.x differ in option names; the plan pins the 3.x line and records the 4.x rename. The tech lead may prefer 4.x.
5. docs/03 section 7.7 item 10 says authentication through "a hook that mints tokens for each role in `x-roles`"; fuzzing once per allowed role multiplies run time (about 130 operations x roles). The merge profile uses 100 examples; if the run exceeds 12 minutes, the CI budget of docs/09 section 2.2 is exceeded and the nightly profile should become the full run.
6. D-14 says `x-roles` in OpenAPI is generated from `api/permissions.yaml`; the frozen files already agree and `tests/test_alignment.py` checks it, so no generator is built here.
