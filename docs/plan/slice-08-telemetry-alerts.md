# Slice 08: Telemetry ingest and alerts

- **Goal:** gateways post batches of meter readings that are stored exactly once, late and duplicate tolerant; the hub detects silent meters and gateways, low balances and anomalous consumption, and raises each alert once, resolves it automatically, and lets operations acknowledge or resolve it.
- **Depends on:** slices 00 to 07, only through what `docs/03-code-architecture.md` section 10 says they produce: the Django project, settings modules, `shared` (UnitOfWork, outbox, Clock, IdGenerator, job registry, DRF helpers), `identity` (`Principal`, `Authorizer`, `AuditLog`, gateway key authentication), `customers` (sites), `meters` (`MeterDirectory`, gateway routes, `meter_state`), `ledger` (`wallet.low_balance`, `LedgerService.balance`), notifications (consumes `alert.raised`). Slice 15 hardens key handling later; this slice only needs a working `gateway` key.
- **Requirements served:** FR-06, FR-07; NFR-03 (ingest rate), NFR-02 (ingest latency).
- **Size:** L, 7 days.
- **Companion files:** `frontend/fe-08-alerts.md` (alerts list, detail, acknowledge, resolve, rules screen). It builds against the operations in this slice; no backend work lives there.
- **Spikes or open items:** [open] maintenance windows have no table in `db/schema.sql`; default: a site-scoped `alert_rules` row with `enabled = false` suppresses that kind for the site. [open] low-balance threshold: default is the seeded rule (500 minor units), not the PRD's "2 days of consumption". Spike S08-1 (Task 08.2): measure `unnest` against `COPY`; default switch `HUB_TELEMETRY_COPY_MIN_ROWS` = 200.

## Read first

- `docs/00-shared-context.md#D-25 Telemetry` (section 7.5) and `#7.2 Canonical state machines` (alerts.kind row)
- `docs/adr/0010-telemetry-in-partitioned-postgresql.md#Decision`
- `docs/03-code-architecture.md#2.7 telemetry (FR-06, anomaly part of FR-07)`
- `docs/03-code-architecture.md#6.3 Telemetry ingest (FR-06, FR-07)`
- `docs/03-code-architecture.md#4. Domain event catalogue` (rows telemetry.*, meter.*, site.offline, alert.raised, consumption.anomaly_detected, wallet.low_balance)
- `docs/04-data-model.md#6.1 Partitioning` and `#10.8 Bulk and partitioned inserts`
- `docs/05-api-design.md#7.5 Telemetry (D-25)`
- `docs/01-prd.md` acceptance criteria AC-FR-06-1 to AC-FR-06-5 and AC-FR-07-1 to AC-FR-07-5
- `docs/10-test-strategy.md#2.4 Telemetry duplicates and late data (FR-06, FR-07)`
- `db/schema.sql` tables `reading_batches`, `meter_readings`, `meter_state`, `gateways`, `alerts`, `alert_rules`; functions `ensure_meter_reading_partitions`, `verify_api_key`; the `hub_ingest` and `hub_worker` grants

## Global constraints

- A batch has at most 1,000 readings or 256 KB; over either is rejected whole with 413 `payload_too_large` and nothing stored (D-25).
- Readings up to 7 days late are accepted; a reading more than 5 minutes in the future is rejected per reading as `in_future`; older than 7 days is `too_old`. One bad reading never fails the batch.
- Key is `(meter_id, read_at)`; first write wins; `seq` is advisory; a corrected value for an existing timestamp is rejected, never merged.
- Batch idempotency is `reading_batches (gateway_id, batch_key)`; the batch row and its readings commit in one transaction.
- Cumulative energy must be monotonic per meter; a decrease or implausible jump is flagged and kept out of aggregates and alerts.
- A gateway key reaches only meters of its bound sites; the response is 202 with `accepted, duplicates, late, rejected, rejected_items`.
- `late` counts accepted readings older than 1 hour.
- Alert rules: meter silent more than 90 minutes; gateway heartbeat (every 5 minutes) missing 15 minutes; both comparisons are strict.
- `alerts.kind` is only `meter_offline, site_offline, low_balance, anomalous_consumption`; one unresolved alert per `dedup_key`; auto-resolve after 2 consecutive healthy checks.
- Only `operations_engineer` acknowledges, resolves, lists alerts and edits rules (`api/permissions.yaml`); a support agent gets 403 and the denial is audited.
- No ORM writes to `meter_readings`; raw SQL through psycopg; `hub_ingest` role only; `ATOMIC_REQUESTS` stays off; no per-reading events (cardinality).
- Back-pressure: when the telemetry in-flight cap is full or the pool wait exceeds 200 ms, answer 503 with `Retry-After` between 30 and 90 seconds; telemetry sheds before webhooks.
- Use the `Clock` port; timestamps UTC; no personal data or key material in logs.

