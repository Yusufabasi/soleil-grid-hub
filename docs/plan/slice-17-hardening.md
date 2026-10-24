# Slice 17: Hardening

- **Goal:** prove and harden the finished system: load tests at 10x payday and 5x telemetry (k6 scenarios S1 to S8 of docs/09), failover and restore drills, SLO alerts with burn-rate rules, the runbooks RB-01 to RB-20, the audit-chain verifier and anchor jobs, the accessibility audit, dependency automation, security review fixes, and the disaster-recovery restore with erasure-log replay.
- **Depends on:** slices 0 to 16 and T1 to T4 (T1 provider stub, T2 meter simulator, T3 fixtures with the perf profile, T4 verification tooling).
- **Requirements served:** NFR-01, NFR-02, NFR-03, NFR-04, NFR-06, NFR-09, NFR-10, NFR-14; supports NFR-05, FR-15 (audit chain), FR-20 (erasure replay).
- **Size:** L, 10 days (two sessions of five).
- **Companion files:** [slice-T1-provider-stub.md](slice-T1-provider-stub.md), [slice-T2-meter-simulator.md](slice-T2-meter-simulator.md), [slice-T3-fixtures-and-seed.md](slice-T3-fixtures-and-seed.md), [slice-T4-verification-tooling.md](slice-T4-verification-tooling.md), [99-verification.md](99-verification.md).
- **Spikes or open items:** synthetic probe wallet in production **[open: finance accepts a synthetic wallet; default: probe runs in staging only]**; confirmation-pull threshold **[open, default 100 USD]** stays a setting; legal sign-off of retention **[open]** does not change this slice; bundle budgets **[open]** until measured by S8; the full-size performance environment needs cloud capacity (default if none: run the scaled profile and record the run as scaled, never as passed).

## Read first

- `docs/09-devops-sre.md#6. SLOs, alerting and on-call` (6.1 to 6.6)
- `docs/09-devops-sre.md#7. Capacity and load testing` (7.1 to 7.4)
- `docs/09-devops-sre.md#8. Resilience` (8.1 to 8.4)
- `docs/09-devops-sre.md#9.1 Runbook index`
- `docs/09-devops-sre.md#2.4 Automated dependency and security updates (NFR-14)`
- `docs/10-test-strategy.md#5. Non-functional testing mapped to NFR-01..NFR-14` and `#5.1 Chaos programme`
- `docs/10-test-strategy.md#7.4 Defect severity and release criteria`
- `docs/08-frontend-ux.md#7. Accessibility plan to WCAG 2.2 AA (NFR-10)`
- `docs/07-security-threat-model.md#8.1 OWASP ASVS (v4.0.3) Level 2 chapters` and the testing table containing "Penetration test"
- `docs/04-data-model.md` the audit log paragraph (hash chain, `verify_audit_chain`, `audit_anchor`) and the erasure paragraph (`replay_erasure_log`)
- `docs/00-shared-context.md#7.5 Data, infrastructure and operations` (D-22, D-26, D-29, D-30) and `#7.6 AI` (D-39)
- `docs/plan/slice-T3-fixtures-and-seed.md#Interfaces` (perf profile) and `docs/plan/slice-T2-meter-simulator.md#Interfaces` (storm)

## Global constraints

