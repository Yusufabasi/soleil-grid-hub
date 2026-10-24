# Slice 05: Outbox relay, consumers and the job framework

- **Goal:** the in-house job framework in `hub.shared.jobs`: the relay from `hub.outbox` to Redis Streams, consumer groups with `consumer_inbox`, retry with backoff and dead-letter streams `dlq.<stream>`, the 10-minute replay window after Redis loss, generic sweepers, cron with an advisory lock, delayed jobs, and lag metrics. The three management commands `run_relay`, `run_worker` and `run_scheduler` stop being no-op loops.
- **Depends on:** slice 00 (`UnitOfWork`, `DomainEvent`, metrics helpers, no-op commands, settings), slice 04 (a real `payment.confirmed` producer for the demo and the replay job), slice 03 only through the same outbox. Consumers for tokens and notifications arrive in slices 06 and 07.
- **Requirements served:** NFR-01 (enabler: the payment path stays up while Redis or workers fail), FR-19 (outbox atomicity, reliable delivery; the notification consumers are slice 07).
- **Size:** M, 7 days for a cheaper agent.
- **Companion files:** none (no UI).
- **Spikes or open items:** none business-level. Defaults chosen and made settings: replay window 600 s, claim timeout 60 s, 5 deliveries before dead-letter, backoff base 1 s factor 4. `LISTEN/NOTIFY` is not built in v1 (Plan note 2); the relay polls every 500 ms.

## Read first

- `docs/00-shared-context.md#7.5 Data, infrastructure and operations` (D-27) and `#7.7 Stack and delivery model` (D-42, D-43)
- `docs/adr/0003-outbox-and-redis-streams.md`, `docs/adr/0018-per-deployable-database-roles.md`
- `docs/03-code-architecture.md#4. Domain event catalogue` (transport paragraph and the stream-family table), `#7.5 Background jobs and scheduling`, `#7.4 Database access`
- `docs/04-data-model.md#5.4 Outbox, consumer_inbox and the replay window (D-27)` and `db/schema.sql` tables `hub.outbox`, `hub.consumer_inbox`
- `docs/02-system-architecture.md#5.2 Outbox and relay` and `#5.3 Consumer idempotency and the Redis-loss gap`
- `docs/10-test-strategy.md#2.3 Outbox and relay`
- `docs/plan/slice-00-walking-skeleton.md` Interfaces (outbox, metrics, commands)

## Global constraints

- The outbox is the source of truth; Redis loses nothing that the outbox cannot re-publish. Correctness never depends on Redis (ADR-0003).
- Relay claim is raw SQL `UPDATE ... RETURNING` with `FOR UPDATE SKIP LOCKED`, claim timeout 60 s, `ORDER BY available_at, id`; never the ORM (docs/04 5.4).
- Stream families (exact): `events.payments`, `events.tokens`, `events.support`, `events.notifications`, `events.alerts`; dead letters `dlq.<stream>`; `XADD ... MAXLEN ~ 500000`.
- Delivery is at-least-once. Every consumer writes `(consumer, event_id)` to `consumer_inbox` in the same transaction as its effect, then `XACK`s after commit.
- Retry: 1 s base, factor 4, jitter; after 5 deliveries the entry moves to `dlq.<stream>` and raises an alert.
- Replay window: 10 minutes. Business sweepers cover anything older.
- `consumer_inbox` rows purged after 14 days; outbox published rows after 7 days, dead rows after 90 days.
- Cron jobs are singletons by advisory lock and must be idempotent; the relay lag alert threshold is 30 s.
- The worker role is `hub_worker`: INSERT, UPDATE, DELETE on `outbox`; INSERT, DELETE on `consumer_inbox`. No new grants in this slice.
- No `/metrics` route (no endpoint outside `api/openapi.yaml`): metrics use the slice 00 `start_metrics_server` on the internal port.
- Never log event payloads (they can hold ids and amounts); log event id, name and aggregate id only.

## Review focus

