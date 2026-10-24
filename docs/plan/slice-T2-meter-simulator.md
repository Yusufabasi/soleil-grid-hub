# Slice T2: Meter and gateway simulator

- **Goal:** a standalone tool under `tools/meter_simulator/` that simulates site gateways and up to 300,000 virtual meters in one process: hourly telemetry batches, late and duplicate batches, offline sites, heartbeats, token receipt with acknowledgement or silence, register tampering and reconnection storms with jitter. It speaks the gateway API of `api/openapi.yaml` with a gateway-kind API key.
- **Depends on:** slice 0. Must be finished before slice 6 (token issuance) and slice 8 (telemetry) start; used by slice 17 (S2 telemetry storm, NFR-03).
- **Requirements served:** FR-05, FR-06, FR-07 (as a test double); NFR-03.
- **Size:** L, 6 days.
- **Companion files:** [slice-T1-provider-stub.md](slice-T1-provider-stub.md), [slice-T3-fixtures-and-seed.md](slice-T3-fixtures-and-seed.md), [slice-T4-verification-tooling.md](slice-T4-verification-tooling.md), [slice-17-hardening.md](slice-17-hardening.md).
- **Spikes or open items:** the hub-to-gateway token push protocol is not specified in any document (see plan notes); this slice defines the simulator side. No business item is open.

## Read first

- `docs/00-shared-context.md#Capacity assumptions (design target, NFR-03)`
- `docs/00-shared-context.md#7.5 Data, infrastructure and operations` (D-20, D-25)
- `docs/00-shared-context.md#7.2 Canonical state machines` (the `meter_tokens.status` and `alerts.kind` rows)
- `api/openapi.yaml#/paths/~1telemetry~1batches`, `#/paths/~1gateway~1heartbeat`, `#/paths/~1gateway~1tokens~1{token_id}~1acknowledge` and schemas `TelemetryBatch`, `TelemetryReading`, `TelemetryBatchResult`, `GatewayHeartbeat`, `GatewayHeartbeatAck`, `TokenAck`, `Token`
- `docs/03-code-architecture.md#6.2 Token issuance (FR-05)` and `#5. Ports and adapters` (`MeterGateway`)
- `docs/05-api-design.md#10. Rate-limit tiers (authoritative for every document)`
- `docs/10-test-strategy.md#2.4 Telemetry duplicates and late data (FR-06, FR-07)` and `#6.4 Test doubles`
- `docs/09-devops-sre.md#7.2 Scenarios and thresholds` (rows S2 only)

## Global constraints

- Authentication header is `X-API-Key` with a gateway-kind key `sgh_<env>_<keyid>_<secret>`; one key per physical gateway (D-20); a key is bound to a set of sites.
- Endpoints used: `POST /api/v1/telemetry/batches`, `POST /api/v1/gateway/heartbeat` (every 300 s), `POST /api/v1/gateway/tokens/{token_id}/acknowledge`; nothing else (no pull endpoint, no remote disconnect, no configuration; D-07, D-20).
- A batch holds at most 1,000 readings and 256 KB; the hub answers 413 above that.
- Readings up to 7 days late are accepted; more than 5 minutes in the future are rejected per reading; the dedupe key is `(meter, recorded_at)`; `seq` is advisory; `batch_id` (uuid) is the batch idempotency key (D-25).
- Ack results are exactly `applied`, `rejected`, `counter_replayed`.
- Meter rule: a token with a counter at or below the last applied counter is refused; a resend with the same token id is not applied twice; an expired token is refused.
- Heartbeat missing 15 minutes raises `site_offline`; a meter silent more than 90 minutes raises `meter_offline`.
- Rate limit tier `gateway` is 300 requests per minute per key: the simulator honours `429` with `Retry-After` and never retries faster than the header says.
- Peak capacity design: about 83 batches per second average and about 417 at 5x (1,700 readings per second).
- Keys are read from files or environment variables, never printed or logged; meter serials and key ids may be logged, secrets not.
- Memory budget: 300,000 virtual meters in one process under 1.5 GB resident.

## Review focus