- NFR-02 p95: webhook ack under 300 ms; read API under 200 ms; cached dashboard under 500 ms; ask-the-data under 8 s.
- Load shape: payday is 10 payments per second; **10x payday = 100 webhook calls per second**; telemetry average 83 batches per second, **5x = 417 batches per second** (about 1,700 readings per second).
- Pass criteria (docs/09 7.3): every threshold holds, invariant checks clean, no sev1-equivalent alert on the system under test, CPU under 70 percent on app tiers and under 65 percent on the Postgres primary; a p95 or throughput regression above 10 percent against the stored baseline blocks.
- After S1: ledger postings equal distinct valid event ids (zero tolerance) and `ledger_unbalanced_total` is 0.
- SLO-1 is 99.9 percent over 30 days (43.2 minutes); SLO-2 is 95 percent of acks within 300 ms; probe every 30 seconds.
- Burn-rate rules: SLO-1 budget 0.001 with factors 14.4 (1h and 5m, sev1, for 2m), 6 (6h and 30m, sev2, for 5m), 3 (1d and 2h, sev3), 1 (3d and 6h, sev3); SLO-2 budget 0.05 with factors 6 (1h and 5m, sev2) and 2 (6h and 30m, sev3); `LedgerEffectStale` p95 above 5 s for 10 m; `LedgerUnbalanced` any increase, sev1.
- Every alert carries a `runbook_url`; a CI rule check fails a rule without one.
- RPO at most 5 minutes (0 for committed ledger transactions with the synchronous standby); RTO at most 30 minutes for instance and zone failure and 2 hours for logical corruption or region loss.
- After any restore: re-import the off-database erasure log copy, run `hub.replay_erasure_log()`, then run the audit-chain verifier, before serving (D-29).
- Audit chain verifier runs hourly; anchor signed daily; `AuditChainBroken` sev1, `AuditChainVerifyStale` after 2 hours, `AuditAnchorMissing` after 26 hours.
- Accessibility: zero axe serious or critical violations on every route in EN and FR, light and dark; keyboard-only completion at 200 percent zoom.
- Dependency updates: weekly grouped PRs; CRITICAL fixable patched within 48 hours, HIGH 7 days, MEDIUM 30 days; majors never auto-merge.
- No test-only HTTP endpoint is added for any drill (D-38); drills use fixtures, scripts, Toxiproxy and container controls.
- No personal data in drill artefacts; the restore environment is destroyed afterwards.

## Review focus

1. A load test passes only because the generator is the bottleneck or requests are rejected early (401, 429), so latency looks good. Pinned by the `checks` thresholds `acked 200` above 0.999 and `test_k6_thresholds_include_expected_status` in Task 17.1.
2. Duplicate and bad-signature requests are counted in the p95 or in the ledger check, so the ledger equality check is wrong in either direction. Pinned by `post_checks.sql` assertions in Task 17.1.
3. Burn-rate rules are syntactically valid but never fire (wrong label or route name). Pinned by the promtool unit tests `test_fast_burn_fires` and `test_probe_floor_prevents_silent_night` in Task 17.4.
4. The restore drill restores the data but the erased customers come back, or the audit chain verifier is skipped. Pinned by `restore_drill.sh` step checks and `test_erased_customer_stays_erased_after_restore` in Task 17.7.
5. An alert has a runbook URL that points to a missing or empty runbook. Pinned by `tests/test_runbooks.py::test_every_alert_runbook_exists_and_is_complete` in Task 17.8.

## Files

- `tools/loadtest/lib/provider.js`, `lib/gateway.js`, `lib/auth.js`, `lib/checks.js`, `lib/results.js`: signing helper, keys, tokens, shared checks, JSON result writer.
- `tools/loadtest/s1_webhooks.js`, `s2_telemetry.js`, `s3_dashboard.js`, `s4_read_api.js`, `s5_ask_data.js`, `s6_payday.js`, `s7_soak.js`, `s8_web.js`: scenarios.
- `tools/loadtest/post_checks.sql`: post-run ledger and invariant assertions.
- `tools/loadtest/run.py`: runs a scenario, collects results, compares with `tools/loadtest/baselines/*.json`.
- `tools/loadtest/baselines/`: stored result JSON (created by the first accepted run).
- `tools/loadtest/tests/test_run.py`, `test_thresholds.py`: tool tests.
- `infra/observability/rules/slo-payment.yml`, `slo-infra.yml`, `slo-audit.yml`: recording and alert rules.
- `infra/observability/tests/slo-payment.test.yml`, `slo-infra.test.yml`, `slo-audit.test.yml`: promtool unit tests.
- `infra/observability/dashboards/slo.json`, `load.json`: dashboards as code.
- `tools/scripts/check_alert_rules.py`: every alert has `runbook_url` and severity.
- `backend/src/hub/identity/application/audit_anchor.py` and `backend/src/hub/identity/infrastructure/anchor_signer.py` (modify or create if slice 1 did not): signing and storing the anchor; `backend/src/hub/shared/management/commands/run_scheduler.py` (modify only to register the jobs).
- `backend/src/hub/admin/application/erasure_log_export.py`: off-database copy of `erasure_log`.
- `backend/tests/integration/hardening/test_audit_anchor.py`, `test_erasure_log_export.py`.
- `infra/chaos/toxiproxy.json`, `infra/chaos/c1_replica_kill.sh` to `c7_poison_message.sh`, `infra/chaos/run.py`: drills.
- `infra/dr/restore_drill.sh`, `infra/dr/verify_integrity.sql`: restore and integrity scripts; the steps live in RB-09.
- `infra/probe/probe_compose.yaml` (staging probe from the T1 stub).
- `docs/runbooks/RB-01-payment-not-credited.md` to `RB-20-bad-frontend-release.md` (twenty files, names as in the docs/09 index) and `docs/runbooks/_template.md`.
- `tests/test_runbooks.py`: runbook completeness and alert linkage.
- `frontend/e2e/a11y/routes.spec.ts`, `frontend/e2e/a11y/keyboard.spec.ts`, `frontend/e2e/a11y/baseline.json`, `docs/audits/a11y-<date>.md` (results log, written during the task).
- `renovate.json`, `.github/workflows/security.yml` (or the CI equivalent already used), `docs/security/asvs-checklist.md`, `docs/security/review-fixes.md`.

