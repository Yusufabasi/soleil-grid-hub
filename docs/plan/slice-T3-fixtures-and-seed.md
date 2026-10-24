# Slice T3: Fixtures command, seed data and test factories

- **Goal:** one management command, `python manage.py load_fixtures`, that sets up end-to-end and perf data against a test or local database (D-38, no test-only HTTP endpoint); synthetic bilingual seed data (customers, sites, tickets, knowledge base); time control; plain-Python factories for backend tests; reset between tests.
- **Depends on:** slice 0 (project, schema loaded, Makefile). Needs no business module code: it writes through SQL and the definer functions. Must be finished before the first end-to-end or integration test that needs data (slice 2 onward).
- **Requirements served:** FR-16 (bilingual data), NFR-06 (TC-NFR-06-7), NFR-07 (synthetic data only), NFR-14 (one-command environment), NFR-03 (perf profile).
- **Size:** M, 5 days.
- **Companion files:** [slice-T1-provider-stub.md](slice-T1-provider-stub.md), [slice-T2-meter-simulator.md](slice-T2-meter-simulator.md), [slice-T4-verification-tooling.md](slice-T4-verification-tooling.md), [slice-17-hardening.md](slice-17-hardening.md).
- **Spikes or open items:** the perf profile telemetry depth (default 7 days, see plan notes); the synthetic phone range is a project convention (see plan notes). No business item is open.

## Read first

- `docs/00-shared-context.md#7.6 AI` (D-38 only) and `#7.2 Canonical state machines`
- `docs/plan/00-executor-guide.md#4. Working rules (non-negotiable)`
- `docs/10-test-strategy.md#6. Test environments and data` (6.1 to 6.5)
- `docs/09-devops-sre.md#1.2 One-command local environment`
- `docs/03-code-architecture.md#5. Ports and adapters` (`Clock`, `IdGenerator` rows) and `#9. Coding standards and definition of done` (Backend tests row)
- `db/schema.sql` sections for `users`, `user_roles`, `user_sites`, `sites`, `plans`, `plan_tariffs`, `customers`, `meters`, `meter_assignments`, `tickets`, `ticket_messages`, `kb_articles`, `kb_article_versions`, `top_ups`, `provider_events`, and the functions `create_wallet_account` and `post_ledger_transaction`

## Global constraints

- The command refuses to run unless `settings.HUB_ENV` is one of `local`, `test`, `ci` and the database name matches `^hub_(local|test|ci)(_[a-z0-9]+)?$` (TC-NFR-06-7); the refusal exit code is 3 and writes nothing.
- No HTTP route is added for fixtures, time or reset (D-38); CI fails if any route appears that is not in `api/openapi.yaml`.
- Ledger rows are only created through `hub.post_ledger_transaction` (kind `topup_received` needs a stored provider event and a top-up row); never `INSERT INTO ledger_*` or `wallet_balances` (forbidden pattern).
- Ids are UUIDv7 from the `IdGenerator` port (`hub.uuid_v7()` only in SQL seeds); timestamps UTC; time through the `Clock` port.
- No real personal data anywhere; phone numbers only in the range `+243810000000` to `+243810999999` (E.164, passes `ck_customers_phone`); emails only `@example.test`.
- One customer per phone number (D-11); customer language is `en` or `fr` (`ck_customers_language`).
- All generation is seeded: `SEED` environment variable or `--seed`, default 20261024; the same seed yields byte-identical SQL output.
- Staff users exist for every role: `support_agent`, `finance_officer`, `operations_engineer`, `administrator`; TOTP secrets for local use are written to `db/seed/local_staff.json` (gitignored, never committed, never printed).
- Local seed size: 200 customers, 5 sites; tickets and KB articles in EN and FR; about 10 percent of tickets contain Swahili or Lingala loanwords; adversarial strings present (docs/10 section 6.2).
- Perf profile: 300,000 customers and meters via `COPY`; telemetry through `COPY` into partitions; no ORM bulk insert (D-43).
- Distribution (docs/10 6.2): payday spikes, 2 percent chronically offline meters, 0.5 percent disputed payments, 1 percent malformed telemetry.

## Review focus