1. **Effect committed, `XACK` lost.** The redelivery must be a no-op, and a slow handler reclaimed by another consumer must not double the effect. Pinned by `test_redelivery_after_commit_is_noop` and `test_concurrent_duplicate_delivery_single_effect` (Task 05.4).
2. **Redis flushed or failed over after rows were marked published.** Without the replay window those events are lost forever. Pinned by `test_replay_window_republishes_after_run_id_change` and `test_stream_loss_recovered_from_outbox` (Task 05.3).
3. **Redis outage must not dead-letter healthy rows.** A 10-minute outage once burned the attempts counter of every claimed row in a naive design. Pinned by `test_redis_down_does_not_kill_outbox_rows` (Task 05.2).
4. **Three relays must not publish one aggregate's events out of order or twice.** Pinned by `test_three_relays_no_double_claim_and_order_per_aggregate` (Task 05.2).
5. **A poison message must not loop forever or block its stream.** Pinned by `test_poison_goes_to_dlq_after_five_deliveries` and `test_replay_dlq_requeues_after_fix` (Task 05.4).

## Files

Paths relative to `backend/src/hub/shared/` (tests under `backend/tests/`).

- `jobs/envelope.py`: `EventEnvelope`, `from_outbox_row`, `to_stream_fields`, `from_stream_fields`.
- `jobs/routing.py`: `stream_for(event_type)` by prefix table; `dlq_for(stream)`.
- `jobs/backoff.py`: `backoff_seconds(delivery_count, rng)`, `relay_backoff`.
- `jobs/errors.py`: `Retryable`, `Permanent` exceptions and `classify(exc)`.
- `jobs/registry.py`: `JobRegistry` implementation (`register_consumer`, `register_cron`, `register_poller`, `register_sweeper`), `EventHandler` Protocol, module wiring discovery.
- `jobs/outbox_repository.py`: `claim_batch`, `mark_published`, `release_claims`, `mark_dead`, `reemit`, `purge`, `oldest_unpublished_age`; `jobs/inbox_repository.py`: `try_insert`, `purge`.
- `jobs/relay.py`: `Relay.run_once`, `Relay.run_forever`, `RedisLossDetector`.
- `jobs/consumer.py`: `ConsumerLoop` (`XREADGROUP`, reclaim, dispatch), `jobs/dlq.py`: `move_to_dlq`, `replay`.
- `jobs/cron.py`: `CronSpec` (5-field parser: `*`, lists, ranges, `*/n`), `jobs/scheduler.py`: `Scheduler`, `AdvisoryLock`.
- `jobs/queue.py`: `RedisJobQueue` (delayed and ad hoc jobs on a sorted set), `NullJobQueue`.
- `jobs/sweepers.py`: `outbox_purge`, `inbox_purge`, `idempotency_purge`, `dlq_depth_probe`, `lag_probe`.
- `jobs/metrics.py`: metric names and `gauge` helper (extends slice 00 `metrics.py`).
- `management/commands/run_relay.py`, `run_worker.py`, `run_scheduler.py` (modify), `replay_dlq.py`, `replay_outbox.py` (new).
- `infra/observability/prometheus/rules/jobs.yml`: alert rules `OutboxLagHigh` (above 30 s for 2 minutes), `DlqNotEmpty`, `ConsumerLagHigh`.
- Tests: `unit/shared/jobs/` (`test_routing.py`, `test_backoff.py`, `test_cron.py`, `test_envelope.py`), `integration/shared/jobs/` (`test_relay.py`, `test_replay_window.py`, `test_consumer.py`, `test_dlq.py`, `test_scheduler.py`, `test_queue.py`, `test_chaos_worker_kill.py`).

## Interfaces

**Consumes:** `UnitOfWork`, `OutboxWriter`, `DomainEvent` (slice 00: the writer stores the envelope fields `event_id, name, version, occurred_at, correlation_id, causation_id` at the top level of `outbox.payload` next to the business fields; the row `id` is the event id); `hub.shared.metrics.counter/histogram/start_metrics_server`; `Clock`; settings `HUB_REDIS_URL`, `HUB_DB_WORKER_URL`.