## Review focus

1. **A gateway replays the same batch id with a different body** (corrupt retry). Pinned by `test_same_batch_id_different_body_returns_409` in `tests/integration/telemetry/test_ingest_idempotency.py`; owned by Task 08.2.
2. **A late reading arrives after its alert was resolved.** It must not resurrect the offline alert or duplicate an anomaly alert. Pinned by `test_late_reading_does_not_resurrect_alert` in `tests/integration/telemetry/test_alert_engine.py`; Task 08.5.
3. **Duplicates inside one batch and across concurrent batches** abort a naive `COPY`. Pinned by `test_copy_path_survives_duplicates_and_overlap` in `tests/integration/telemetry/test_reading_writer.py`; Task 08.2.
4. **A site gateway goes silent: hundreds of meters would each alert.** Only one `site_offline` opens and meter alerts are suppressed. Pinned by `test_site_offline_suppresses_meter_alerts` in `test_alert_engine.py`; Task 08.5.
5. **Month boundary and 5-minute future skew.** A reading at 23:58 on the last day of a month received at 00:01 lands in the right partition and one at now+301 s is rejected. Pinned by `test_month_boundary_and_future_skew` in `tests/integration/telemetry/test_ingest_idempotency.py`; Task 08.1.

## Files

Create (backend root `backend/src/hub/`; tests under `backend/tests/`):

- `db/migrations/0080_telemetry_hourly_and_copy.sql`: tables `meter_hourly_usage`, `telemetry_dirty_hours`; `GRANT TEMPORARY` on the database to `hub_ingest`; grants for the new tables; index `ix_alerts_meter_kind_opened` on `alerts (meter_id, kind, opened_at DESC)`.
- `shared/migrations/0080_telemetry_hourly_and_copy.py`: `RunSQL` applying that file.
- `telemetry/domain/validation.py`: pure per-reading validation loop (no DRF).
- `telemetry/domain/monotonic.py`: register checks (decrease, jump) producing quality flags.
- `telemetry/domain/alert_rules.py`: pure functions deciding offline, spike and healthy-check transitions.
- `telemetry/application/ingest.py`: `IngestBatch` use case.
- `telemetry/application/ports.py`: `ReadingWriter`, `WalletBalanceReader`.
- `telemetry/application/alert_engine.py`: open, dedupe, auto-resolve.
- `telemetry/application/alert_operations.py`: list, acknowledge, resolve, rule update.
- `telemetry/application/jobs.py`: `aggregate_hourly`, `scan_heartbeats`, `detect_anomalies`, `ensure_partitions`, `purge_batches`.
- `telemetry/application/handlers.py`: consumers of `wallet.low_balance` and `payment.confirmed`.
- `telemetry/application/wiring.py`: registers jobs and consumers.
- `telemetry/infrastructure/reading_writer.py`: `UnnestReadingWriter`, `CopyReadingWriter`, `AutoReadingWriter`.
- `telemetry/infrastructure/queries.py`: raw SQL for previous-reading lookup, aggregation, alert lists.
- `telemetry/api/serializers.py`: `TelemetryBatchEnvelopeSerializer`, `TelemetryBatchResultSerializer`, `AlertSerializer`, `AlertActionSerializer`, `AlertRuleSerializer`, `AlertRuleUpdateSerializer`.
- `telemetry/api/views.py`: `TelemetryBatchView` (ingest pool), `AlertViewSet`, `AlertRuleViewSet` (api pool).
- `telemetry/api/urls.py`, `telemetry/api/urls_ingest.py`: route files mounted by `hub.urls.api` and `hub.urls.telemetry`.
- `tools/loadtest/telemetry_storm.js`: k6 storm scenario (S2 of `docs/09-devops-sre.md`).
- Tests: `tests/unit/telemetry/test_validation.py`, `test_monotonic.py`, `test_alert_rules.py`; `tests/integration/telemetry/test_ingest_idempotency.py`, `test_reading_writer.py`, `test_ingest_api.py`, `test_aggregates.py`, `test_alert_engine.py`, `test_alert_api.py`, `test_partitions.py`, `test_reconnection_storm.py`.

Modify:

- `meters/contracts.py`, `meters/application/directory.py`, `meters/infrastructure/queries.py`: add the `MeterDirectory` methods listed under Interfaces.
- `meters/api/gateway.py`: make `gatewayHeartbeat` call `MeterDirectory.record_heartbeat` if slice 06 did not already.
- `telemetry/contracts.py`, `telemetry/events.py`: add the contracts and events below.
- `hub/bootstrap.py`: wire `WalletBalanceReader` to `LedgerService.balance`.
- `hub/settings/ingest.py`: `DATA_UPLOAD_MAX_MEMORY_SIZE = 262144`, telemetry settings.
- `Makefile`, `tools/verify/slice_08.sh`: verify target.