## Interfaces

**Consumes:** the staging or perf stack (`BASE`, `WEBHOOK_SECRET_*`, `METER_GW_KEYS_FILE`, `STAFF_TOKENS_FILE`), `HUB_ALIGN_DATABASE_URL`-style scratch servers for restores, Prometheus, Toxiproxy, the T1 stub and T2 simulator control APIs, fixtures perf profile.

**Produces**

- `python tools/loadtest/run.py --scenario s1|s2|s3|s4|s5|s6|s7|s8 --profile smoke|scaled|full [--compare baseline]`: writes `build/loadtest/<scenario>-<timestamp>.json` with thresholds, p95 values, invariant results; exit 0 pass, 1 threshold or invariant failure, 2 usage, 5 regression above 10 percent.
- `make load-smoke` (10 percent, 5 minutes), `make load-full SCENARIO=s6`.
- `python infra/chaos/run.py --experiment c1..c7 [--staging]`: exit 0 when acceptance holds.
- `bash infra/dr/restore_drill.sh --target-time ISO --env drill`: prints timings (`t_first_connection`, `t_consistent`, `t_total`), exit 0 only when RTO and checks pass.
- Alerts and recording rules named in docs/09 section 6.3.
- Runbooks with the ten-heading template: Symptoms and alerts, Impact, Severity, First five minutes, Diagnosis, Mitigation, Verification, Escalation, Prevention, Last drilled.

## Tasks

### Task 17.1: k6 library, S1 webhook burst and ledger post-checks

- [ ] Step 1, failing tests in `tools/loadtest/tests/test_thresholds.py`: `test_k6_thresholds_include_expected_status` (`k6 inspect s1_webhooks.js` JSON has `http_req_duration{expected:true}` `p(95)<300`, `http_req_failed` `rate<0.001`, `checks` `rate>0.999`), `test_signature_helper_matches_contract` (the JS helper output equals Python `hmac` over `ts.event_id.body` for a vector), `test_headers_are_openapi_names` (`X-Webhook-Id`, `X-Webhook-Timestamp`, `X-Webhook-Signature`), `test_mix_ratios` (30 percent duplicates, 5 percent bad signature, 2 percent outside window, by a seeded dry run), plus a SQL test `test_post_checks_fail_on_extra_posting` (insert one extra posting in a scratch database: `post_checks.sql` raises).
- [ ] Step 2: `python -m pytest tools/loadtest/tests -q`; expect failure for missing files.
- [ ] Step 3: implement `lib/provider.js` (signing with k6 `crypto.hmac`), `s1_webhooks.js` (ramping-arrival-rate: 30 per second for 10 min, 100 per second for 5 min, then 500 requests in 5 seconds; tags `expected:true` only on valid first deliveries; duplicates and invalid requests are tagged `expected:false` and excluded from the latency threshold but checked for status 200 `duplicate`, 401, 400), `post_checks.sql` (count of distinct valid `provider_events` event ids equals count of `topup_received` postings; every transaction sums to zero; `ledger_unbalanced_total` unchanged), and `run.py`. Webhook events reference fixtures top-ups created by `load_fixtures --profile perf`.
- [ ] Step 4: rerun until pass; run `make load-smoke SCENARIO=s1` on the compose stack: exit 0.
- [ ] Step 5: commit `test(perf): S1 webhook burst with ledger post-checks`.