**Produces** (later slices rely on these):

```python
# jobs/envelope.py
@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: UUID; name: str; version: int; aggregate_type: str; aggregate_id: UUID
    occurred_at: datetime; correlation_id: UUID | None; causation_id: UUID | None
    payload: Mapping[str, Any]            # business fields only
# jobs/registry.py
class EventHandler(Protocol):
    name: str                             # the consumer_inbox key, e.g. "meters.on_payment_confirmed"
    event_names: frozenset[str]           # events outside this set are acknowledged without work
    def __call__(self, event: EventEnvelope, uow: UnitOfWork) -> None: ...   # runs inside the transaction that also writes the inbox row
class JobRegistry(Protocol):              # docs/03 section 7.5, plus register_sweeper
    def register_consumer(self, *, stream: str, group: str, handler: EventHandler, concurrency: int = 1) -> None: ...
    def register_cron(self, *, name: str, cron: str, handler: JobHandler, singleton: bool = True) -> None: ...
    def register_poller(self, *, name: str, interval_s: float, handler: JobHandler) -> None: ...
    def register_sweeper(self, *, name: str, interval_s: float, handler: JobHandler) -> None: ...   # singleton poller
# jobs/errors.py
class Retryable(Exception): ...           # backoff, up to 5 deliveries
class Permanent(Exception): ...           # dead-letter at once
# jobs/routing.py
def stream_for(event_type: str) -> str: ...      # raises UnroutableEvent
# jobs/queue.py: JobQueue.enqueue(name, payload, *, delay=timedelta(0), dedupe_key=None) -> None
# jobs/outbox_repository.py
class OutboxReemitter(Protocol): def reemit(self, event_id: UUID) -> bool: ...   # clears published_at of the same row; consumers dedupe
```

Metrics: `outbox_backlog` (gauge), `outbox_oldest_unpublished_age_seconds` (gauge), `relay_published_total`, `relay_errors_total{kind}`, `relay_replay_total`, `consumer_processed_total{group,handler,outcome}`, `consumer_failed_total{group}`, `stream_lag{stream,group}` (from `XINFO GROUPS`), `stream_pending{stream,group}`, `dlq_depth{stream}`, `job_runs_total{job,outcome}`, `job_duration_seconds{job}`. Commands: `run_relay [--once]`, `run_worker --group <module> [--concurrency N] [--once]`, `run_scheduler [--once]`, `replay_dlq --stream S [--group G] [--event-id UUID] [--limit N]`, `replay_outbox --since 10m`. Settings: `HUB_RELAY_BATCH=100`, `HUB_RELAY_POLL_MS=500`, `HUB_RELAY_CLAIM_TIMEOUT_S=60`, `HUB_RELAY_MAX_ATTEMPTS=10`, `HUB_RELAY_REPLAY_WINDOW_S=600`, `HUB_STREAM_MAXLEN=500000`, `HUB_CONSUMER_BLOCK_MS=1000`, `HUB_CONSUMER_MAX_DELIVERIES=5`, `HUB_BACKOFF_BASE_S=1`, `HUB_BACKOFF_FACTOR=4`, `HUB_SCHEDULER_TICK_S=1`, `HUB_DB_ADVISORY_URL` (defaults to `HUB_DB_WORKER_URL`; set to a direct, non-PgBouncer URL where PgBouncer runs in transaction mode).

## Tasks

### Task 05.1: Envelope, routing, backoff, cron parser, error classes