## Interfaces

**Consumes** (earlier slices):

```python
# identity.contracts
class Principal: kind: str; scopes: frozenset[str]; site_ids: frozenset[UUID]; subject_id: UUID   # kind == "gateway"
# shared.ports
class Clock(Protocol): def now(self) -> datetime: ...
class IdGenerator(Protocol): def new_id(self) -> UUID: ...
# shared.uow / jobs: UnitOfWork, OutboxWriter.add(event), JobRegistry.register_cron / register_consumer
# meters.contracts (existing)
class MeterDirectory(Protocol):
    def resolve(self, device_ref: str, *, site_ids: frozenset[UUID]) -> MeterRef | None: ...
    def mark_seen(self, meter_id: UUID, at: datetime) -> None: ...
# ledger.contracts: LedgerService.balance(account: AccountRef) -> Money   (only through the bootstrap adapter)
```

**Produces** (this slice):

```python
# meters/contracts.py additions (module meters owns the tables)
@dataclass(frozen=True, slots=True)
class MeterRef: meter_id: UUID; site_id: UUID; gateway_id: UUID | None; customer_id: UUID | None; status: str
class MeterDirectory(Protocol):
    def resolve_many(self, serials: Sequence[str], *, site_ids: frozenset[UUID]) -> Mapping[str, MeterLookup]: ...
    def mark_seen_bulk(self, seen: Mapping[UUID, datetime]) -> None: ...        # GREATEST(last_seen_at), GREATEST(last_reading_at)
    def meter_for_customer(self, customer_id: UUID) -> MeterRef | None: ...
    def silent_meters(self, *, older_than: timedelta, now: datetime, limit: int) -> list[SilentMeter]: ...
    def silent_gateways(self, *, older_than: timedelta, now: datetime) -> list[SilentGateway]: ...
    def record_heartbeat(self, gateway_id: UUID, at: datetime, firmware: str | None) -> None: ...
    def resolve_gateway(self, name: str, *, site_ids: frozenset[UUID]) -> GatewayRef | None: ...

# telemetry/contracts.py
@dataclass(frozen=True, slots=True)
class IngestResult: batch_id: UUID; accepted: int; duplicates: int; late: int; rejected: int
                    rejected_items: list[RejectedItem]; replayed_batch: bool
class TelemetryService(Protocol):
    def ingest_batch(self, batch: ReadingBatch, *, gateway: Principal, source_ip: str) -> IngestResult: ...
    def usage(self, meter_id: UUID, start: datetime, end: datetime, grain: Literal["hour", "day"]) -> list[UsagePoint]: ...
class AlertService(Protocol):
    def acknowledge(self, alert_id: UUID, *, actor: Principal, note: str | None, uow: UnitOfWork) -> AlertView: ...
    def resolve(self, alert_id: UUID, *, actor: Principal, note: str | None, uow: UnitOfWork) -> AlertView: ...

# telemetry/application/ports.py
class ReadingWriter(Protocol):
    def insert(self, rows: Sequence[ReadingRow]) -> int: ...      # rows actually inserted (first write wins)
class WalletBalanceReader(Protocol):
    def balance_minor(self, customer_id: UUID, currency: str) -> int: ...
```

**Events produced** (names and fields):

- `telemetry.batch_ingested`: `batch_id, gateway_id, accepted, duplicates, rejected` (summary only).
- `telemetry.heartbeat_missed`: `meter_id, last_reading_at, threshold_minutes`.
- `consumption.anomaly_detected`: `meter_id, customer_id, rule_id, observed_wh, expected_wh, window_start, window_end`.
- `alert.raised`: `alert_id, kind, site_id, meter_id | null, severity`.
- `alert.updated`: `alert_id, kind, site_id, meter_id | null, severity, status` (acknowledge, resolve, auto-resolve); slice 09 turns it into the SSE event of the same name.

**Quality bit flags** stored in `meter_readings.quality`: 1 tamper, 2 overcurrent, 4 low_battery, 8 relay_open, 16 register_decrease, 32 register_jump. Readings with bit 16 or 32 are excluded from every aggregate and alert input.

## Tasks

### Task 08.1: Migration, partitions and reading validation

