# Slice T1: Mobile-money provider stub

- **Goal:** a small standalone service under `tools/provider_stub/` that behaves like a mobile-money provider: it accepts collection requests, answers status pulls, sends signed webhooks to the hub with programmable faults, produces daily and monthly statement CSV files with injectable mismatches, and hosts one registered synthetic provider for the availability probe (D-39).
- **Depends on:** slice 0 (repository, compose, Makefile). Must be finished before slice 4 (top-up and webhook) starts, and before slice 14 (reconciliation) and slice 17 (load, probe).
- **Requirements served:** FR-02, FR-03, FR-14 (as a test double); NFR-01 (probe), NFR-02 and NFR-05 (load inputs).
- **Size:** M, 5 days.
- **Companion files:** [slice-T2-meter-simulator.md](slice-T2-meter-simulator.md), [slice-T3-fixtures-and-seed.md](slice-T3-fixtures-and-seed.md), [slice-T4-verification-tooling.md](slice-T4-verification-tooling.md), [slice-17-hardening.md](slice-17-hardening.md).
- **Spikes or open items:** the confirmation-pull threshold of D-09 is **[open]** (default 100 USD); the stub never decides it, the hub does. Finance acceptance of a synthetic wallet in production is **[open]** (default: the probe runs in staging only). The provider code `synthetic` is not in the OpenAPI `Provider` enum (see plan notes).

## Read first

- `docs/00-shared-context.md#7.3 Money rules` (D-05, D-06, D-09)
- `docs/00-shared-context.md#7.6 AI` (D-38, D-39 only)
- `docs/plan/00-executor-guide.md#4. Working rules (non-negotiable)`
- `api/openapi.yaml#/paths/~1webhooks~1providers~1{provider}` (operation `receiveProviderWebhook`) and the schemas `WebhookEnvelope`, `WebhookAck`, parameters `WebhookId`, `WebhookTimestamp`, `WebhookSignature`, `Provider`
- `api/openapi.yaml#/paths/~1reconciliation~1statements` (upload limits) and schemas `TopUpCreate`, `ReconStatementUpload`
- `docs/03-code-architecture.md#5. Ports and adapters` (the `ProviderGateway` protocol)
- `docs/10-test-strategy.md#6.4 Test doubles`
- `docs/09-devops-sre.md#6.3 Multi-window burn-rate alerts (Prometheus)` (probe paragraph only)
- `db/schema.sql` tables `reconciliation_batches` and `reconciliation_items` (kinds)

## Global constraints

- Webhook route: `POST /api/v1/webhooks/providers/{provider}` on the hub; the stub sends to it, never to another path.
- Signed string is `timestamp.event_id.body` (D-09): `X-Webhook-Signature: v1=hex(HMAC-SHA256(secret, X-Webhook-Timestamp + "." + X-Webhook-Id + "." + raw_body))`.
- `X-Webhook-Id` equals `body.event_id`; `X-Webhook-Timestamp` is Unix seconds; the hub tolerance is 300 s.
- The signature covers the exact bytes sent: serialise once with compact separators in UTF-8, sign those bytes, send those bytes.
- Webhook body is a `WebhookEnvelope`: `event_id`, `type` in `payment.confirmed|payment.failed|payment.reversed`, `occurred_at`, `data{provider_reference, external_reference (uuid of the top-up), amount{amount_minor, currency}, payer_msisdn (E.164), failure_code}`.
- Money is integer minor units plus ISO currency (USD, CDF); never floats.
- Provider codes of the contract: `mpesa_cd`, `airtel_cd`, `orange_cd`; `synthetic` is the registered probe provider (D-39).
- No secret has a default value: the stub refuses to start when a secret variable is missing (no key in the repository).
- No test-only endpoint is added to the hub (D-38); every control endpoint lives in the stub only.
- Phone numbers come only from the synthetic range `+243810000000` to `+243810999999` (see slice T3).
- Statement files: at most 10 MB and 100,000 rows (the hub cap); cell injection tests use cells starting `=`, `+`, `-`, `@`.
- Logs never contain secrets, signatures or full phone numbers (last 4 digits only).

## Review focus