1. The environment guard checks only the environment variable, so a developer with a production connection string in the shell wipes it. Pinned by `test_refuses_database_name_outside_pattern` and `test_refuses_non_test_env` in Task T3.1.
2. Fixture wallet funding bypasses the definer function (direct inserts) and so hides ledger bugs. Pinned by `test_wallet_funding_uses_definer_function` in Task T3.4.
3. A real-looking phone number or email slips into generators, tests or seed files. Pinned by `test_no_phone_outside_synthetic_range` (repository scan) in Task T3.2.
4. Reset leaves state in Redis or object storage while the database is restored, so the next test sees ghost data. Pinned by `test_reset_clears_redis_and_object_store` in Task T3.6.
5. Time control leaks into production configuration. Pinned by `test_offset_clock_refused_when_env_prod` in Task T3.5.

## Files

- `backend/src/hub/shared/management/commands/load_fixtures.py` (create; the directory exists from slice 0): the command, thin; it parses arguments and calls the package below.
- `backend/src/hub/shared/management/commands/fixtures_time.py`: sub-command `fixtures_time set|advance|clear`.
- `backend/src/hub/shared/management/commands/fixtures_reset.py`: reset command.
- `backend/src/hub/fixtures/__init__.py`: package (not a Django app; no models, no migrations).
- `backend/src/hub/fixtures/guard.py`: environment and database-name guard.
- `backend/src/hub/fixtures/rng.py`: seeded generator wrappers (`random.Random`, no global state).
- `backend/src/hub/fixtures/names.py`: fictional name, place and phone generators, EN and FR text templates.
- `backend/src/hub/fixtures/profiles.py`: profile definitions `minimal`, `dev`, `e2e`, `perf`.
- `backend/src/hub/fixtures/registry.py`: sites, plans, tariffs, customers, meters, assignments, gateways.
- `backend/src/hub/fixtures/staff.py`: staff users, roles, site scopes, TOTP secrets file.
- `backend/src/hub/fixtures/support.py`: tickets, messages, KB articles and versions in two languages.
- `backend/src/hub/fixtures/money.py`: funding wallets through the definer function.
- `backend/src/hub/fixtures/telemetry.py`: readings via `COPY`.
- `backend/src/hub/fixtures/bulk.py`: `COPY` helpers for the perf profile.
- `backend/src/hub/fixtures/clock.py`: `OffsetClock` (reads an offset from Redis key `hub:fixtures:clock_offset_s`).
- `backend/src/hub/fixtures/reset.py`: truncates and restores state.
- `backend/tests/factories/__init__.py` and `backend/tests/factories/*.py` (`customers.py`, `meters.py`, `topups.py`, `tickets.py`, `staff.py`): plain functions returning inserted ids; no factory library.
- `backend/tests/conftest.py` (modify): fixtures `db_template`, `clean_db`, `frozen_clock`, `factories`.
- `backend/tests/unit/fixtures/` : `test_guard.py`, `test_rng.py`, `test_names.py`, `test_profiles.py`, `test_clock.py`; `backend/tests/integration/fixtures/`: `test_load_dev.py`, `test_money.py`, `test_reset.py`, `test_perf_small.py`.
- `backend/src/hub/settings/test.py` (modify): add database alias `fixtures` (role `migrator`, test database only) and `HUB_ENV`.
- `Makefile` (modify): targets `seed`, `reset`, `test-tools-t3`.
- `.gitignore` (modify): `db/seed/local_staff.json`.

## Interfaces

**Consumes:** `HUB_ENV`, `DATABASE_URL_FIXTURES` (migrator role; test database only), `REDIS_URL`, `OBJECT_STORE_ENDPOINT`, `SEED` (optional).

**Produces**

- `python manage.py load_fixtures --profile minimal|dev|e2e|perf [--seed N] [--customers N] [--telemetry-days N] [--language-mix fr=70,en=30] [--out-ids FILE]`: prints a one-line JSON summary `{profile, seed, customers, meters, sites, tickets, kb_articles}`; `--out-ids` writes a JSON map of well-known ids (`staff.support_agent`, `customer.fr_01`, `meter.m_01`, and so on) for Playwright. Exit codes: 0 success, 2 bad arguments, 3 guard refusal, 4 database error.
- `python manage.py fixtures_time set 2026-10-30T08:00:00Z | advance 3600 | clear`: sets or clears the app clock offset for all processes sharing the test Redis.
- `python manage.py fixtures_reset [--keep-schema]`: truncates data tables (not lookup tables seeded by the schema), flushes the test Redis database, empties the test object-store bucket.
- Profile sizes: `minimal` 3 customers; `dev` 200 customers and 5 sites; `e2e` 40 customers with scripted scenarios (one per role flow); `perf` 300,000 customers and meters, 7 days telemetry by default.
- Python: `guard.check(settings, database_name) -> None` (raises `FixtureGuardError`), `rng.Rng(seed: int)`, `registry.create_customer(conn, rng, site_id, language) -> UUID`, `money.fund_wallet(conn, customer_id, amount_minor, currency, clock) -> UUID`, `clock.OffsetClock(base: Clock, redis) -> Clock`.
- Test factories: `factories.customer(conn, **overrides) -> UUID`, `factories.meter(conn, site_id, ...)`, `factories.top_up(conn, customer_id, status=...)`, `factories.ticket(conn, customer_id, language=...)`, `factories.staff(conn, role)`.
- pytest fixtures: `clean_db` (restores the template database for each test module and truncates changed tables for each test), `frozen_clock` (`FrozenClock` with `advance`), `factories`.