1. Virtual-meter state drifts (energy register not monotonic, counter not monotonic), so hub rules for "decreasing register" are tested by accident. Pinned by `test_register_is_monotonic_unless_tamper` in Task T2.1.
2. Replayed batches regenerate readings with new values instead of the identical bytes, so hub dedupe tests prove nothing. Pinned by `test_duplicate_batch_is_byte_identical` in Task T2.3.
3. Reconnection storm without jitter or without honouring `Retry-After`, which turns the simulator into a denial-of-service on itself and hides real hub behaviour. Pinned by `test_storm_spreads_over_window_and_obeys_retry_after` in Task T2.5.
4. Token receiver applies a replayed counter or a duplicate token id twice. Pinned by `test_replayed_counter_is_not_applied` and `test_duplicate_token_id_applies_once` in Task T2.4.
5. Simulator memory or CPU limits the load test instead of the hub (generator bottleneck). Pinned by `test_300k_meters_memory_and_rate` in Task T2.6.

## Files

- `tools/meter_simulator/pyproject.toml`: package `meter_simulator`, Python 3.12, dependency `httpx`; test extras `pytest`, `jsonschema`.
- `tools/meter_simulator/src/meter_simulator/__main__.py`: command line (`run`, `receiver`, `storm`, `describe`).
- `tools/meter_simulator/src/meter_simulator/config.py`: reads environment and a topology file.
- `tools/meter_simulator/src/meter_simulator/fleet.py`: struct-of-arrays state of all meters (`array('q')` and `array('l')`, no numpy).
- `tools/meter_simulator/src/meter_simulator/curves.py`: consumption curves (household, shop, pump) and clock skew.
- `tools/meter_simulator/src/meter_simulator/batcher.py`: builds batches (per-meter or aggregated) and keeps replay buffers.
- `tools/meter_simulator/src/meter_simulator/client.py`: async HTTP client with rate-limit handling.
- `tools/meter_simulator/src/meter_simulator/faults.py`: late, duplicate, offline, tamper, storm models.
- `tools/meter_simulator/src/meter_simulator/receiver.py`: token receiver (stdlib HTTP server in a thread) with meter-side counter semantics.
- `tools/meter_simulator/src/meter_simulator/control.py`: control endpoints.
- `tools/meter_simulator/schemas/token_push.schema.json`, `token_push_result.schema.json`, `batch.schema.json` (copied from OpenAPI by `tools/scripts/sync_simulator_schemas.py`): the double's contract.
- `tools/meter_simulator/topology.example.yaml`: sample topology (sites, gateways, meter counts; no keys).
- `tools/meter_simulator/tests/`: `test_fleet.py`, `test_batcher.py`, `test_faults.py`, `test_receiver.py`, `test_storm.py`, `test_scale.py`, `test_contract.py`.
- `tools/scripts/sync_simulator_schemas.py`: drift check against `api/openapi.yaml`.
- `infra/docker/compose.yaml` (modify): service `meter-sim` (port 9200 control, 9201 token receiver), default fleet 200 meters.
- `Makefile` (modify): target `test-tools-t2`.

## Interfaces

**Consumes**

- Hub endpoints listed in Global constraints at `SIM_HUB_BASE_URL` (default `http://haproxy:8080`).
- Environment: `SIM_HUB_BASE_URL`, `SIM_KEYS_FILE` (JSON map `gateway_id -> key`, mounted file, never in the repository), `SIM_TOPOLOGY` (path to the topology file), `SIM_SEED` (integer), `SIM_SPEED` (virtual seconds per real second, default 1), `SIM_CONTROL_TOKEN` (required).
- Topology file: `sites[]{site_id, gateways[]{gateway_id, meters[]{serial_prefix, count, curve}}}`; `describe` prints counts.

**Produces**