- [ ] **1. Failing tests.** `unit/shared/jobs/test_routing.py`: `test_prefix_table` (parametrised: `topup.initiated`, `payment.confirmed`, `ledger.posted`, `reconciliation.exception` to `events.payments`; `token.issued`, `meter.offline`, `site.offline`, `telemetry.heartbeat_missed` to `events.tokens`; `ticket.created`, `draft.approved`, `ai.budget_exceeded`, `askdata.query_executed` to `events.support`; `notification.requested` to `events.notifications`; `alert.raised`, `wallet.low_balance`, `consumption.anomaly_detected`, `approval.granted`, `customer.erased`, `user.locked`, `api_key.revoked` to `events.alerts`), `test_unknown_prefix_raises_unroutable`, `test_dlq_name` (`dlq.events.payments`). `test_backoff.py`: `test_schedule_without_jitter` (delivery 1 to 5 gives 1, 4, 16, 64, 256 s), `test_jitter_bounded` (within plus or minus 20 percent, seeded rng), `test_relay_backoff_caps_at_60`. `test_cron.py`: `test_star_and_step` (`*/5 * * * *`), `test_lists_ranges` (`0 2,14 * * 1-5`), `test_next_after_is_strictly_later`, `test_invalid_expression_raises`, `test_matches_minute_boundary_utc`. `test_envelope.py`: `test_round_trip_through_stream_fields`, `test_missing_envelope_field_in_payload_raises`, `test_payload_excludes_envelope_keys`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/unit/shared/jobs -q`. Expected: ImportError.
- [ ] **3. Implement.** `routing.py` holds one table `PREFIXES: dict[str, str]` (the table of docs/03 section 4) and `stream_for(event_type)` splits on the first dot. `backoff.py`: `backoff_seconds(n, rng) = base * factor**(n-1)` with factor and base from settings, jitter uniform in plus or minus 20 percent. `cron.py`: `CronSpec.parse(expr)` for five fields in UTC (minute, hour, day of month, month, day of week), `next_after(dt)` by minute stepping bounded to 366 days. `envelope.py`: `from_outbox_row(row)` splits the stored payload into envelope keys and business fields; `to_stream_fields` writes flat string fields (`event_id`, `name`, `version`, `aggregate_type`, `aggregate_id`, `occurred_at`, `correlation_id`, `causation_id`, `payload` as compact JSON).
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(jobs): envelope, stream routing, backoff and cron parser`

### Task 05.2: The relay (claim, publish, mark) with ordering and outage safety