## Tasks

### Task T3.1: Guard, rng and command skeleton

- [ ] Step 1, failing tests in `backend/tests/unit/fixtures/test_guard.py` and `test_rng.py`: `test_refuses_non_test_env` (`HUB_ENV=prod` raises `FixtureGuardError`), `test_refuses_database_name_outside_pattern` (names `hub`, `hub_prod`, `hub_test_x_prod` refused; `hub_test`, `hub_ci_w3` accepted), `test_command_exit_code_3_on_refusal` (`call_command` in a subprocess, no connection opened: assert via a spy that `connect` is never called), `test_same_seed_same_sequence`, `test_rng_has_no_global_state` (two instances interleaved give independent sequences).
- [ ] Step 2: `cd backend && python -m pytest tests/unit/fixtures -q`; expect `ModuleNotFoundError: hub.fixtures`.
- [ ] Step 3: implement `guard.check`, `rng.Rng`, and the command skeleton (argument parsing, guard call before any connection). Approach: the guard runs first in `handle`, reads the database name from the `fixtures` alias settings dictionary (never from the environment alone), and raises before `connections["fixtures"]` is touched.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(fixtures): environment guard and command skeleton`.

### Task T3.2: Names, phones and bilingual text

- [ ] Step 1, failing tests in `test_names.py`: `test_phone_in_synthetic_range_and_unique` (10,000 generated, all match `^\+243810[0-9]{6}$`, all distinct), `test_no_phone_outside_synthetic_range` (scans `backend/`, `frontend/`, `db/seed/`, `tools/`, `docs/plan/` for `\+[1-9][0-9]{7,14}` and fails on any match outside the range or on a documented allow-list file `tools/scripts/phone_allowlist.txt` that holds only the OpenAPI example numbers), `test_emails_use_example_test`, `test_templates_cover_both_languages` (every ticket category has at least 3 EN and 3 FR templates), `test_french_has_accents_and_apostrophes`, `test_adversarial_strings_included` (`Robert'); DROP TABLE`, a 10 kB message, zero-width characters, an embedded instruction), `test_code_switching_share_between_8_and_12_percent`.
- [ ] Step 2: run the file; expect import errors.
- [ ] Step 3: implement `names.py` with fictional lists, `phone(rng, index)` (index based so uniqueness is by construction up to 1,000,000), `ticket_text(rng, category, language, code_switch)`, `kb_article(rng, topic, language)`. Categories follow the vocabulary of section 7.2 (`billing_payment`, `no_power`, `meter_fault`, `tariff_question`, `account_change`, `complaint`, `safety_hazard`, `other`). Include hazard words (fire, smoke; `incendie`, `fumée`) in a share of `safety_hazard` tickets.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(fixtures): synthetic bilingual generators`.

### Task T3.3: Registry, staff and support data (dev profile)

- [ ] Step 1, failing tests in `backend/tests/integration/fixtures/test_load_dev.py` (uses the template database): `test_dev_profile_counts` (200 customers, 5 sites, at least 40 tickets, at least 12 KB articles in both languages), `test_every_role_has_a_staff_user`, `test_customers_have_unique_phones_and_valid_language`, `test_each_customer_has_one_active_meter_assignment`, `test_idempotent_second_run_adds_nothing` (second run with the same seed leaves counts unchanged, exit 0), `test_ids_file_written` (keys present), `test_totp_file_gitignored_and_not_in_output` (stdout does not contain a secret from the file).
- [ ] Step 2: `python -m pytest tests/integration/fixtures/test_load_dev.py -q`; expect failures.
- [ ] Step 3: implement `registry.py`, `staff.py`, `support.py` using parameterised SQL through psycopg 3 on the `fixtures` alias, in one transaction per entity group. Insert order follows foreign keys: sites, plans, tariffs, gateways, customers, meters, assignments, staff, roles, `user_sites`, KB articles with versions (published and draft), tickets and messages. Idempotency: natural keys (`external_ref` `seed-<profile>-<n>`) with `ON CONFLICT DO NOTHING`. Staff TOTP secrets are generated from the rng and written to `db/seed/local_staff.json` with mode 0600.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(fixtures): dev profile registry, staff and support data`.