- Telemetry traffic and heartbeats as above; ack calls for tokens it accepted.
- Token receiver (the simulator side of the hub's outbound adapter): `POST /gateway/push/tokens` on port 9201 with JSON `{token_id, meter_serial, counter, expires_at, energy_wh, payload}`; replies 202 `{status:"accepted_for_delivery"}`, or 422 `{status:"rejected", reason: invalid_meter|counter_stale|expired}`, or hangs for `timeout` mode. Final outcome is always sent later through the acknowledge call.
- Control (port 9200, `Authorization: Bearer <SIM_CONTROL_TOKEN>`): `GET /control/stats` (batches sent, accepted, duplicates, late, rejected, 429 count, in-flight, lag), `POST /control/modes` body `{gateway_id?, site_id?, mode, params}` with modes `late{fraction, max_age_hours<=168}`, `duplicate{fraction}`, `offline{site_id, duration_s}`, `heartbeat_pause{gateway_id, duration_s}`, `token_noack{fraction}`, `token_reject{reason}`, `token_timeout`, `tamper{meter_serial, kind: register_decrease|register_jump|clock_skew}`, `future{meter_serial}`, `malformed{fraction}`, `unknown_meter{count}`; `POST /control/storm` body `{meters, window_s, backlog_hours, jitter}`; `POST /control/reset`.
- Command lines: `python -m meter_simulator run --meters 300000 --shape per_meter|aggregated --speed 1`, `python -m meter_simulator storm --meters 100000 --window 120 --backlog-hours 24`, `python -m meter_simulator receiver --port 9201`.
- Python: `fleet.Fleet(count: int, seed: int)`, `Fleet.read(index: int, at: int) -> Reading`, `batcher.build_batch(fleet, indexes, gateway_id, sent_at, seq_start) -> tuple[bytes, str]` returning body bytes and batch id, `receiver.Meter.apply(counter: int, token_id: str, expires_at: int, now: int) -> str` returning one of `applied|counter_replayed|rejected`.

## Tasks

### Task T2.1: Fleet model and curves

- [ ] Step 1, failing tests in `tests/test_fleet.py`: `test_register_is_monotonic_unless_tamper` (1,000 reads over 24 virtual hours for 50 meters: `energy_wh_total` never decreases), `test_same_seed_same_readings`, `test_readings_every_15_minutes` (4 per hour, timestamps on :00 :15 :30 :45 UTC), `test_clock_skew_applies_only_to_marked_meters`, `test_serials_unique_and_match_pattern` (`SGM-` plus 7 digits), `test_values_within_schema_ranges` (power_w, voltage 200000 to 250000 mV).
- [ ] Step 2: `cd tools/meter_simulator && python -m pytest tests/test_fleet.py -q`; expect import error.
- [ ] Step 3: implement `Fleet` with parallel arrays (`serial_no`, `energy_wh`, `skew_s`, `counter_applied`, `curve_id`). `read(i, at)` derives power from the curve and a hash of `(seed, i, at)` (stateless), then advances the stored register to `max(register, computed)` so it is monotonic. Constants live in `curves.py`.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(meter-sim): virtual fleet and consumption curves`.

### Task T2.2: Batches and hub client

- [ ] Step 1, failing tests in `tests/test_batcher.py` and `tests/test_contract.py`: `test_per_meter_batch_has_4_readings`, `test_aggregated_batch_never_exceeds_1000_readings_or_256kb`, `test_batch_validates_against_schema`, `test_batch_id_is_uuid_and_unique`, `test_client_sends_api_key_header_not_logged` (fake hub records `X-API-Key`; log capture does not contain the secret part), `test_client_honours_retry_after` (fake hub answers 429 with `Retry-After: 2`; next request is at least 2 s later by injectable clock), `test_schema_copy_equals_openapi`.
- [ ] Step 2: run `python -m pytest tests/test_batcher.py tests/test_contract.py -q`; expect failures.
- [ ] Step 3: implement `build_batch` (compact JSON, UTF-8, keys sorted) and `client.Client.post_batch/heartbeat/ack` over one shared `httpx.AsyncClient` (HTTP/1.1 keep-alive, `max_connections` from `SIM_MAX_CONNECTIONS`, default 200). On 429 the client sleeps the header value plus jitter up to 20 percent. On 5xx or timeout it retries the same bytes (same `batch_id`) with exponential backoff capped at 60 s.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(meter-sim): batch builder and gateway client`.

### Task T2.3: Late, duplicate, offline, malformed and tamper faults

- [ ] Step 1, failing tests in `tests/test_faults.py`: `test_duplicate_batch_is_byte_identical` (the replayed body equals the original bytes and the `batch_id` is the same), `test_overlapping_batch_shares_readings_with_new_batch_id` (new id, 2 readings overlap), `test_late_batch_age_between_1h_and_168h`, `test_too_old_batch_age_over_7_days_only_when_requested`, `test_future_reading_6_minutes_ahead`, `test_malformed_reading_rate_is_requested_fraction` (1 in 1,000 with fixed seed gives exactly the planned indexes), `test_offline_site_sends_no_batch_and_no_heartbeat`, `test_heartbeat_pause_keeps_telemetry_flowing`, `test_tamper_register_decrease_lowers_register_once`, `test_unknown_meter_serial_not_in_topology`.
- [ ] Step 2: run `python -m pytest tests/test_faults.py -q`; expect failures.
- [ ] Step 3: implement `faults.py` as pure functions over `(batch, rng)` plus the mode registry used by the control endpoint. Replay buffers keep the original bytes of the last N batches per gateway (N from `SIM_REPLAY_BUFFER`, default 100) so duplicates are exact. An offline period suppresses everything for the gateway, then on reconnection sends the backlog in order of `recorded_at`, split into batches of at most 1,000 readings.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(meter-sim): late, duplicate, offline, malformed and tamper modes`.

### Task T2.4: Token receiver, acknowledgement and silence

- [ ] Step 1, failing tests in `tests/test_receiver.py`: `test_replayed_counter_is_not_applied` (apply counter 5, then counter 5 with a new token id: `counter_replayed`), `test_lower_counter_is_refused`, `test_duplicate_token_id_applies_once` (same id twice: one `applied`, one idempotent repeat of the same result), `test_expired_token_rejected`, `test_token_for_unknown_meter_rejected_invalid_meter`, `test_noack_mode_never_calls_hub`, `test_ack_posts_counter_and_result` (fake hub receives `{counter, result:"applied", applied_at}` at `/gateway/tokens/{id}/acknowledge`), `test_reject_mode_returns_422_with_reason`, `test_timeout_mode_hangs_past_client_timeout`, `test_push_payload_validates_against_schema`.
- [ ] Step 2: run `python -m pytest tests/test_receiver.py -q`; expect failures.
- [ ] Step 3: implement `receiver.Meter.apply` over the fleet arrays (`counter_applied`, a dict of seen token ids bounded to 10 per meter) and the stdlib threaded server. Approach: the receiver replies 202 immediately after validation, then a worker thread waits the configured delay and calls the hub acknowledge endpoint through `client.ack`, unless `token_noack` selects the token. The ack call carries an idempotency header `Idempotency-Key` equal to the token id so resends of the ack are safe.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(meter-sim): token receiver with counter semantics and ack modes`.

### Task T2.5: Heartbeat loop and reconnection storm

- [ ] Step 1, failing tests in `tests/test_storm.py`: `test_heartbeat_every_300_virtual_seconds`, `test_heartbeat_body_matches_schema` (`sent_at`, `firmware`, `meters_connected`, `token_queue_depth`, `clock_offset_ms`), `test_storm_spreads_over_window_and_obeys_retry_after` (100,000 meters, window 120 s: no 5-second bucket holds more than 12 percent of the batches; with fake hub returning 429 the observed rate drops below the limit), `test_storm_backlog_is_24_hours_in_order`, `test_storm_batches_respect_limits`, `test_storm_jitter_is_deterministic_per_seed`.
- [ ] Step 2: run `python -m pytest tests/test_storm.py -q`; expect failures.
- [ ] Step 3: implement `storm.run(meters, window_s, backlog_hours, jitter)`: each gateway draws a start offset uniformly in the window (seeded), then sends its backlog as sequential batches. Heartbeat is an asyncio task per gateway with a virtual clock scaled by `SIM_SPEED`.
- [ ] Step 4: rerun until pass.
- [ ] Step 5: commit `feat(meter-sim): heartbeats and reconnection storm`.

### Task T2.6: Scale, control API and compose

- [ ] Step 1, failing tests: `tests/test_scale.py::test_300k_meters_memory_and_rate` (builds a 300,000-meter fleet; resident memory under 1.5 GB measured with `resource.getrusage`; generating one hour of batches for all meters in under 60 s against a null transport), `test_scale.py::test_sustains_417_batches_per_second_to_fake_hub` (fake hub in-process, 30 s, at least 417 per second achieved), `tests/test_contract.py::test_control_requires_bearer`, `test_control_modes_apply_to_next_cycle`, `test_stats_counts_match_fake_hub`.
- [ ] Step 2: run `python -m pytest tests/test_scale.py tests/test_contract.py -q`; expect failures.
- [ ] Step 3: implement `control.py`, the scheduler that drives hourly cycles (each meter sends once per virtual hour, offsets spread evenly so the average is about 83 batches per second for 300,000 meters), `describe`, and the compose service. If the 417 per second test is slower than the target on the CI runner, reduce `max_connections` pressure by raising `aggregated` size; do not lower the threshold.
- [ ] Step 4: rerun until pass; run `make test-tools-t2`.
- [ ] Step 5: commit `feat(meter-sim): scale run, control API and compose service`.

## Verification

`make verify SLICE=T2` runs:

1. `cd tools/meter_simulator && python -m pytest -q` : exit 0, at least 45 tests passed.
2. `python tools/scripts/sync_simulator_schemas.py --check` : exit 0.
3. `python -m meter_simulator describe --topology tools/meter_simulator/topology.example.yaml` : prints totals, exit 0.
4. `docker compose -f infra/docker/compose.yaml up -d meter-sim --wait && curl -fsS -H "Authorization: Bearer $SIM_CONTROL_TOKEN" localhost:9200/control/stats` : JSON with `batches_sent`.
5. `python tools/verify_forbidden_patterns.py --paths tools/meter_simulator` (once T4 exists) : exit 0.

## Demo script

1. `make up`; seed fixtures (`python manage.py load_fixtures --profile dev`, slice T3).
2. `python -m meter_simulator run --meters 200`; show `GET /control/stats` growing and the hub reading count rising.
3. `POST /control/modes {"mode":"duplicate","params":{"fraction":0.1}}`; show `duplicates` in stats and zero new rows.
4. `POST /control/modes {"mode":"offline","params":{"site_id":"<id>","duration_s":1200}}`; after 15 virtual minutes a `site_offline` alert appears.
5. `POST /control/storm {"meters":2000,"window_s":60,"backlog_hours":24}`; the hub lag metric returns to under 60 s.

## Done criteria

- All tasks committed, `make verify SLICE=T2` green, progress file updated.
- The simulator makes these hub test cases runnable once their slices exist: TC-FR-05-7, TC-FR-05-11, TC-FR-06-1, TC-FR-06-2, TC-FR-06-3, TC-FR-06-4, TC-FR-06-5, TC-FR-06-7, TC-FR-06-8, TC-FR-06-9, TC-FR-07-1, TC-FR-07-4, TC-NFR-03-1.
- Its own tests carry no TC id; the slices that consume it implement those cases.

## Plan notes for the tech lead

1. The outbound token push from the hub to the site gateway is named in D-20 and docs/03 section 6.2 but its wire format appears in no document. This slice defines the simulator receiver (`POST /gateway/push/tokens`, JSON fields above) and a schema file; slice 6 must target it.
2. docs/09 section 7.2 k6 sketch posts telemetry as `{batch_id, readings[{meter_id, ts, kwh_x1000}]}` with header `X-Api-Key`; OpenAPI defines `gateway_id`, `batch_id`, `sent_at`, readings with `meter_serial`, `recorded_at`, `energy_wh_total`, and header `X-API-Key`. Followed OpenAPI.
3. docs/10 section 5 asks for 90 days of telemetry in the 300,000-meter seed; docs/09 section 7.1 says 7 days (about 200 million rows). This slice does not seed telemetry (slice T3 does); see its notes.
4. The capacity text pairs "83 batches per second" with one batch per meter per hour; with gateways aggregating, request rate falls. The simulator supports both shapes (`per_meter`, `aggregated`) so S2 can run the worse case.
5. `docs/03` places `tools/loadtest/` for k6; docs/09 uses `tools/k6/`. Followed docs/03 (see slice 17).
