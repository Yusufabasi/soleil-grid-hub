# 99. Verification: the verifier's checklist

Audience: the tech lead (or any verifier) checking the finished build against the contract after the implementing agent has worked through slices 00 to 17 and T1 to T4. This file does not repeat any slice's commands: each slice file has a Verification section, and `make verify SLICE=<id>` runs exactly that list.

## 1. How to verify

1. Start from a clean clone: `git clone` the repository, `make up`, wait for healthy (NFR-14: under 10 minutes). Record the time.
2. Read `docs/plan/progress.md`: every task has a status, commit hash, test command and summary line; every slice has the pasted output of `make verify`, the demo result, assumptions and change requests. Missing evidence for a task counts as a failed task (executor guide section 6).
3. Rerun, do not trust: run section 2 (per slice), then section 3 (global gates), then read the code of section 4, then look for the red flags of section 5.
4. Record every result in the sign-off tables of section 7. A row is signed only with a command output or a file path as evidence.
5. Any red flag or any failed global gate stops the sign-off (section 5).
6. Verification runs against the commit tagged `release-candidate`; no edit to the repository is allowed during verification except `progress.md` notes and `docs/audits/` files.

Environment notes: Docker is required (schema tests, migration equivalence, compose). The scratch PostgreSQL 16 for the alignment tools is given by `HUB_ALIGN_DATABASE_URL`. `make verify` exit codes: 0 pass, 1 failure, 2 unknown slice or bad registry, 4 timeout; `--allow-skip-docker` must not be used by the verifier.

## 2. Per-slice verification

For each slice the verifier runs `make verify SLICE=<id>`, runs the slice's Demo script, and confirms the test case ids in its Done criteria exist in `docs/10-test-strategy.md` section 8 and have passing tests (marker `@pytest.mark.tc("TC-...")` or Playwright annotation). Pass criteria are in each slice's own Verification section; the generic criterion is: every command exits 0 and meets the stated threshold, the Done criteria are met, no frozen file changed.