### Task T3.4: Money and scenarios (e2e profile)

- [ ] Step 1, failing tests in `test_money.py`: `test_wallet_funding_uses_definer_function` (a SQL log captured with `pg_stat_statements` or a `log_statement` test hook shows no `INSERT INTO hub.ledger_`; the wallet balance equals the funded amount and every transaction sums to zero), `test_funding_requires_matching_provider_event` (top-up and stored `provider_events` row exist for the posting), `test_e2e_profile_scenarios_present` (named scenarios: `funded_customer`, `pending_top_up`, `expired_top_up_awaiting_late_webhook`, `meter_with_pending_token`, `open_ticket_fr`, `open_ticket_en_safety`, `unreconciled_statement_line`), `test_ledger_invariants_hold_after_load` (zero imbalanced transactions).
- [ ] Step 2: run `python -m pytest tests/integration/fixtures/test_money.py -q`; expect failures.
- [ ] Step 3: implement `money.fund_wallet`: create wallet account through `hub.create_wallet_account`, insert a `top_ups` row with status `confirmed` and a `provider_events` row (event type `payment.succeeded`, status `processed`) with matching `provider_txn_id`, then call `SELECT hub.post_ledger_transaction(...)` with kind `topup_received` and lines (provider clearing debit, wallet credit). Scenario builders in `profiles.py` call small functions per scenario and return their ids for the `--out-ids` file. Sequences that need the application (a token for a confirmed top-up) are not faked; the scenario ends where the application takes over.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(fixtures): money funding through the definer function and e2e scenarios`.

### Task T3.5: Time control

- [ ] Step 1, failing tests in `backend/tests/unit/fixtures/test_clock.py`: `test_offset_clock_adds_redis_offset`, `test_advance_accumulates`, `test_clear_returns_to_system_time`, `test_offset_clock_refused_when_env_prod` (building the container with `env=prod` and the offset clock raises, extending the startup assertion of docs/03 section 5), `test_frozen_clock_fixture_advance`, `test_clock_commands_refused_by_guard`.
- [ ] Step 2: run; expect failures.
- [ ] Step 3: implement `OffsetClock` (`now()` = base now plus integer offset read with a 1-second local cache), `fixtures_time` command, the `frozen_clock` pytest fixture, and the wiring: `hub.settings.test` and `local` select `OffsetClock` through `settings.adapters`; `bootstrap.build_container` already refuses fakes in prod; add `OffsetClock` to that refusal list in the same change. Database-side time that tests must control uses the existing `statement_timestamp` parameters (docs/10 section 6.5); this slice documents them in a comment table in `clock.py`.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(fixtures): cross-process time control for tests`.

### Task T3.6: Reset and factories