1. Signed bytes differ from sent bytes (re-serialisation after signing), which makes every hub signature test pass for the wrong reason. Pinned by `test_signature_covers_exact_sent_bytes` in Task T1.2.
2. Duplicate and reordered deliveries share one `event_id` but get different timestamps and signatures, or reordering drops a delivery. Pinned by `test_duplicates_reuse_event_id_and_resign_timestamp` and `test_reorder_swaps_two_events` in Task T1.3.
3. Injected statement mismatches do not map one to one to `reconciliation_items.kind`, so slice 14 tests pass against a statement that proves nothing. Pinned by `test_each_injection_maps_to_one_kind` in Task T1.4.
4. The stub's state is lost or inconsistent after restart so a status pull contradicts a webhook already sent. Pinned by `test_status_matches_last_webhook_after_restart` in Task T1.1.
5. The synthetic provider is reachable with a real provider's secret, or the reverse. Pinned by `test_secrets_are_per_provider` in Task T1.5.

## Files

All paths are new unless marked.

- `tools/provider_stub/pyproject.toml`: standalone package `provider_stub`, Python 3.12, dependency `httpx` only; test extra `pytest`, `jsonschema`.
- `tools/provider_stub/src/provider_stub/__main__.py`: command line (`serve`, `probe`, `statement`).
- `tools/provider_stub/src/provider_stub/config.py`: reads environment variables, fails fast on missing secrets.
- `tools/provider_stub/src/provider_stub/state.py`: in-memory store of collections and delivered events with an append-only JSON-lines journal for restart.
- `tools/provider_stub/src/provider_stub/server.py`: threaded stdlib HTTP server and router (`http.server.ThreadingHTTPServer`; no web framework).
- `tools/provider_stub/src/provider_stub/signing.py`: `sign(secret, timestamp, event_id, body)` and `build_headers`.
- `tools/provider_stub/src/provider_stub/deliver.py`: scheduler thread with a heap of pending deliveries; applies scenario modes.
- `tools/provider_stub/src/provider_stub/scenarios.py`: scenario model and matching.
- `tools/provider_stub/src/provider_stub/statements.py`: daily and monthly CSV generation and mismatch injection.
- `tools/provider_stub/src/provider_stub/probe.py`: the synthetic probe loop.
- `tools/provider_stub/schemas/collection_request.schema.json`, `collection_ack.schema.json`, `collection_status.schema.json`, `webhook_envelope.schema.json` (copied from the OpenAPI schema by `tools/scripts/sync_stub_schemas.py`), `statement_row.schema.json`: the interface contract of the double (docs/10 section 6.4).
- `tools/provider_stub/tests/` : `test_signing.py`, `test_server.py`, `test_deliver.py`, `test_statements.py`, `test_probe.py`, `test_contract.py`.
- `tools/scripts/sync_stub_schemas.py`: extracts `WebhookEnvelope` from `api/openapi.yaml` and fails if the committed copy differs.
- `infra/docker/compose.yaml` (modify): add service `provider-stub` on port 9100, health check `GET /health`.
- `Makefile` (modify): target `test-tools-t1` running the stub tests.
- `.env.example` (modify): variable names only, no values.

## Interfaces

**Consumes**

- Hub webhook endpoint `POST {STUB_HUB_BASE_URL}/api/v1/webhooks/providers/{provider}` (default base `http://haproxy:8080`).
- Environment: `STUB_SECRET_MPESA_CD`, `STUB_SECRET_AIRTEL_CD`, `STUB_SECRET_ORANGE_CD`, `STUB_SECRET_SYNTHETIC` (all required, one per provider), `STUB_CONTROL_TOKEN` (required), `STUB_HUB_BASE_URL`, `STUB_JOURNAL_PATH` (default `/var/lib/provider_stub/journal.jsonl`), `STUB_LATENCY_P50_MS` (default 400), `STUB_LATENCY_P99_MS` (default 3000), `STUB_AUTO_CONFIRM_SECONDS` (default 5; 0 disables automatic completion).

**Produces (HTTP on port 9100)**