- [ ] **1. Failing tests.** `integration/shared/jobs/test_relay.py` (real PostgreSQL as `hub_worker`, real Redis 7 container): `test_publishes_row_to_correct_stream_and_marks_published`, `test_unpublished_future_available_at_is_skipped` (recall hold of D-35), `test_claim_is_skip_locked` (two sessions, no row returned twice), `test_stale_claim_older_than_60s_is_reclaimed`, `test_three_relays_no_double_claim_and_order_per_aggregate` (1,000 rows over 20 aggregates, 3 relay threads; each stream entry appears once; per aggregate the stream order equals outbox order), `test_redis_down_does_not_kill_outbox_rows` (a Redis wrapper raising `ConnectionError` for 3 s: rows stay unpublished, `attempts` unchanged after recovery, none has `dead_at`, backlog then drains), `test_unroutable_event_goes_dead_after_max_attempts` (`dead_at` set, `last_error` names the event type), `test_relay_crash_between_xadd_and_mark_publishes_twice` (fault hook: stream holds two entries, same `event_id`; TC-FR-19-2 consumer half is Task 05.4), `test_backlog_and_age_gauges`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/shared/jobs/test_relay.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `OutboxRepository.claim_batch(relay_id, limit)` executes the statement of docs/04 5.4 with one addition that keeps per-aggregate order across relays: the inner select adds `AND NOT EXISTS (SELECT 1 FROM hub.outbox o2 WHERE o2.aggregate_id = o.aggregate_id AND o2.published_at IS NULL AND o2.dead_at IS NULL AND (o2.available_at, o2.id) < (o.available_at, o.id))` so a row waits for earlier unpublished rows of its aggregate (claimed by anyone). It increments `attempts`. `Relay.run_once()`: claim, then publish with one Redis pipeline of `XADD <stream> MAXLEN ~ N * fields`, then one `UPDATE ... SET published_at = now() WHERE id = ANY(%s) AND claimed_by = %s`. On a Redis connection or timeout error, call `release_claims` which clears `claimed_by` and `claimed_at` and restores `attempts = attempts - 1`, then sleep `relay_backoff`; on an unroutable event or a serialisation error increment `last_error` and set `dead_at` once `attempts >= HUB_RELAY_MAX_ATTEMPTS`, emitting `relay_errors_total{kind}`. `run_forever` loops `run_once`, sleeping `HUB_RELAY_POLL_MS` when idle. A `FaultHooks.after_xadd` parameter exists for tests only. `run_relay` builds the relay from the container; `--once` runs one pass.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(jobs): outbox relay with ordered claims and outage-safe publishing`

### Task 05.3: Redis-loss detection, replay window and generic sweepers

- [ ] **1. Failing tests.** `integration/shared/jobs/test_replay_window.py`: `test_replay_window_republishes_after_run_id_change` (publish 5 rows, change the detector's remembered `run_id`: rows with `published_at` newer than 10 minutes are claimed and re-published, older ones are not), `test_stream_loss_recovered_from_outbox` (`FLUSHALL` between publish and consume: after one relay pass the consumer receives every event once in effect), `test_replay_after_connection_error_streak`, `test_replay_is_idempotent_for_consumers` (re-published events leave the inbox unchanged), `test_replay_outbox_command_since_10m`, `test_reemit_clears_published_at_of_same_row`. `test_sweepers.py`: `test_outbox_purge_batched` (published older than 7 days and dead older than 90 days deleted in batches of 1,000; recent rows untouched), `test_inbox_purge_14_days`, `test_idempotency_purge_deletes_expired_keys`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/shared/jobs/test_replay_window.py tests/integration/shared/jobs/test_sweepers.py -q`. Expected: AttributeError on `RedisLossDetector`.
- [ ] **3. Implement.** `RedisLossDetector.check() -> bool` compares `INFO server` `run_id` with the remembered value and with a sentinel key `hub:relay:sentinel` (value `run_id`, no TTL); a changed `run_id`, a missing sentinel, or a reconnect after three consecutive errors returns true and rewrites the sentinel. When true the relay runs `UPDATE hub.outbox SET published_at = NULL, claimed_by = NULL, claimed_at = NULL WHERE published_at > now() - make_interval(secs => %s) AND dead_at IS NULL` (window `HUB_RELAY_REPLAY_WINDOW_S`), increments `relay_replay_total`, and continues normally. `replay_outbox --since 10m` runs the same statement by hand. `OutboxRepository.reemit(event_id)` clears `published_at` of one row so a business sweeper can republish an event. Register sweepers in the framework (not business sweepers, which belong to the owning modules): `shared.outbox_purge` (hourly), `shared.inbox_purge` (daily 03:00), `shared.idempotency_purge` (hourly; deletes where `expires_at < now()` in batches), `shared.lag_probe` and `shared.dlq_depth_probe` (every 15 s).
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(jobs): replay window after Redis loss and framework sweepers`

### Task 05.4: Consumers, inbox, retry and dead-letter

- [ ] **1. Failing tests.** `integration/shared/jobs/test_consumer.py`: `test_handler_runs_in_transaction_with_inbox_row` (inbox row and handler effect commit together; a handler exception rolls back both), `test_redelivery_after_commit_is_noop` (commit, drop the `XACK`, redeliver: the effect counter stays 1), `test_concurrent_duplicate_delivery_single_effect` (the same entry processed by two consumers at once: one effect, one inbox row), `test_unhandled_event_name_is_acked_without_inbox_row`, `test_ack_after_commit_only` (fault between commit and `XACK` leaves the entry pending; no ack before commit), `test_xautoclaim_recovers_entries_of_a_crashed_consumer`, `test_retry_uses_backoff_schedule` (entry fails twice: reclaimed no sooner than 1 s then 4 s, injectable clock), `test_inbox_key_is_handler_name`. `test_dlq.py`: `test_poison_goes_to_dlq_after_five_deliveries` (generic `Exception` handler: after the fifth failed delivery the entry is in `dlq.events.payments` with fields `group`, `handler`, `error`, `delivery_count`, `failed_at`, and acknowledged in the source), `test_permanent_error_dead_letters_at_once`, `test_dlq_alert_metric_increments`, `test_replay_dlq_requeues_after_fix` (command re-adds to the source stream and deletes from the dlq; handler now succeeds once), `test_replay_dlq_filters_by_event_id`, `test_replay_of_full_stream_reproduces_state` (TC-FR-19-5: replay a recorded 200-event stream against a fresh inbox-free database with an idempotent counter handler: final state equals the original), `test_consumer_survives_database_reconnect`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/shared/jobs/test_consumer.py tests/integration/shared/jobs/test_dlq.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `ConsumerLoop(stream, group, handlers, concurrency)`: each thread calls `XGROUP CREATE ... MKSTREAM` (ignore `BUSYGROUP`), then loops: reclaim pass (every second: `XPENDING` extended, for entries whose idle time is at least `backoff_seconds(delivery_count)` call `XCLAIM`), then `XREADGROUP ... BLOCK HUB_CONSUMER_BLOCK_MS COUNT 10 >`. For each entry: build the envelope; for every handler whose `event_names` contains the name, run `with uow:` then `InboxRepository.try_insert(handler.name, event_id)` (raw `INSERT ... ON CONFLICT DO NOTHING`, zero rows means skip) then `handler(event, uow)`; commit; after all handlers succeed `XACK`; handlers that do not match are skipped; `close_old_connections()` after each entry. On exception: `classify`; `Permanent` or a delivery count at or above `HUB_CONSUMER_MAX_DELIVERIES` calls `move_to_dlq` (`XADD dlq.<stream>`, then `XACK`), anything else leaves the entry pending for the reclaim pass. Concurrency safety rests on the inbox primary key, not on Redis. `run_worker --group` starts one `ConsumerLoop` per registered `(stream, group)` plus pollers. `replay_dlq` implements the filters and uses `XADD` back to the source stream.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(jobs): stream consumers with inbox dedupe, backoff and dead-letter`