- [ ] **Step 1: failing tests.** `tests/unit/telemetry/test_validation.py`: `test_valid_reading_passes`, `test_future_beyond_five_minutes_rejected` (`recorded_at = now + 301 s` gives code `in_future`; `now + 300 s` passes), `test_older_than_seven_days_rejected` (`now - 7 d - 1 s` gives `too_old`; `now - 7 d` passes), `test_late_counts_only_over_one_hour`, `test_negative_energy_is_invalid_value`, `test_bad_item_does_not_fail_batch` (999 good, 1 bad at index 417: `rejected_items == [RejectedItem(417, "invalid_value", ...)]`). `tests/integration/telemetry/test_partitions.py`: `test_ensure_partitions_idempotent` (second call returns no rows), `test_default_partition_stays_empty`. `tests/integration/telemetry/test_ingest_idempotency.py::test_month_boundary_and_future_skew` (stub writer; asserts partition name `meter_readings_YYYY_MM` of the stored row).
- [ ] **Step 2: run.** `cd backend && uv run pytest tests/unit/telemetry/test_validation.py -q`. Expected: ImportError for `hub.telemetry.domain.validation`.
- [ ] **Step 3: implement.** `def validate_readings(readings: Sequence[Mapping], *, now: datetime) -> ValidationOutcome` in `telemetry/domain/validation.py`: one hand-written loop, no serializer per row, returns `valid: list[ValidReading]` (with original index) and `rejected_items`. Window is `now - 7 days <= recorded_at <= now + 5 minutes`; `late` flag when `recorded_at < now - 1 hour`. Flags map to quality bits above; `relay_open` also sets `relay_closed = False`. Write the SQL migration: `meter_hourly_usage (meter_id, hour_start, energy_wh, readings_count, max_power_w, min_voltage_mv)` primary key `(meter_id, hour_start)`; `telemetry_dirty_hours (meter_id, hour_start)` primary key both; grant `INSERT` on the dirty table and `SELECT` on both to `hub_ingest`, `SELECT, INSERT, UPDATE, DELETE` on both to `hub_worker`, `SELECT` to `hub_api`; `GRANT TEMPORARY ON DATABASE` to `hub_ingest`. Add the `RunSQL` migration. Schedule `ensure_partitions` daily in `wiring.py` calling `SELECT * FROM hub.ensure_meter_reading_partitions(2, 3)` through `hub_worker`.
- [ ] **Step 4: run until green.** `uv run pytest tests/unit/telemetry tests/integration/telemetry/test_partitions.py -q`; then `make contracts`.
- [ ] **Step 5: commit.** `feat(telemetry): validate readings and add hourly aggregate tables`

### Task 08.2: Reading writer, batch idempotency and monotonic check (spike S08-1)

- [ ] **Step 1: failing tests.** `tests/unit/telemetry/test_monotonic.py`: `test_decrease_sets_bit_16`, `test_jump_over_max_power_sets_bit_32` (`delta_wh > HUB_TELEMETRY_MAX_POWER_W * hours`, default 20,000 W), `test_equal_value_is_fine`. `tests/integration/telemetry/test_reading_writer.py`: `test_unnest_and_copy_produce_identical_rows` (same 1,000 rows through both writers into two meters, compare), `test_copy_path_survives_duplicates_and_overlap` (readings 1-100 then 50-150: 150 rows, second call returns 50), `test_second_write_of_same_key_keeps_first_value` (value differs, stored value is the first). `test_ingest_idempotency.py`: `test_same_batch_sent_ten_times_stores_once` (TC-FR-06-1; ninth and tenth responses have `replayed_batch=True`), `test_same_batch_id_different_body_returns_409`, `test_unknown_meter_and_foreign_site_rejected_per_reading` (TC-FR-06-6; response contains only the codes), `test_decreasing_energy_is_flagged_not_dropped` (TC-FR-06-9: row stored with quality bit 16, `meter_state.last_cum_energy_wh` unchanged), `test_rollback_leaves_no_batch_row` (writer raises, `reading_batches` empty so a retry works).
- [ ] **Step 2: run.** `uv run pytest tests/integration/telemetry/test_reading_writer.py -q`. Expected: ImportError for `reading_writer`.
- [ ] **Step 3: implement.** `UnnestReadingWriter.insert` runs the single statement of `docs/04-data-model.md` section 10.8 and returns `cursor.rowcount`. `CopyReadingWriter.insert` runs inside the caller's transaction: `CREATE TEMP TABLE _stage (LIKE hub.meter_readings INCLUDING DEFAULTS) ON COMMIT DROP`, `cursor.copy("COPY _stage (...) FROM STDIN")` in chunks of 5,000, then `INSERT INTO hub.meter_readings (...) SELECT DISTINCT ON (meter_id, read_at) ... FROM _stage ORDER BY meter_id, read_at, ctid ON CONFLICT (meter_id, read_at) DO NOTHING` and returns the rowcount. `AutoReadingWriter` picks COPY when `len(rows) >= HUB_TELEMETRY_COPY_MIN_ROWS`. Run the spike once with `pytest -k bench --benchmark-only`, record the numbers in `docs/plan/progress.md`, keep the default unless COPY is slower at 200 rows. `IngestBatch.__call__(batch, gateway, source_ip)` inside `with uow:`: resolve the gateway by `MeterDirectory.resolve_gateway(batch.gateway_id, site_ids=gateway.site_ids)` (none: 403 `forbidden`); compute `payload_sha256` over the canonical body; `INSERT INTO reading_batches ... ON CONFLICT (gateway_id, batch_key) DO NOTHING RETURNING 1`; on conflict read the stored row, a different hash raises `ConflictError("batch_id_reuse")` (409), the same hash returns the stored counts with `replayed_batch=True`; validate, resolve meters with `resolve_many`, fetch the previous reading per meter (one `LATERAL` query bounded on both sides of `read_at`, earliest batch time minus 8 days), run `monotonic.check`, sort per meter, call the writer, `UPDATE reading_batches SET inserted_count`, call `mark_seen_bulk` with the highest unflagged `read_at` per meter, insert `telemetry_dirty_hours` for every touched `(meter, hour)` with `ON CONFLICT DO NOTHING`, stage `telemetry.batch_ingested`. Duplicates are `len(valid) - inserted`.
- [ ] **Step 4: run until green.** `uv run pytest tests/unit/telemetry tests/integration/telemetry -q`.
- [ ] **Step 5: commit.** `feat(telemetry): idempotent batch ingest with unnest and copy writers`