Provider API (what the hub's `ProviderGateway` adapter calls; no authentication beyond the network, as it is a double):

- `POST /providers/{provider}/collections` body `{reference (uuid), amount{amount_minor,currency}, payer_msisdn}` returns 202 `{provider_reference, reference, status:"pending"}`. Idempotent on `reference`: the same reference returns the same `provider_reference`. A different amount for a known reference returns 409.
- `GET /providers/{provider}/collections/{provider_reference}` returns `{provider_reference, reference, status in pending|successful|failed|reversed, amount, payer_msisdn, updated_at}`.
- `GET /providers/{provider}/statements?date=YYYY-MM-DD` and `?month=YYYY-MM` return `text/csv`.
- `GET /health` returns 200.

Control API (header `Authorization: Bearer <STUB_CONTROL_TOKEN>`):

- `POST /control/scenarios` body `{match{provider?, payer_msisdn?, reference?, amount_minor?}, mode, params}` returns 201 `{scenario_id}`; `DELETE /control/scenarios/{id}`; `POST /control/reset` clears scenarios, collections and journal.
- Modes: `success`, `failure{failure_code}`, `delay{seconds}`, `duplicate{count<=20, spread_ms}`, `reorder{with: scenario or reference}`, `late{seconds}`, `bad_signature`, `stale_signature{age_seconds>300}`, `wrong_secret`, `wrong_amount{amount_minor}`, `wrong_currency{currency}`, `wrong_payer{payer_msisdn}`, `drop`, `slow_initiate{seconds}`, `error_initiate{status}`, `reverse_after{seconds}`.
- `POST /control/collections/{provider_reference}/complete` body `{outcome: successful|failed|reversed}` forces a webhook now.
- `POST /control/statements/inject` body `{provider, date, injections[{kind, count}]}` where kind is `statement_only|ledger_only|amount_mismatch|duplicate_in_statement|currency_mismatch|formula_cell`.
- `GET /control/deliveries` lists sent deliveries `{event_id, provider, attempt, sent_at, hub_status}` for assertions.

Command lines:

- `python -m provider_stub serve --port 9100`
- `python -m provider_stub probe --interval 30 --hub-url URL --region NAME` (synthetic probe; one signed `payment.confirmed` for provider `synthetic` per interval, logs `probe_result{status,latency_ms}`)
- `python -m provider_stub statement --provider mpesa_cd --month 2026-10 --rows 100000 --out FILE`

Python functions (tests import these): `signing.sign(secret: bytes, timestamp: int, event_id: str, body: bytes) -> str` returning `v1=<hex>`; `deliver.build_event(collection, outcome) -> tuple[bytes, dict[str, str]]`; `statements.render(rows, injections) -> bytes`.

## Tasks

### Task T1.1: Package, state and provider API

- [ ] Step 1, failing tests in `tools/provider_stub/tests/test_server.py`: `test_initiate_is_idempotent_on_reference` (two POSTs with the same `reference` return equal `provider_reference`, status 202), `test_initiate_conflicting_amount_is_409`, `test_status_pending_then_successful` (with `STUB_AUTO_CONFIRM_SECONDS=0` and a control `complete`, status becomes `successful`), `test_status_matches_last_webhook_after_restart` (start server, complete, stop, start on the same journal, `GET` returns `successful`), `test_missing_secret_refuses_to_start` (`config.load()` raises `ConfigError`).
- [ ] Step 2: run `cd tools/provider_stub && python -m pytest tests/test_server.py -q`; expect `ModuleNotFoundError: provider_stub`.
- [ ] Step 3: implement `config.load() -> Config` (frozen dataclass), `state.Store` (`add_collection`, `get`, `set_status`, `replay_journal`), router in `server.py` with the three provider endpoints and `/health`. Approach: every state change appends one JSON line (fsync) before the response is sent; on start the store replays the journal; the HTTP layer holds a single lock per store. Latency is simulated by sleeping a value drawn from a log-normal fitted to `STUB_LATENCY_P50_MS` and `STUB_LATENCY_P99_MS`, only when `STUB_SIMULATE_LATENCY=1` (off in unit tests).
- [ ] Step 4: rerun until `5 passed`.
- [ ] Step 5: commit `feat(provider-stub): initiate and status endpoints with journal`.

### Task T1.2: Signing and single delivery

- [ ] Step 1, failing tests in `test_signing.py` and `test_deliver.py`: `test_sign_matches_contract_vector` (secret `b"k"`, timestamp `1700000000`, event id `evt_1`, body `b'{"a":1}'`; expected value is computed in the test with `hmac` and the string `1700000000.evt_1.{"a":1}`; assert the function equals it), `test_signature_covers_exact_sent_bytes` (a fake hub records raw bytes and headers; recompute the HMAC over the recorded bytes and assert equality), `test_headers_complete` (`X-Webhook-Id == body.event_id`, timestamp integer within 2 s of now, signature matches `^v1=[0-9a-f]{64}$`), `test_envelope_validates_against_schema` (jsonschema against `schemas/webhook_envelope.schema.json`).
- [ ] Step 2: run `python -m pytest tests/test_signing.py tests/test_deliver.py -q`; expect import failure.
- [ ] Step 3: implement `signing.sign`, `deliver.build_event`, and `deliver.send(url, body, headers)` using `httpx.Client` with `content=body` (never `json=`, so bytes are not re-serialised). Event ids are `evt_` plus 16 hex from a seeded `random.Random(STUB_SEED)`. `occurred_at` is UTC ISO with `Z`. Record every attempt in `/control/deliveries`.
- [ ] Step 4: rerun until `4 passed` for these files.
- [ ] Step 5: commit `feat(provider-stub): signed webhook delivery`.

### Task T1.3: Scenario engine (faults)

- [ ] Step 1, failing tests in `test_deliver.py`: `test_duplicates_reuse_event_id_and_resign_timestamp` (mode `duplicate{count:3, spread_ms:1100}` yields 4 deliveries with one `event_id`, at least two distinct timestamps, every signature valid), `test_reorder_swaps_two_events` (two collections with `reorder` pair: the failed event is delivered before the confirmed one), `test_late_delivers_after_delay` (uses an injectable clock, no sleeping), `test_bad_signature_fails_verification`, `test_stale_signature_timestamp_older_than_300s`, `test_wrong_amount_changes_body_and_resigns` (signature still valid so the hub must reject on business rules, not signature), `test_drop_sends_nothing_but_status_pull_reports_successful`, `test_scenario_matching_by_msisdn`, `test_reverse_after_sends_reversed_event_with_new_event_id_and_new_provider_txn`.
- [ ] Step 2: run `python -m pytest tests/test_deliver.py -q`; expect failures naming missing `scenarios`.
- [ ] Step 3: implement `scenarios.Scenario` (frozen dataclass), `scenarios.match`, the control endpoints, and a scheduler thread using `heapq` of `(due_time, seq, delivery)` with an injectable `clock` callable. Duplicates are separate heap entries sharing `event_id`, each signed at send time (new timestamp). `drop` skips the send but still sets the collection status so the pull path stays truthful (the hub's pull verification and `pending_unknown` sweeper depend on it). The reversal event carries its own `event_id` and `provider_reference` suffix `-R` (the schema keys a reversal by its own provider transaction id).
- [ ] Step 4: rerun until `9 passed`.
- [ ] Step 5: commit `feat(provider-stub): programmable delivery faults`.

### Task T1.4: Statements

- [ ] Step 1, failing tests in `test_statements.py`: `test_daily_csv_header_and_rows` (header exactly `provider_reference,external_reference,occurred_at,amount_minor,currency,fee_minor,status,payer_msisdn_last4`; one row per successful collection that day, sorted by `occurred_at`), `test_monthly_csv_equals_concatenated_days`, `test_each_injection_maps_to_one_kind` (for each of the five kinds, inject one, compare the stub's own expected-result file `expected_items.json` with the kind; assert exactly one row of that kind and none other), `test_duplicate_line_has_same_provider_reference`, `test_currency_mismatch_changes_only_currency`, `test_formula_cell_present_for_neutralisation_test` (a cell starting `=HYPERLINK(`), `test_100000_rows_under_10mb`, `test_statement_is_deterministic_for_seed` (same seed gives the same SHA-256).
- [ ] Step 2: run `python -m pytest tests/test_statements.py -q`; expect import failure.
- [ ] Step 3: implement `statements.render(rows, injections)`. `statement_only` appends a line whose reference the hub never saw; `ledger_only` omits a line for a collection the stub confirmed by webhook; `amount_mismatch` changes `amount_minor` by one unit on a line; `duplicate_in_statement` repeats a line; `currency_mismatch` swaps the currency; `formula_cell` prefixes a free-text field. Quote with the stdlib `csv` writer (QUOTE_MINIMAL, `\n` line ends, UTF-8 without BOM). Each call also writes `expected_items.json` (kind and provider reference per injection) next to the output when `--expected` is given. Endpoints serve the same renderer.
- [ ] Step 4: rerun until `8 passed`.
- [ ] Step 5: commit `feat(provider-stub): daily and monthly statements with injected mismatches`.

### Task T1.5: Synthetic provider, probe, contract tests and compose

- [ ] Step 1, failing tests: `test_probe.py::test_probe_sends_signed_event_every_interval` (injectable clock, 3 intervals, 3 deliveries, distinct event ids, valid signatures with `STUB_SECRET_SYNTHETIC`), `test_probe_records_failures_without_stopping` (hub returns 503; loop continues; result `status=503`), `test_secrets_are_per_provider` (an event for `synthetic` signed with the `mpesa_cd` secret does not verify against `STUB_SECRET_SYNTHETIC` and the reverse), `test_contract.py::test_all_documents_validate_against_schemas` (every response and webhook body validates against `schemas/*.json`), `test_contract.py::test_envelope_schema_equals_openapi` (runs `sync_stub_schemas.py --check`; fails when the copy drifts).
- [ ] Step 2: run `python -m pytest tests/test_probe.py tests/test_contract.py -q`; expect failures.
- [ ] Step 3: implement `probe.run(interval, hub_url, region, clock)`; the event is `payment.confirmed` for amount 1 minor unit USD with `external_reference` of the synthetic top-up id read from `STUB_PROBE_TOP_UP_ID` (the hub side is prepared in slice 17; until then the probe is exercised against a fake hub). Add the compose service and `make test-tools-t1`.
- [ ] Step 4: rerun until `5 passed`; then `make test-tools-t1` shows all T1 tests pass.
- [ ] Step 5: commit `feat(provider-stub): synthetic probe, schemas and compose service`.

## Verification

`make verify SLICE=T1` runs:

1. `cd tools/provider_stub && python -m pytest -q` : pass when exit 0 and at least 27 tests passed.
2. `python tools/scripts/sync_stub_schemas.py --check` : exit 0 (no drift from `api/openapi.yaml`).
3. `docker compose -f infra/docker/compose.yaml up -d provider-stub --wait && curl -fsS localhost:9100/health` : prints status 200.
4. `python -m provider_stub statement --provider mpesa_cd --month 2026-10 --rows 100000 --out /tmp/s.csv && test $(wc -c < /tmp/s.csv) -lt 10485760` : exit 0.
5. `python tools/verify_forbidden_patterns.py --paths tools/provider_stub` (after slice T4 exists; before it, skip) : exit 0.
6. `python tools/check_traceability.py` : exit 0.

## Demo script

1. `make up`; confirm `provider-stub` is healthy.
2. `curl -X POST localhost:9100/control/scenarios -H "Authorization: Bearer $STUB_CONTROL_TOKEN" -d '{"match":{"provider":"mpesa_cd"},"mode":"duplicate","params":{"count":3,"spread_ms":500}}'`.
3. Initiate a collection with `POST /providers/mpesa_cd/collections`; wait 5 s; `GET /control/deliveries` shows four deliveries with one `event_id`.
4. Request `GET /providers/mpesa_cd/statements?date=<today>` and show the line.
5. Start `python -m provider_stub probe --interval 5`, watch three `probe_result` log lines.

## Done criteria

- All tasks committed, `make verify SLICE=T1` green, progress file updated.
- Doubles contract-checked against JSON schemas (docs/10 section 6.4).
- The stub makes these hub test cases runnable once their slices exist: TC-FR-02-1, TC-FR-02-4, TC-FR-02-6, TC-FR-03-1, TC-FR-03-3, TC-FR-03-4, TC-FR-03-5, TC-FR-03-7, TC-FR-03-8, TC-FR-03-9, TC-FR-14-1, TC-FR-14-2, TC-NFR-01-1, TC-NFR-02-1. The stub's own tests carry no TC id; the owning slices implement them.

## Plan notes for the tech lead

1. `api/openapi.yaml` `Provider` parameter enum lists only `mpesa_cd`, `airtel_cd`, `orange_cd`, yet D-39 requires a registered synthetic provider posting to the real handler (docs/09 section 6.3 uses `/webhooks/providers/synthetic`). Followed D-39 for the stub (provider code `synthetic`); a change request to the frozen OpenAPI enum is needed before slice 17 can run the probe against the hub.
2. `db/schema.sql` has no synthetic flag on top-ups, customers or provider events, although D-39 says synthetic activity is flagged and excluded from reports. Gap left to the change request of note 1.
3. OpenAPI wire type is `payment.confirmed`; `provider_events.event_type` CHECK allows `payment.succeeded`. The stub follows OpenAPI; the hub must map.
4. docs/09 section 7.2 k6 sketch uses headers `X-Signature` and `X-Timestamp`; OpenAPI uses `X-Webhook-Signature`, `X-Webhook-Timestamp`, `X-Webhook-Id`. Followed OpenAPI.
5. The provider-side API (initiate, status, statement) and the statement CSV columns are not in any document. The shapes above are this plan's decision; slice 4 and slice 14 adapters must conform to `tools/provider_stub/schemas/`.
6. docs/09 says tools live in `tools/` with `deploy/compose`; docs/03 says `infra/docker`. Followed docs/03.