### Task 05.5: Scheduler, cron singleton and delayed jobs

- [ ] **1. Failing tests.** `integration/shared/jobs/test_scheduler.py`: `test_two_schedulers_one_runs_slot` (two `Scheduler` instances with a frozen clock and one cron job: after one tick the handler ran exactly once across both), `test_lock_released_when_job_finishes_or_raises`, `test_lock_lost_when_holder_connection_closes` (kill the holder session: the other instance runs the next slot), `test_non_singleton_cron_runs_on_every_instance`, `test_missed_slots_are_not_replayed_after_downtime` (clock jumps 10 minutes: one run, not ten), `test_job_exception_is_logged_and_next_slot_runs`, `test_job_runs_counted_by_outcome`. `test_queue.py`: `test_enqueue_runs_after_delay`, `test_dedupe_key_suppresses_duplicate_pending_job`, `test_job_retried_with_backoff_then_dropped_to_dlq_jobs`, `test_poller_runs_each_interval`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/shared/jobs/test_scheduler.py tests/integration/shared/jobs/test_queue.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `AdvisoryLock(name)` opens its own connection from `HUB_DB_ADVISORY_URL`, runs `SELECT pg_try_advisory_lock(hashtextextended(%s, 0))` and holds the session until `release()`; a session lock dies with its connection, which frees a crashed holder's lock. `Scheduler.tick(now)`: for each cron job whose `CronSpec` matches the current minute and which has not run for that minute in this process, singleton jobs try the lock, run the handler in a job span (`job_duration_seconds`), release; the last-run minute is also written to Redis hash `hub:cron:last` as a hint so a second replica skips a slot just finished (jobs are idempotent, so a rare double run is harmless). Downtime never replays missed minutes. `RedisJobQueue.enqueue` writes the job JSON to a sorted set scored by due time (`ZADD NX` on `dedupe_key`); the scheduler loop pops due jobs with `ZREM` returning 1 as the claim, runs the handler registered under `name`, and on exception re-enqueues with `backoff_seconds` up to 5 attempts, then moves to the Redis list `dlq.jobs`. `NullJobQueue` raises `ExternalUnavailable`. `run_scheduler` wires the loop with a `HUB_SCHEDULER_TICK_S` tick.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(jobs): cron scheduler with advisory lock and delayed jobs`