### Task 08.3: HTTP ingest endpoint, size limits and back-pressure

- [ ] **Step 1: failing tests.** `tests/integration/telemetry/test_ingest_api.py`: `test_post_batch_returns_202_with_counts` (example body of `ingestTelemetryBatch`; envelope `{"data": {...}}`), `test_1001_readings_returns_413_and_stores_nothing` and `test_1000_readings_accepted` (TC-FR-06-7), `test_body_one_byte_over_256kb_returns_413` (build a valid 1,000-reading body padded to exactly 262,144 and 262,145 bytes), `test_malformed_json_returns_400`, `test_one_bad_reading_in_1000_reported_by_index` (TC-FR-06-5), `test_late_data_lands_in_right_partition` (6 hours, 6 days accepted; 8 days `too_old`; TC-FR-06-3, TC-FR-06-8), `test_out_of_order_and_future_partial_success` (TC-FR-06-4), `test_overlapping_batches_store_union` (TC-FR-06-2), `test_inflight_cap_returns_503_with_retry_after` (cap set to 1, second concurrent request gets 503 and `30 <= Retry-After <= 90`), `test_gateway_key_without_scope_gets_403`, `test_response_matches_openapi` (schemathesis for `ingestTelemetryBatch`).
- [ ] **Step 2: run.** `uv run pytest tests/integration/telemetry/test_ingest_api.py -q`. Expected: 404 on the route.
- [ ] **Step 3: implement.** `TelemetryBatchView(GenericAPIView)` decorated `@operation("ingestTelemetryBatch")`, `authentication_classes = [GatewayKeyAuthentication]`, JSON parser only. Reject by `Content-Length` and by actual bytes read above 262,144 with `PayloadTooLarge` (413 `payload_too_large`) before parsing; `TelemetryBatchEnvelopeSerializer` validates `gateway_id`, `batch_id`, `sent_at`, and that `readings` is a list of 1 to 1,000 mappings, without validating items. A process-wide `BoundedSemaphore(HUB_TELEMETRY_MAX_INFLIGHT, default 64)` acquired non-blocking; on failure or when a DB pool wait over 200 ms is reported by the connection wrapper, raise `ServiceUnavailable` with `Retry-After = random.randint(30, 90)`. Never log the body. Mount in `urls_ingest.py` under `/api/v1/telemetry/batches`; the view is absent from `hub.urls.api`.
- [ ] **Step 4: run until green.** same file, then `uv run pytest tests/contract -k telemetry -q`.
- [ ] **Step 5: commit.** `feat(telemetry): POST /telemetry/batches with size limits and shedding`

### Task 08.4: Heartbeat, aggregates and meter state