- [ ] Step 1, failing tests: `test_reset.py::test_reset_clears_redis_and_object_store` (put a key and an object, reset, both gone), `test_reset_keeps_lookup_tables` (roles, permissions, alert rules seeded by the schema remain), `test_reset_twice_is_safe`; `test_factories.py::test_customer_factory_unique_phone_per_call`, `test_top_up_factory_status_enum_valid`, `test_staff_factory_has_role`, `test_clean_db_isolates_tests` (two tests insert the same external ref; both pass in any order under `pytest-randomly`).
- [ ] Step 2: run; expect failures.
- [ ] Step 3: implement `reset.py` (truncate in dependency order with `TRUNCATE ... RESTART IDENTITY CASCADE` on non-protected tables; protected tables listed in one constant and tested against `information_schema`; ledger and audit tables are not truncated by SQL but are recreated by restoring the template database, because immutability triggers block truncation by design), the factories, and `conftest.py` fixtures. Approach: `clean_db` clones the template per module, and per test it rolls back via savepoint where the code under test does not commit, or restores the template for `transaction=True` tests.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(fixtures): reset command and backend test factories`.

### Task T3.7: Perf profile with COPY

- [ ] Step 1, failing tests in `test_perf_small.py`: `test_perf_profile_scaled_down_100_customers` (`--customers 100 --telemetry-days 2`: 100 meters, 100 x 96 x 2 readings, rows in the right monthly partitions, zero in `meter_readings_default`), `test_perf_uses_copy_not_insert` (spy: `cursor.copy` called, no per-row `execute` above 50 calls), `test_distribution_shares` (offline meters 2 percent plus or minus 1, disputed payments 0.5 percent plus or minus 0.5, malformed telemetry rows absent from table but counted in the summary), `test_perf_deterministic_counts_for_seed`, `test_meter_serials_match_simulator_topology` (serial pattern `SGM-` plus 7 digits, same as slice T2).
- [ ] Step 2: run; expect failures.
- [ ] Step 3: implement `bulk.copy_rows(conn, table, columns, rows_iterable, batch=50000)` and `telemetry.generate(...)` streaming rows with a generator so memory stays flat. Write a `--manifest FILE` option that outputs the topology file for `tools/meter_simulator` (sites, gateways, serial prefixes) so the simulator and the database agree. The full 300,000-customer run is a documented manual step in slice 17, not a unit test.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(fixtures): perf profile with COPY and simulator manifest`.

## Verification

`make verify SLICE=T3` runs:

1. `cd backend && python -m pytest tests/unit/fixtures tests/integration/fixtures tests/factories -q -p no:randomly` then once more without `-p no:randomly`: both exit 0.
2. `make reset && make seed` followed by `python manage.py load_fixtures --profile dev --out-ids /tmp/ids.json | python -c "import json,sys; d=json.load(sys.stdin); assert d['customers']==200 and d['sites']==5"`: exit 0.
3. `HUB_ENV=prod python manage.py load_fixtures --profile dev; test $? -eq 3`: exit 0 (refusal).
4. `python tools/check_traceability.py` and `python -m pytest tests/test_alignment.py -q`: exit 0 (no route added).
5. `python tools/verify_forbidden_patterns.py --paths backend/src/hub/fixtures` (once T4 exists): exit 0.

## Demo script

1. `make reset && make seed`; show the one-line JSON summary.
2. Log in as the seeded `support_agent` in the browser (secret from `db/seed/local_staff.json`), open the ticket list: French and English tickets appear.
3. `python manage.py fixtures_time advance 86400`; reload the dashboard: "revenue today" changes day.
4. `HUB_ENV=prod python manage.py load_fixtures --profile dev`; show the refusal and exit code 3.

## Done criteria

- All tasks committed, `make verify SLICE=T3` green, progress file updated.
- Cases this slice makes runnable or partly covers: TC-NFR-06-7 (guard refusal and no test-only route), TC-NFR-14-1 (one-command environment seeding), TC-FR-16-1 (bilingual data present), TC-NFR-07-1 (synthetic data only), TC-NFR-03-1 (perf data).
- Factories and `clean_db` are in use by slice 2 tests.

## Plan notes for the tech lead

1. docs/10 section 5 (NFR-03) says 90 days of telemetry at 300,000 meters; docs/09 section 7.1 says 7 days (about 200 million rows). Followed docs/09 for the default (`--telemetry-days 7`) and made it an argument; 90 days is about 2.6 billion rows and needs a decision on perf storage.
2. docs/10 section 6.2 asks for "reserved or clearly fake" phone ranges; no document names one. This plan declares `+243810000000` to `+243810999999` a project convention (the OpenAPI examples sit inside it). Real numbers could exist in that block; legal or telecom confirmation is outside this plan.
3. docs/10 section 6.5 requires a controllable `Clock` but not how end-to-end browser tests move time across processes. This plan adds an `OffsetClock` driven by a Redis key and a command; it needs the tech lead's acceptance because it touches the startup assertion in docs/03 section 5.
4. docs/03 lists `db/seed/` as a dev seed directory and docs/09 says the seed file holds test TOTP secrets "recorded in the seed file for local use only". The plan writes them to a gitignored file instead of a committed one, to keep secrets out of the repository (executor guide rule 3).
5. Immutability triggers are `ENABLE ALWAYS` (D-22), so reset cannot truncate ledger or audit tables; reset restores the template database instead.
6. Fixtures run as the `migrator` role through an extra `fixtures` database alias, which D-43 does not list (it names four settings modules plus migrate and test). Added to `hub.settings.test` only.