### Task 17.2: S2 telemetry storm and S3 dashboard

- [ ] Step 1, failing tests: `test_thresholds.py::test_s2_stages` (83 per second 20 min, 417 per second 10 min, storm of 100,000 meters in 120 s with 24 h backlog; thresholds p95 under 300 ms steady, under 1 s at 5x, 5xx under 0.5 percent), `test_s2_runs_concurrent_s1` (payment p95 under 300 ms while S2 runs, taken from the composite config), `test_s3_cold_and_warm_phases` (cold: Redis flushed, first 30 s p95 under 1.5 s then under 500 ms; warm p95 under 500 ms, hit ratio above 90 percent), `test_s3_origin_single_flight` (origin queries for the summary key at most 1 per TTL window, read from a Prometheus query in the result file).
- [ ] Step 2: run; expect missing scenario files.
- [ ] Step 3: implement `s2_telemetry.js` (k6 constant arrival rate using bodies shaped like OpenAPI `TelemetryBatch`, gateway keys from file; the herd phase is driven by `python -m meter_simulator storm` per T2, started by `run.py`) and `s3_dashboard.js` (200 virtual users, mix of summary, site detail, alerts, 20 percent SSE streams through the stream-ticket flow). `run.py` records `telemetry_ingest_lag_seconds` returning under 60 s within 10 minutes after the herd.
- [ ] Step 4: rerun until pass; smoke profile of both: exit 0.
- [ ] Step 5: commit `test(perf): S2 telemetry storm and S3 dashboard`.

### Task 17.3: S4, S5, S8, composite S6, soak S7 and baselines