- [ ] **Step 1: failing tests.** `tests/integration/telemetry/test_aggregates.py`: `test_hourly_rollup_energy_is_delta_of_register` (readings 100, 180 at 08:00 and 08:45, previous hour closes at 60: hour 08 `energy_wh = 120`), `test_flagged_readings_excluded_from_rollup`, `test_late_reading_recomputes_only_its_hour` (dirty marker cleared, other hours untouched, `computed_at` unchanged), `test_daily_rollup_from_hourly`, `test_dirty_marker_survives_job_crash`. `tests/integration/telemetry/test_ingest_api.py::test_heartbeat_updates_gateway` (`gatewayHeartbeat` sets `gateways.last_heartbeat_at` to the server clock, returns `server_time` and `next_heartbeat_seconds = 300`, ignores `sent_at` skew).
- [ ] **Step 2: run.** `uv run pytest tests/integration/telemetry/test_aggregates.py -q`. Expected: ImportError for `jobs.aggregate_hourly`.
- [ ] **Step 3: implement.** `jobs.aggregate_hourly(ctx)` every minute: claim up to 5,000 dirty rows with `FOR UPDATE SKIP LOCKED`, recompute each `(meter, hour)` by one `INSERT ... SELECT ... ON CONFLICT (meter_id, hour_start) DO UPDATE` where `energy_wh = last_cum_in_hour - last_cum_before_hour` (first reading of the hour when none before), bounded on `read_at` both sides, excluding `quality & 48 <> 0`; delete the claimed markers in the same transaction; then recompute `meter_daily_usage` for the touched days from hourly rows (`INSERT ... ON CONFLICT DO UPDATE`). Add `MeterDirectory.record_heartbeat` and `resolve_gateway` implementations (raw SQL; `hub_ingest` may update `last_heartbeat_at`, `firmware`). `purge_batches` hourly deletes `reading_batches` older than 7 days in 5,000-row batches.
- [ ] **Step 4: run until green.** `uv run pytest tests/integration/telemetry/test_aggregates.py tests/integration/meters -q`.
- [ ] **Step 5: commit.** `feat(telemetry): hourly and daily aggregates, gateway heartbeat`

### Task 08.5: Alert engine (offline, low balance, anomaly)

- [ ] **Step 1: failing tests.** `tests/unit/telemetry/test_alert_rules.py`: `test_meter_silent_exactly_90_minutes_is_not_offline_91_is` (TC-FR-07-1), `test_gateway_silent_exactly_15_minutes_is_not_offline`, `test_flapping_meter_is_bounded` (unit: debounce needs 2 healthy checks, so ten silent/active flips produce at most 2 alerts). `tests/integration/telemetry/test_alert_engine.py`: `test_meter_offline_alert_opened_once` (run the scan 5 times, one row, `details.last_seen_at` updated, one `alert.raised` outbox row), `test_site_offline_suppresses_meter_alerts` (TC-FR-07-4: 300 silent meters at a site whose gateway is silent 16 minutes give one `site_offline` and no `meter_offline`), `test_auto_resolve_after_two_healthy_checks` (one healthy check keeps it open, second resolves and emits `alert.updated`), `test_late_reading_does_not_resurrect_alert` (TC-FR-07-2: after resolve, ingest a reading older than `last_seen_at`, scan: no new alert, `meter_state.last_seen_at` unchanged), `test_maintenance_rule_disabled_suppresses` (TC-FR-07-3), `test_low_balance_event_opens_alert_and_dedupes` (two `wallet.low_balance` events, one alert), `test_low_balance_resolves_when_balance_recovers` (fake `WalletBalanceReader`), `test_spike_alert_over_three_times_median` and `test_register_decrease_alert` (subtype in `details.subtype`), `test_anomaly_not_duplicated_for_same_window`.
- [ ] **Step 2: run.** `uv run pytest tests/unit/telemetry/test_alert_rules.py -q`. Expected: ImportError for `domain.alert_rules`.
- [ ] **Step 3: implement.** `domain/alert_rules.py`: `is_meter_silent(last_seen, now, threshold) = last_seen < now - threshold`, same shape for gateways; `next_healthy_count(condition_true, current) -> (count, resolve: bool)`. `AlertEngine.raise_alert(kind, site_id, meter_id, gateway_id, customer_id, severity, dedup_key, details)` runs `INSERT INTO hub.alerts (...) ON CONFLICT (dedup_key) WHERE status <> 'resolved' DO UPDATE SET details = alerts.details || jsonb_build_object('last_seen_at', %s, 'healthy_checks', 0) RETURNING (xmax = 0) AS inserted`; only an insert stages `alert.raised`. Dedup keys: `meter_offline:{meter_id}`, `site_offline:{gateway_id}`, `low_balance:{customer_id}`, `anomalous_consumption:{meter_id}:{subtype}`. `jobs.scan_heartbeats` (cron every 60 s, singleton) reads thresholds from the matching enabled `alert_rules` row (site-specific over default; disabled means suppressed), calls `MeterDirectory.silent_gateways` then `silent_meters` for meters whose site has no open `site_offline`, emits `telemetry.heartbeat_missed` once per `(meter_id, last_reading_at)`, opens alerts, and runs the healthy-check pass for open offline alerts (condition cleared increments `healthy_checks`; at 2 set `status='resolved'`, `resolved_at`, stage `alert.updated`). Resolution never uses reading arrival time, only `last_seen_at`, which only moves forward. `handlers.on_wallet_low_balance` opens the alert using `MeterDirectory.meter_for_customer` (no meter: skip, log id only); `handlers.on_payment_confirmed` enqueues a re-check 60 s later through `JobQueue.enqueue` with `dedupe_key`; the re-check compares `WalletBalanceReader.balance_minor` with the rule threshold. `jobs.detect_anomalies` every 15 minutes: spike when the last complete hour in `meter_hourly_usage` exceeds `threshold_value` percent of `percentile_cont(0.5)` of the same UTC hour over 28 days (skip when fewer than 7 samples); register decrease from readings with bit 16 in the last 2 hours; before opening, skip when any alert for the meter and subtype (any status) has `details.window_end >= window_end`; stage `consumption.anomaly_detected` and `alert.raised`. Wire all in `wiring.py` and the bootstrap adapter.
- [ ] **Step 4: run until green.** `uv run pytest tests/unit/telemetry tests/integration/telemetry/test_alert_engine.py -q`.
- [ ] **Step 5: commit.** `feat(telemetry): offline, low-balance and anomaly alerts with dedupe and auto-resolve`