| Slice | Where the commands and pass criteria are |
|---|---|
| 00 walking skeleton | the Verification section of the slice 00 file (`docs/plan/slice-00-*.md`) |
| 01 identity, RBAC and approvals | slice 01 file, Verification section |
| 02 registry | slice 02 file, Verification section |
| 03 ledger core | slice 03 file, Verification section |
| 04 top-up and webhook | slice 04 file, Verification section |
| 05 outbox, relay, worker | slice 05 file, Verification section |
| 06 token issuance | slice 06 file, Verification section |
| 07 notifications | slice 07 file, Verification section |
| 08 telemetry and alerts | slice 08 file, Verification section |
| 09 operations dashboard | slice 09 file, Verification section |
| 10 support desk | slice 10 file, Verification section |
| 11 AI gateway and triage | slice 11 file, Verification section |
| 12 reply drafting | slice 12 file, Verification section |
| 13 ask the data | slice 13 file, Verification section |
| 14 reconciliation | slice 14 file, Verification section |
| 15 integrator and gateway access | slice 15 file, Verification section |
| 16 privacy and lifecycle | slice 16 file, Verification section |
| 17 hardening | [slice-17-hardening.md](slice-17-hardening.md#verification) |
| T1 provider stub | [slice-T1-provider-stub.md](slice-T1-provider-stub.md#verification) |
| T2 meter simulator | [slice-T2-meter-simulator.md](slice-T2-meter-simulator.md#verification) |
| T3 fixtures and seed | [slice-T3-fixtures-and-seed.md](slice-T3-fixtures-and-seed.md#verification) |
| T4 verification tooling | [slice-T4-verification-tooling.md](slice-T4-verification-tooling.md#verification) |

Slices 00 to 16 are named `docs/plan/slice-NN-<kebab-name>.md`; the verifier opens the one whose number matches. Run order: T4 first (it provides the tooling), then 00 to 16 in numeric order with T1 before 04, T2 before 06, T3 before 02, then 17.

## 3. Global gates

Run after all slices pass. Every gate must exit 0; the pass criterion is stated per gate. Run `make verify-global` first (it runs the commands marked global in `tools/verify/slices.yaml`), then the individual commands to see the evidence.

| Gate | Command (repository root unless stated) | Pass criterion |
|---|---|---|
| Contract tests | `python -m pytest tests/test_alignment.py tests/test_schema_in_postgres.py -q` and `python tools/check_traceability.py` | all pass; traceability prints no ERROR; frozen files unchanged (`git diff --stat release-baseline -- docs/00-shared-context.md api db/schema.sql db/invariant_tests.sql docs/adr` is empty) |
| Django alignment | `cd backend && python -m pytest ../tests/test_django_alignment.py -q` | models equal the database (TC-NFR-14-6), baseline plus numbered SQL equal `db/schema.sql` (TC-NFR-14-7) |
| Migration equivalence | `python tools/verify/migration_equivalence.py` | exit 0, catalog snapshots identical including grants, triggers and role settings |
| Schemathesis | `python tools/verify/run_schemathesis.py --profile nightly` | exit 0; zero 5xx, zero schema violations; the only excluded operation is `streamDashboard` |
| Authorization matrix | `cd backend && python -m pytest tests/security -q` | zero failures and zero skips; cells equal operations times 9 principals (TC-FR-15-1, TC-FR-15-2, TC-FR-15-18) |
| import-linter | `cd backend && lint-imports` | all contracts kept, no ignored imports |
| Django configuration | `python tools/check_django_config.py` | no admin, `ATOMIC_REQUESTS` false, no managed model, no forbidden middleware; `manage.py check --deploy --fail-level WARNING` clean for each module (TC-NFR-06-9, TC-NFR-06-10) |
| Forbidden patterns | `python tools/verify_forbidden_patterns.py --self-test && python tools/verify_forbidden_patterns.py` | exit 0 both; `tools/verify/allowlist.yaml` is empty or approved in `progress.md` |
| Lint and types | `make lint` | clean (ruff, mypy strict on `ledger`, `payments`, `identity`, `ai_gateway`; ESLint; `tsc`) |
| Test suite and coverage | `make test` | green; coverage at least 95 percent on `ledger`, `payments`, `identity` policy, `ai_gateway` guards, SQL guard; 85 percent other modules; 80 percent overall; frontend 80 percent and 100 percent of permission helpers |
| Mutation (spot) | `cd backend && mutmut run --paths-to-mutate src/hub/ledger,src/hub/payments` | score at least 90 percent on ledger, 85 percent on payments |
| Traceability of TC ids | `python tools/check_traceability.py` | every FR and NFR has cases, each FR has a NEG case, every referenced `TC-` id exists |
| Frontend build | `cd frontend && npm run build && node ../tools/check_bundle_budget.mjs` | portal first load at most 130 KB gzipped, staff at most 250 KB; `serverActions` disabled, `images.unoptimized` true, `/_next/image` not served (TC-NFR-02-4, TC-NFR-06-12, TC-NFR-06-13) |
| Load-test results | `python tools/loadtest/run.py --scenario s6 --profile full --compare baseline` and the stored JSON for S1 to S8 | every threshold met; report says `profile=full`; ledger postings equal distinct valid events; CPU limits held (TC-NFR-02-1 to TC-NFR-02-3, TC-NFR-03-1, TC-NFR-03-2) |
| Accessibility results | `cd frontend && npx playwright test e2e/a11y` and `docs/audits/a11y-*.md` | zero serious or critical axe violations in EN and FR; keyboard scripts pass; manual screen reader log signed (TC-NFR-10-1 to TC-NFR-10-3) |
| Alerts and runbooks | `promtool test rules infra/observability/tests/*.yml`, `python tools/scripts/check_alert_rules.py`, `python -m pytest tests/test_runbooks.py -q` | all pass; RB-01 to RB-20 complete |
| Chaos and restore evidence | `docs/audits/chaos-*.md`, `docs/audits/restore-*.md` | C1, C2, C4, C7 pass; restore total within RTO; erased customer still erased; audit verifier clean (TC-NFR-04-1, TC-NFR-04-2, TC-FR-20-6) |
| Security scans | CI jobs: gitleaks, pip-audit, `npm audit --omit=dev`, semgrep, trivy on both images, ZAP baseline | no high or critical open (TC-NFR-06-1) |
| Clean clone | `make down && make reset && make up` timed | healthy under 10 minutes; `make smoke` green (TC-NFR-14-1) |

## 4. Spot-check list (the verifier reads the code)

For each item open the named place, read it, and write one line in the sign-off notes. Paths follow `docs/03-code-architecture.md` section 1.

1. **Ledger writes only through the definer function.** In `backend/src/hub/ledger/infrastructure/`, every write is `SELECT hub.post_ledger_transaction(...)` or `hub.reverse_ledger_transaction(...)`; no `INSERT`, `UPDATE`, `DELETE` and no model `.save()` on ledger or wallet models. Confirm with `grep -rn "ledger_entries\|ledger_transactions\|wallet_balances" backend/src --include=*.py` that only reads and the definer call appear. In `db/schema.sql` the application roles have no INSERT on those tables.
2. **The webhook transaction boundary.** In `backend/src/hub/payments/` find the confirmation use case and the ingest view: signature verification happens before the transaction opens; one `transaction.atomic` encloses recording `provider_events`, the ledger posting, the outbox row and the top-up update; the acknowledgement is returned after commit; an invalid signature stores nothing in `provider_events` (quarantine only); there is no `ATOMIC_REQUESTS`; the pull verification (status call) is outside the transaction; no Redis result decides the outcome (D-09).
3. **The SQL guard.** In `backend/src/hub/analytics/` read the validator: parses with a real SQL parser, allows a single `SELECT` only, allows only the views of the caller's `analytics_*` schema, rejects multiple statements, functions on a deny list, `pg_*` and `information_schema` access, comments that hide statements, and enforces the row cap; confirm the executor connects with an analytics login role, not `hub_api`, and that the 3 s, 5 s and 8 s limits are settings read in one place. Confirm result rows are never passed to the model (D-31).
4. **The AI gateway insert-only role.** In `backend/src/hub/settings/` find the database alias used for `ai_runs` (D-50): its role can only `INSERT` into `ai_runs` (check the grants in `db/schema.sql`); no other module imports `anthropic` (import-linter contract); suggestions are written by module services after validation, never by the gateway; `tool_choice` and assistant prefill do not exist on the request type.
5. **Maker-checker enforcement.** In `backend/src/hub/identity/` read `ApprovalService`: approver must differ from the maker by user and by session; the approval stores the hash of the exact payload and the executor re-computes it; step-up age is checked within 5 minutes; every action of D-15 goes through the registry and none executes directly; the database function `post_ledger_transaction` demands a consumed approval for manual kinds (second line).
6. **The permission class generated from `api/permissions.yaml`.** In `backend/src/hub/shared/` (DRF helpers) the class loads the YAML at import, keyed by operation id; there is no hand-written role list in views; a view without an operation id is denied; the matrix test (section 3) is green and has no skips.
7. **Token issuance.** In `backend/src/hub/meters/`: re-check of meter ownership and status inside the issuing transaction with locks in the fixed order top-up then meter; token has a posting id, a monotonic counter and expiry; a resend reuses the token id; the push happens outside any transaction.
8. **Outbox.** Business change and outbox row are written in the same transaction in every use case that emits events; consumers write `consumer_inbox` to dedupe.
9. **Sessions and CSRF.** Refresh cookie attributes `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`; only refresh and logout read the cookie and both check `Origin` and `X-Hub-CSRF` (D-46); no Django CSRF or session middleware.
10. **Audit.** All audit writes call `hub.audit_append`; the anchor signature key is read through the secrets port, not from settings; the anchor copy goes to the locked bucket.
11. **Frontend.** No `"use server"`, no `dangerouslySetInnerHTML`, CSP nonce built in middleware, no secret in any `NEXT_PUBLIC_*`, the access token is kept in memory only.
12. **Test doubles.** `tools/provider_stub` signs exact sent bytes; `tools/meter_simulator` enforces counter semantics; neither is imported by `backend/src`; fixtures command refuses non-test databases and no route exposes it (D-38).
13. **Privacy.** Log calls never include personal data; redaction runs before every model call; erasure paths use `hub.erase_customer`, not ad hoc updates.

## 5. Red flags: automatic failure

Any one of these fails the whole verification until fixed and re-verified.

1. A frozen file changed without an approved change request (`docs/plan/change-requests/` entry accepted by the tech lead).
2. `django.contrib.admin` in any settings module or URLConf; `ATOMIC_REQUESTS` true; a managed model for a hub table.
3. Any application code path writing ledger, wallet or audit tables other than through the definer functions.
4. A test deleted, skipped, marked expected-fail, loosened, or a threshold changed, without a change request. Check `git log -p -- '*test*' tools/loadtest` for removed assertions and `grep -rn "skip\|xfail\|quarantine" backend/tests tests tools` for new markers on safety tests (ledger, idempotency, authorization, SQL guard are never quarantined).
5. A test-only route, a route outside `api/openapi.yaml`, or a role outside `api/permissions.yaml`.
6. Secrets, keys or `.env` files committed (`gitleaks detect` finds anything), or secrets in logs, images or `NEXT_PUBLIC_*`.
7. A real-looking phone number or email in fixtures, tests or documents outside the synthetic range of slice T3.
8. Any AI output reaching a customer without a staff approval; any path where model output triggers a write; query rows in a model request.
9. A load result marked `profile=scaled` or `smoke` offered as the pass for NFR-02 or NFR-03; a restore drill without erasure replay and audit verification.
10. A double (`provider_stub`, `meter_simulator`) whose schemas differ from `api/openapi.yaml` (`sync_*_schemas.py --check` fails).
11. Missing `progress.md` evidence for a task, or `--allow-skip-docker` used.
12. A payment path double effect: ledger postings differ from distinct valid provider events after S1, or any imbalanced posting.
13. Placeholder markers left in code or in documents delivered by the plan.

## 6. Open items and their defaults

The verifier checks that each default is implemented as a setting (not a constant) and that the item is still listed in `progress.md`. A changed answer from the business is applied by editing the setting only.

| Item | Source | Default to use | Check |
|---|---|---|---|
| Confirmation pull threshold | D-09 | every confirmation of 100 USD or more is verified by a status pull | setting exists; test reads it from configuration (TC-FR-02-6) |
| Revenue recognition at token issuance | D-10 (finance lead) | recognised at issuance | documented in `docs/plan/progress.md`; posting kinds match |
| Retention table | D-28 (legal sign-off) | the table of D-28 as written | retention values are settings in one place |
| AI notice wording in portal terms | D-35 (legal) | neutral English and French sentence marked "pending legal wording" in the message catalogue | key exists in both locales |
| Synthetic probe wallet in production | D-39 (finance) | probe runs in staging only; production probe disabled | production configuration has the probe off |
| Bundle budgets | D-44 | 130 KB portal, 250 KB staff, confirmed by the early spike | budget check in CI uses these values |
| Accounting period approver attribute | D-40 (`finance_lead`) | users with the attribute only | approval test passes |

## 7. Sign-off tables

Result values: PASS, FAIL, WAIVED (waiver file path required). Verifier, date and evidence (command output or file path) are mandatory on every row.

### 7.1 By slice

| Slice | `make verify` | Demo run | Done criteria TC ids passing | Result | Verifier | Date | Evidence |
|---|---|---|---|---|---|---|---|
| 00 | | | | | | | |
| 01 | | | | | | | |
| 02 | | | | | | | |
| 03 | | | | | | | |
| 04 | | | | | | | |
| 05 | | | | | | | |
| 06 | | | | | | | |
| 07 | | | | | | | |
| 08 | | | | | | | |
| 09 | | | | | | | |
| 10 | | | | | | | |
| 11 | | | | | | | |
| 12 | | | | | | | |
| 13 | | | | | | | |
| 14 | | | | | | | |
| 15 | | | | | | | |
| 16 | | | | | | | |
| 17 | | | | | | | |
| T1 | | | | | | | |
| T2 | | | | | | | |
| T3 | | | | | | | |
| T4 | | | | | | | |

### 7.2 By non-functional requirement

| NFR | What proves it | Test cases | Result | Verifier | Date | Evidence |
|---|---|---|---|---|---|---|
| NFR-01 | probe deployed, rolling deploy and replica kill drills, burn-rate rule tests, 30-day soak report (manual) | TC-NFR-01-1, TC-NFR-01-2, TC-NFR-01-3 | | | | |
| NFR-02 | S1, S3, S4, S5, S8 results at `profile=full`; bundle budget | TC-NFR-02-1, TC-NFR-02-2, TC-NFR-02-3, TC-NFR-02-4 | | | | |
| NFR-03 | S2 and S6 at full profile with the 300,000-meter seed | TC-NFR-03-1, TC-NFR-03-2 | | | | |
| NFR-04 | restore drill and primary-kill drill | TC-NFR-04-1, TC-NFR-04-2 | | | | |
| NFR-05 | hypothesis and stateful tests, nightly invariant job, post-load SQL checks | TC-NFR-05-1, TC-NFR-05-2 | | | | |
| NFR-06 | alignment, hardening checks, scans, matrix, CSP, Next.js configuration | TC-NFR-06-1 to TC-NFR-06-13 | | | | |
| NFR-07 | redaction tests, erasure across stores | TC-NFR-07-1 to TC-NFR-07-3 | | | | |
| NFR-08 | AI audit completeness, egress block, fallback rules | TC-NFR-08-1 to TC-NFR-08-5 | | | | |
| NFR-09 | trace, log and metric propagation, AI cost metrics, SLO alert drill | TC-NFR-09-1, TC-NFR-09-2, TC-NFR-09-3 | | | | |
| NFR-10 | axe on all routes, keyboard and zoom scripts, manual screen reader log | TC-NFR-10-1, TC-NFR-10-2, TC-NFR-10-3 | | | | |
| NFR-11 | formatters, pseudo-locale, locale-aware dates | TC-NFR-11-1, TC-NFR-11-2, TC-NFR-11-3 | | | | |
| NFR-12 | two-target deploy smoke, no cloud SDK outside adapters | TC-NFR-12-1, TC-NFR-12-2 | | | | |
| NFR-13 | budget tests and token accounting in load | TC-NFR-13-1 to TC-NFR-13-4 | | | | |
| NFR-14 | clean clone timing, runbook drill by another person, dependency PR, alignment gates | TC-NFR-14-1 to TC-NFR-14-7 | | | | |

### 7.3 Final decision

| Question | Answer |
|---|---|
| All 22 slice rows PASS | |
| All 14 NFR rows PASS or WAIVED with a waiver file | |
| Every global gate in section 3 exits 0 | |
| No red flag of section 5 found | |
| Release criteria of `docs/10-test-strategy.md` section 7.4 met (no open S1 or S2, three green nightlies, restore drill within 30 days, UAT sign-off) | |
| Decision (ACCEPT or REJECT) and signature | |