- [ ] Step 1, failing tests in `test_thresholds.py`: `test_s4_400_rps_thresholds`, `test_s5_hostile_questions_all_rejected` (the scenario's hostile list has at least 20 statements and the check asserts 100 percent rejection), `test_s8_first_load_budget_threshold` (`portal_first_load_js_bytes p(95)<133120`), `test_s6_contains_all_scenarios_and_60_minutes`, `test_s7_is_8_hours_at_50_percent`, `test_regression_over_10_percent_exit_5` (`run.py --compare` with a baseline 20 percent faster exits 5), `test_result_json_schema`.
- [ ] Step 2: run; expect failures.
- [ ] Step 3: implement the five scenario files and the baseline comparison. S5 uses the real model stub of the AI gateway (latency 2 to 6 s, 3 percent timeouts); one capped real-API run of 200 calls is a separate manual command `run.py --scenario s5 --real-model --max-calls 200`. S6 composes S1 at 30 to 100 per second, S4 at 400, S3 with 200 users, S8 at 20 to 60 loads per second, S2 at 83 to 417 batches per second, S5 at 10 users, 5 tickets per minute with triage, for 60 minutes. Baselines are written only by `run.py --accept` after a passing full run.
- [ ] Step 4: rerun until pass; run smoke of S4, S5, S8: exit 0.
- [ ] Step 5: commit `test(perf): S4 S5 S8 composite S6 soak S7 and baseline comparison`.

### Task 17.4: SLO rules, burn-rate alerts and promtool tests

- [ ] Step 1, failing tests: `infra/observability/tests/slo-payment.test.yml` cases `test_fast_burn_fires` (error ratio 2 percent for 1 h: `PaymentPathBudgetBurnFast` fires at 62 minutes), `test_medium_burn_fires`, `test_no_fire_at_budget_burn_below_factor`, `test_probe_floor_prevents_silent_night` (synthetic probe series at 0.033 per second keeps the ratio defined at zero traffic), `test_ledger_unbalanced_fires_on_any_increase`, `test_latency_burn_fast_fires_at_30_percent_slow`; `slo-infra.test.yml` for `OutboxStuck`, `ReplicaLagHigh`, `BackupMissing`, `TelemetryLag`; `slo-audit.test.yml` for the three audit alerts; Python `tools/scripts/check_alert_rules.py` tests `test_rule_without_runbook_url_fails`.
- [ ] Step 2: `promtool test rules infra/observability/tests/*.yml`; expect failure (missing rules).
- [ ] Step 3: write the rule files from docs/09 section 6.3 with the metric and route names the application actually exports (look them up in the code; if a metric is missing, add it in this task with a test), including the supporting alerts list of 6.3 and the alert-to-runbook mapping of 6.6. Add `SyncStandbyLost` (sev2) and `ErasureReplayPending` (sev2, RB-09).
- [ ] Step 4: rerun until promtool reports `SUCCESS`; `python tools/scripts/check_alert_rules.py` exit 0.
- [ ] Step 5: commit `feat(observability): SLO rules, burn-rate alerts and rule tests`.

### Task 17.5: Audit chain verifier and anchor jobs

- [ ] Step 1, failing tests in `backend/tests/integration/hardening/test_audit_anchor.py` (scratch database): `test_verifier_reports_first_bad_seq_for_altered_row`, `test_verifier_detects_deleted_and_reordered_rows`, `test_head_older_than_anchor_is_failure`, `test_anchor_job_writes_signed_head_once_per_day` (second run the same day writes nothing, uses `FrozenClock`), `test_anchor_signature_verifies_with_public_key`, `test_anchor_copy_written_to_object_store_with_lock_flag` (fake store records `object_lock=True`), `test_failure_increments_audit_chain_verify_failures_total`.
- [ ] Step 2: run; expect failures where slice 1 left gaps (first check which of verifier, anchor signer and metrics already exist; build only what is missing).
- [ ] Step 3: implement `audit_anchor.run(stream, clock, signer, store)`: calls `hub.audit_anchor(stream, signature)` after signing the head hash with the Ed25519 key from the secrets manager port, stores a copy in the locked bucket and a second account bucket; verifier job runs hourly and exports the metrics of docs/09 6.3. Register both with the scheduler (advisory-locked singleton).
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(audit): signed daily anchor, hourly verifier metrics and tamper tests`.

### Task 17.6: Failover and chaos drills

- [ ] Step 1, failing tests in `infra/chaos/tests/test_run.py` (fake docker and fake Prometheus): `test_each_experiment_has_hypothesis_abort_and_acceptance`, `test_abort_condition_stops_experiment`, `test_c2_acceptance_ack_p95_under_400ms_and_zero_double_effect`, `test_c4_acceptance_recovery_under_10_minutes_abort`, `test_c7_dlq_exactly_one_entry`.
- [ ] Step 2: run `python -m pytest infra/chaos/tests -q`; expect failures.
- [ ] Step 3: implement `run.py` and the seven scripts exactly per docs/09 section 8.1 (C1 kill an api replica; C2 stop Redis 10 min; C3 pause replica replay 3 min; C4 promote the synchronous standby while S1 runs at 30 per second; C5 block the Claude host 30 min; C6 stub delay 20 s and 30 percent duplicates; C7 poison message into `token-issuance` and `ledger-posting`). Each ends with `post_checks.sql`. Deploy the probe (`infra/probe/probe_compose.yaml`, T1 stub in `probe` mode) in staging from two locations.
- [ ] Step 4: rerun unit tests; then run C1, C2, C4, C7 once in staging and store outputs in `docs/audits/chaos-<date>.md`.
- [ ] Step 5: commit `test(chaos): drills C1 to C7 and staging probe`.

### Task 17.7: Backup, restore and erasure-log replay

- [ ] Step 1, failing tests: `backend/tests/integration/hardening/test_erasure_log_export.py::test_export_written_after_each_erasure_and_daily`, `::test_export_is_append_only_object_key`; `infra/dr/tests/test_restore_drill.py` with scratch PostgreSQL: `test_erased_customer_stays_erased_after_restore` (erase, take backup, restore to before the erasure, import the copy, replay: customer pseudonymised), `test_integrity_sql_detects_imbalance`, `test_drill_fails_when_rto_exceeded` (injected clock), `test_drill_runs_audit_verifier_after_replay`, `test_marker_transaction_present_or_absent_as_designed`.
- [ ] Step 2: run; expect failures.
- [ ] Step 3: implement `erasure_log_export.py` (write the rows of `erasure_log` to the locked bucket after each erasure and daily), `verify_integrity.sql` (every ledger transaction sums to zero; per-currency account sums zero; wallet balances equal posting sums; latest `provider_events` before target present and none after; migrations head matches), and `restore_drill.sh` following the PITR checklist of docs/09 section 8.3: restore base backup to the drill environment, set `recovery_target_time`, promote, import the erasure-log copy, `SELECT hub.replay_erasure_log()`, run the audit verifier and integrity SQL, boot the production release and run `make smoke`, print timings, destroy the environment.
- [ ] Step 4: rerun until pass; run the drill once in staging and record the timings and reviewer in `docs/audits/restore-<date>.md`; RTO at most 30 minutes (instance) and 2 hours (corruption scenario).
- [ ] Step 5: commit `feat(dr): restore drill with erasure-log replay and integrity checks`.

### Task 17.8: Runbooks RB-01 to RB-20

- [ ] Step 1, failing test `tests/test_runbooks.py`: `test_twenty_runbooks_exist_with_ids` (`RB-01` to `RB-20` files), `test_each_has_ten_headings_in_order`, `test_each_has_commands_or_screens_in_first_five_minutes` (at least 3 numbered steps), `test_no_placeholder_words` (no placeholder markers), `test_every_alert_runbook_exists_and_is_complete` (reads `runbook_url` values from the rule files and matches ids), `test_titles_match_docs09_index`.
- [ ] Step 2: run `python -m pytest tests/test_runbooks.py -q`; expect failures.
- [ ] Step 3: write the twenty files from `docs/09` section 9.1 outlines using `_template.md`; operator-facing steps use screen names, engineer steps give commands and queries; RB-09 holds the PITR checklist and the erasure replay; RB-18 holds the analytics role-settings fix (a migration, never by hand). Set "Last drilled" to the drill reports of tasks 17.6 and 17.7 where applicable and otherwise `not yet drilled` plus the planned game day.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `docs(runbooks): RB-01 to RB-20 with completeness test`.

### Task 17.9: Accessibility audit

- [ ] Step 1, failing tests: `frontend/e2e/a11y/routes.spec.ts::every route has zero serious or critical axe violations` (iterates the route list generated from the Next.js build manifest, for `/fr` and `/en`, light and dark, widths 320 and 1280, states loading, empty, error, populated), `keyboard.spec.ts::top-up flow by keyboard at 200 percent zoom`, `::ticket reply flow by keyboard at 200 percent zoom`, `::focus visible on every interactive element of the staff dashboard`, `::reduced motion honoured`, `routes.spec.ts::baseline file has no entry past its expiry date`.
- [ ] Step 2: `cd frontend && npx playwright test e2e/a11y`; expect failures listing real violations.
- [ ] Step 3: fix violations in components (never loosen the test or add baseline entries without an expiry within 30 days); write `docs/audits/a11y-<date>.md` with route count, violation counts before and after, the keyboard script result, and the manual NVDA and VoiceOver checklist (TC-NFR-10-3 is manual: a person runs it and signs the log).
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `fix(a11y): axe and keyboard audit fixes with results log`.

### Task 17.10: Dependency automation and security review fixes

- [ ] Step 1, failing tests: `tests/test_dependency_policy.py::test_renovate_config_groups_weekly_and_security_immediate`, `::test_majors_never_automerge`, `::test_sensitive_packages_need_owner` (django, djangorestframework, psycopg, pyjwt, cryptography, anthropic, next, postgres and redis images), `::test_vulnerability_sla_values_in_policy` (48 hours, 7 days, 30 days), `tests/test_security_ci.py::test_pipeline_runs_gitleaks_pip_audit_npm_audit_semgrep_trivy_and_zap_baseline`, `::test_asvs_checklist_has_row_per_chapter_with_evidence`.
- [ ] Step 2: run `python -m pytest tests/test_dependency_policy.py tests/test_security_ci.py -q`; expect failures.
- [ ] Step 3: write `renovate.json` per docs/09 section 2.4 (stale package names there are replaced by the real stack), the security workflow, `docs/security/asvs-checklist.md` (chapters V1 to V14 each linked to an automated check or a documented manual test), run ZAP baseline, Semgrep, gitleaks, testssl against staging, and fix every high or critical finding; record each fix with commit hash in `docs/security/review-fixes.md`. External penetration test is a manual pre-go-live item: record the scope document, not a result.
- [ ] Step 4: rerun until pass; staging scans show no high or critical open (TC-NFR-06-1).
- [ ] Step 5: commit `chore(security): dependency automation, ASVS checklist and review fixes`.

## Verification

`make verify SLICE=17` runs:

1. `python -m pytest tools/loadtest/tests infra/chaos/tests infra/dr/tests tests/test_runbooks.py tests/test_dependency_policy.py tests/test_security_ci.py -q` : exit 0.
2. `promtool test rules infra/observability/tests/*.yml && python tools/scripts/check_alert_rules.py` : SUCCESS and exit 0.
3. `cd backend && python -m pytest tests/integration/hardening -q` : exit 0.
4. `python tools/loadtest/run.py --scenario s6 --profile full --compare baseline` : exit 0 (all thresholds, invariants and CPU limits; on a scaled environment the report says `profile=scaled` and the verifier treats it as not passed, see 99-verification).
5. `python infra/chaos/run.py --experiment c2 --staging` and `--experiment c4 --staging` : exit 0.
6. `bash infra/dr/restore_drill.sh --target-time <random time in last 7 days> --env drill` : exit 0, total under 30 minutes (2 hours for the corruption variant).
7. `cd frontend && npx playwright test e2e/a11y` : zero failures.
8. `python tools/check_traceability.py && python -m pytest tests/test_alignment.py -q` : exit 0.

## Demo script

1. Open the SLO dashboard; run `make load-smoke SCENARIO=s6`; show p95 lines under thresholds and the ledger post-check output.
2. Stop Redis (`python infra/chaos/run.py --experiment c2`); show the `RedisDown` alert and the runbook link opening RB-04.
3. Alter one audit row in a scratch copy and run the verifier: the first bad `seq` is named; `AuditChainBroken` fires in the rule test.
4. Run the restore drill; show an erased test customer still erased and the timings.
5. Show the axe report with zero serious violations and the weekly dependency pull request.

## Done criteria

- All tasks committed; `make verify SLICE=17` green; audit logs in `docs/audits/` exist for load, chaos, restore and accessibility; progress file updated.
- Requirements: NFR-01, NFR-02, NFR-03, NFR-04, NFR-06, NFR-09, NFR-10, NFR-14.
- Test cases: TC-NFR-01-1, TC-NFR-01-2, TC-NFR-01-3 (manual soak report), TC-NFR-02-1, TC-NFR-02-2, TC-NFR-02-3, TC-NFR-03-1, TC-NFR-03-2, TC-NFR-04-1, TC-NFR-04-2, TC-NFR-05-2, TC-NFR-06-1, TC-NFR-09-1, TC-NFR-09-2, TC-NFR-09-3, TC-NFR-10-1, TC-NFR-10-2, TC-NFR-10-3 (manual log), TC-NFR-14-1, TC-NFR-14-2 (drill by a person other than the author, logged), TC-NFR-14-3, TC-FR-15-5, TC-FR-15-14, TC-FR-20-6.

## Plan notes for the tech lead

1. docs/09 sections 6.3 and 7.2 use `X-Signature`, `X-Timestamp` and a flat webhook body in the k6 sketch; OpenAPI uses `X-Webhook-*` headers and a `WebhookEnvelope`. Followed OpenAPI.
2. docs/09 puts k6 at `tools/k6/payday.js` and deploy files under `deploy/`; docs/03 uses `tools/loadtest/` and `infra/`. Followed docs/03.
3. docs/09 section 2.4 names `sqlalchemy` and `fastapi` as sensitive packages; the stack is Django (D-42). Replaced in the policy.
4. docs/10 section 5 (NFR-03) wants 90 days of telemetry; docs/09 section 7.1 seeds 7 days. Followed docs/09 (see slice T3 note 1).
5. Audit verifier and anchor are described in docs/04 and scheduled in docs/03 (`identity.verify_audit_chain`), and slice 1's demo mentions a verified chain; this slice builds only what slice 1 left (signing, locked storage, metrics, tamper drill). Check slice 1 first.
6. `hub.replay_erasure_log()` needs the erasure log re-imported from an off-database copy (schema comment), but no document says who keeps that copy. This slice adds a daily and per-erasure export to the locked bucket; the tech lead should confirm it.
7. D-39 needs a `synthetic` provider and a synthetic flag that neither OpenAPI nor the schema contain (see slice T1 notes 1 and 2); the production probe stays disabled until a change request is approved.
8. docs/09 section 7.1 assumes a Terraform-built performance environment at tier C; no slice builds that Terraform. The `scaled` profile runs on any compose host; a `full` pass needs that environment, which is outside this plan.
9. The 10x payday label is not defined in any document; this plan reads it as 10 payments per second times 10 (100 webhook calls per second, the S1 headroom stage).