### Task 08.6: Alert and rule operations on hub-api

- [ ] **Step 1: failing tests.** `tests/integration/telemetry/test_alert_api.py`: `test_list_alerts_filters_and_site_scope` (an engineer assigned to site A never sees site B alerts; `kind`, `severity`, `status`, `sort=-opened_at`), `test_acknowledge_sets_actor_and_time_and_emits_updated`, `test_acknowledge_twice_returns_409`, `test_resolve_from_open_and_from_acknowledged`, `test_resolve_resolved_returns_409`, `test_support_agent_gets_403_and_audit_row` (AC-FR-07-5), `test_rule_update_validates_params` (`threshold_value <= 0` gives 422), `test_rule_update_writes_audit_and_bumps_version`, `test_manual_resolve_while_condition_persists_reopens_next_scan`, `test_alert_openapi_conformance` (schemathesis for the five operations).
- [ ] **Step 2: run.** `uv run pytest tests/integration/telemetry/test_alert_api.py -q`. Expected: 404 on `/api/v1/alerts`.
- [ ] **Step 3: implement.** `AlertViewSet` with `@operation("listAlerts" | "getAlert" | "acknowledgeAlert" | "resolveAlert")` and `AlertRuleViewSet` (`listAlertRules`, `updateAlertRule`); list queries use the `replica` alias through `shared.db.read_alias()` and filter with `hub.site_visible(site_id)`; transitions run `UPDATE alerts SET status=..., acknowledged_by=..., acknowledged_at=... WHERE id=%s AND status=%s RETURNING ...` (zero rows: re-read, 404 or 409 `invalid_transition`) inside `with uow:`, stage `alert.updated`, and call `AuditLog.record`. `AlertRuleUpdateSerializer.params` accepts only `{"threshold_value": positive int}`; the unit stays fixed per kind. Serializer enums come from constants compared with the schema by the alignment test.
- [ ] **Step 4: run until green.** same file plus `make contracts`.
- [ ] **Step 5: commit.** `feat(telemetry): alert list, acknowledge, resolve and rule update operations`

### Task 08.7: Reconnection storm test and verify target

- [ ] **Step 1: failing test.** `tests/integration/telemetry/test_reconnection_storm.py::test_reconnection_storm_loses_nothing`: 300 simulated gateways, 24 hours of backlog each (96 readings per meter, 4 meters per gateway, so batches of 384), started within 2 seconds with random jitter, 10% of readings late, 10% duplicates, 32 client threads against the ingest app with `HUB_TELEMETRY_MAX_INFLIGHT=16`. Assertions: every response is 202 or 503 with `Retry-After`; clients resend identical bodies on 503 until 202; `count(meter_readings) == distinct (meter, read_at)` generated; zero rows in `meter_readings_default`; `sum(inserted_count) == row count`; no telemetry transaction holds a lock on `top_ups` (checked in `pg_locks`). Add `tools/loadtest/telemetry_storm.js` with the S2 shape of `docs/09-devops-sre.md` section 7.2 and thresholds as written there.
- [ ] **Step 2: run.** `uv run pytest tests/integration/telemetry/test_reconnection_storm.py -q`. Expected: fails on a missing fixture `storm_clients`; add it in `tests/integration/telemetry/conftest.py`.
- [ ] **Step 3: implement.** Fixtures only, plus the `tools/verify/slice_08.sh` script and Makefile wiring. If the storm shows 5xx other than 503, fix the writer, not the test.
- [ ] **Step 4: run until green.** Test passes; `make verify SLICE=08`.
- [ ] **Step 5: commit.** `test(telemetry): reconnection storm scenario and slice 08 verification`