### Task 05.6: Lag metrics, alert rules, wiring and chaos test

- [ ] **1. Failing tests.** `integration/shared/jobs/test_metrics.py`: `test_oldest_unpublished_age_gauge`, `test_stream_lag_and_pending_from_xinfo`, `test_dlq_depth_gauge`. `test_chaos_worker_kill.py` (marker `chaos`): `test_kill_worker_midflight_effects_exactly_once` (500 `payment.confirmed` events through the real relay; a counter consumer in a subprocess is killed with SIGKILL twice and restarted: the effect table holds 500 rows, each once), `test_redis_restart_mid_run_loses_nothing` (Redis container restarted during the run; replay window re-publishes; final state equals 500 once). `integration/payments/test_replay_end_to_end.py`: `test_replay_webhook_event_job_runs_through_real_queue` (the slice 04 `replayWebhookEvent` enqueues a job; the scheduler loop runs `payments.replay_event`; posting exists once). `unit/shared/test_alert_rules.py`: `test_rule_files_parse_and_contain_outbox_lag_alert_at_30s`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/shared/jobs/test_metrics.py -q`. Expected: gauges missing.
- [ ] **3. Implement.** `lag_probe` computes `outbox_backlog` and `outbox_oldest_unpublished_age_seconds` (`SELECT count(*), extract(epoch from now() - min(created_at)) FROM hub.outbox WHERE published_at IS NULL AND dead_at IS NULL AND available_at <= now()`), and per stream and group reads `XINFO GROUPS` (`lag`, `pending`). Write `infra/observability/prometheus/rules/jobs.yml`. Bind `RedisJobQueue` in `bootstrap` for the worker and api containers (replacing the null queue). Add a `make demo-jobs` helper script under `tools/scripts/` printing relay lag and stream info.
- [ ] **4. Run** the metrics, replay and chaos tests until green.
- [ ] **5. Commit:** `feat(jobs): lag metrics, alert rules, queue wiring and chaos coverage`

## Verification

`make verify SLICE=05` runs from the repository root; each command must exit 0.

1. `cd backend && uv run ruff check . && uv run mypy --strict src/hub/shared && uv run lint-imports` (zero findings; `shared` imports no business module).
2. `cd backend && uv run pytest tests/unit/shared -q` (all passed).
3. `cd backend && uv run pytest tests/integration/shared/jobs -q -m "not chaos"` (all passed; needs PostgreSQL and Redis containers).
4. `cd backend && uv run pytest tests/integration/shared/jobs/test_chaos_worker_kill.py -q -m chaos` (all passed).
5. `cd backend && for i in 1 2 3; do uv run pytest tests/integration/shared/jobs/test_relay.py tests/integration/shared/jobs/test_consumer.py -q || exit 1; done` (three consecutive green runs).
6. `cd backend && uv run pytest tests/integration/payments/test_replay_end_to_end.py -q` (green).
7. `python -m pytest tests/test_alignment.py tests/test_schema_in_postgres.py -q && python tools/check_traceability.py` from the repository root (green).
8. `docker run --rm -v "$PWD/infra/observability/prometheus/rules:/r" prom/prometheus:v2.55.0 promtool check rules /r/jobs.yml` (`SUCCESS`; pin the image version in the Makefile).

## Demo script

1. `make up`; run `docker compose exec worker python manage.py run_relay --once` and observe `published 0`.
2. Create 20 top-ups confirmed through the provider stub (slice 04 demo scenario). Observe `outbox_oldest_unpublished_age_seconds` fall to under 1 s while the relay runs, and `XLEN events.payments` equal to the number of events.
3. Start `run_worker --group demo` with the demo tap handler (`HUB_JOBS_DEMO_TAP=1`, which only writes inbox rows). `XINFO GROUPS events.payments` shows `lag 0`.
4. `docker compose kill worker` during a 200-event burst, then start it again. Count inbox rows: each event exactly once.
5. `docker compose restart redis`. Within one relay pass `relay_replay_total` becomes 1 and the stream refills; inbox rows do not grow.
6. Set the tap handler to raise: after five deliveries the entry appears in `dlq.events.payments`, `dlq_depth` is 1; fix the handler and run `manage.py replay_dlq --stream events.payments`; the dlq is empty.
7. Start two `run_scheduler` processes and watch `job_runs_total{job="shared.outbox_purge"}` rise by one per slot only.

## Done criteria

Requirements: NFR-01 enabler (payment path independent of queue health, outbox recovery), FR-19 reliability mechanics.

Test cases implemented and passing: TC-FR-19-1 (framework-level atomicity test `test_outbox_row_rolls_back_with_business_row`, Task 05.2, using a throwaway aggregate), TC-FR-19-2 (05.2 and 05.4), TC-FR-19-3 (05.2, 05.3, 05.6), TC-FR-19-4 (05.4), TC-FR-19-5 (05.4), TC-FR-19-6 (05.2). TC-FR-19-7 and TC-FR-19-8 belong to slice 07.

## Plan notes for the tech lead

1. **Single relay or many.** docs/03 section 7.5 says the relay holds a Postgres advisory lock so one relay is active; docs/02 section 5.2, docs/04 section 5.4 and TC-FR-19-6 say multiple relays are safe with `SKIP LOCKED` and keep per-aggregate order. I followed docs/02, 04 and the test matrix and made order hold across relays with the `NOT EXISTS` clause (Task 05.2). No advisory lock for the relay.
2. **`LISTEN/NOTIFY`.** docs/02 and docs/03 name it as the relay wake-up, but the frozen schema has no trigger and slice 00's unit of work emits no `NOTIFY`. I poll every 500 ms (the stated backstop). Adding the notification later needs a change to slice 00's `DjangoUnitOfWork`.
3. **Session advisory locks and PgBouncer.** Transaction pooling breaks session locks, so the scheduler uses a direct connection (`HUB_DB_ADVISORY_URL`). docs/03 and D-43 define only one URL per role.
4. **Envelope fields location.** The schema's outbox has no correlation or version columns; docs/03 section 1.3 says they live in `outbox.payload`. I assume slice 00 writes them at the top level of the payload (slice 00 states "envelope plus payload"); if it nests them, only `envelope.from_outbox_row` changes.
5. **Inbox key.** docs/03 section 4 says `(consumer, event_id)` with one consumer per module; docs/02 section 2.b uses a handler name (`token_consumer`). I key by handler name so two handlers in one module dedupe independently.
6. **Retry tables differ.** docs/03 section 4 (1 s base, factor 4, five deliveries) governs stream consumers here; the notification sender uses the PRD's 1 minute doubling with 8 attempts (slice 07 owns it).
7. **Event not in the catalogue.** The SQL function `reverse_ledger_transaction` emits `ledger.chargeback_opened`; the catalogue lists no such event. It routes to `events.payments` by prefix; docs/03 section 4 needs a row.
8. **Business sweepers** (confirmed top-up without a token after 5 minutes, approved drafts unsent after 10 minutes) belong to slices 06 and 12; this slice supplies `register_sweeper` and `reemit` only.
9. **Circular dependency.** Slice 04 needs the real `JobQueue` for webhook replay while this slice depends on slice 04 for a producer; resolved with a fake in slice 04 and Task 05.6 here.