## Verification

`make verify SLICE=08` runs, from the repository root:

1. `make lint`: clean (ruff, mypy, import-linter including the telemetry row of the dependency table).
2. `cd backend && uv run pytest tests/unit/telemetry -q`: all pass.
3. `cd backend && uv run pytest tests/integration/telemetry -q`: all pass, including the storm test.
4. `cd backend && uv run pytest tests/contract -k "telemetry or alert" -q`: schemathesis passes for `ingestTelemetryBatch`, `gatewayHeartbeat`, the four alert operations and the two rule operations.
5. `make contracts`: alignment, schema and traceability checks green.
6. `cd backend && uv run python manage.py check_db_alignment`: no drift (new tables mapped as unmanaged models).
7. `select count(*) from hub.meter_readings_default` on the test database: 0.

## Demo script

1. `make up && make seed`. Observe all services healthy.
2. Run `uv run python tools/meter_simulator/run.py --gateways 3 --duplicates 10 --late 10`. Observe counts with `duplicates > 0` and `late > 0`, no errors.
3. Send the same batch twice with curl and a gateway key from the simulator. Observe the second answer has `replayed_batch: true` and the row count is unchanged.
4. Stop the simulator for one site, wait 16 minutes (or advance the compose clock with `make clock-skip MIN=16`). Observe exactly one `site_offline` alert in `GET /api/v1/alerts` and no meter alerts for that site.
5. Restart it; observe the alert auto-resolves after two scans.
6. Acknowledge an alert as an operations engineer; as a support agent observe 403.
7. Run `k6 run tools/loadtest/telemetry_storm.js` at 10% scale; observe the thresholds pass.

## Done criteria

Requirements covered: FR-06, FR-07. Test cases implemented and passing: TC-FR-06-1, TC-FR-06-2, TC-FR-06-3, TC-FR-06-4, TC-FR-06-5, TC-FR-06-6, TC-FR-06-7, TC-FR-06-8, TC-FR-06-9, TC-FR-07-1, TC-FR-07-2, TC-FR-07-3, TC-FR-07-4. TC-NFR-03-1 is delivered as the k6 script and the storm test; its one-hour nightly run belongs to slice 17.

## Plan notes for the tech lead

1. **COPY versus unnest.** The assignment, ADR-0010 and `docs/03` section 6.3 call for COPY; `docs/04-data-model.md` section 10.8 says COPY is not used on ingest because `hub_ingest` has no TEMP right. Followed ADR-0010: COPY into a temporary stage for batches of 200 rows or more, with a numbered migration granting `TEMPORARY` to `hub_ingest` only. `db/schema.sql` is not edited.
2. **Hourly aggregate missing.** `docs/03` section 1.3 says `meter_daily_usage` is "the hourly aggregate per 04"; neither `db/schema.sql` nor `04` defines an hourly table or `dirty_hour` markers. Added `meter_hourly_usage` and `telemetry_dirty_hours` by migration `0080`.
3. **Status code of ingest.** `docs/03` section 6.3 says 200 `duplicate`; `docs/02` 2.c says 200; `api/openapi.yaml` says 202 with `replayed_batch`. Followed the OpenAPI file.
4. **Reason codes.** The PRD says `future_timestamp` and `duplicate`; OpenAPI says `in_future` and `duplicates`. Followed OpenAPI.
5. **Existence leak.** OpenAPI requires `site_not_permitted` for a meter of another site while TC-FR-06-6 demands no existence leak; both codes together reveal which serials exist elsewhere. Followed OpenAPI; consider collapsing to `unknown_meter`.
6. **Columns without a home.** `credit_remaining_wh` and `seq` have no column in `meter_readings`; they are validated and dropped. The OpenAPI `gateway_id` string has no column either; the plan resolves the gateway by `(name, bound site)` rather than `gateways.api_key_id`.
7. **Low-balance rule.** PRD AC-FR-07-2 (two days of average consumption) differs from the seeded `alert_rules` row (500 minor units). Followed the schema. `alert.updated` and `telemetry` as a consumer of `wallet.low_balance` are not in the `docs/03` event catalogue and are added here.
8. **Heartbeat interval and routes.** `docs/03` places the heartbeat route in slice 06; the assignment puts the semantics here. Slice 06 keeps the route; this slice adds `record_heartbeat`. `docs/02` and `docs/03` say SSE heartbeats are 15 s; OpenAPI and `docs/09` say 20 s (matters to slice 09).
9. **Administrators.** PRD AC-FR-07-5 says admins may acknowledge alerts; `api/permissions.yaml` lists `operations_engineer` only. Followed the permissions file.
