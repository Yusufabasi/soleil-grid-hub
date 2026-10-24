# Slice 09: Operations dashboard, cache and live stream

- **Goal:** staff see site status, revenue today and open alerts from a Redis cache that survives stampedes and replica trouble, and live changes arrive through a ticketed, short-lived SSE stream that re-checks the principal on every heartbeat.
- **Depends on:** slices 00 to 08, only through what `docs/03-code-architecture.md` section 10 says they produce: `shared` (UnitOfWork, Clock, Redis adapter with circuit breaker, job registry, `read_alias`), `identity` (`Principal`, `Authorizer`, token-version check), the registry tables, `top_ups`, `meter_tokens`, and the events `payment.confirmed`, `meter.offline`, `meter.online`, `site.offline`, `alert.raised`, `alert.updated` (added by slice 08).
- **Requirements served:** FR-13; NFR-02 (cached dashboard p95 under 500 ms), NFR-03 (200 concurrent staff, about 300 streams).
- **Size:** L, 7 days.
- **Companion files:** `frontend/fe-09-dashboard.md` (dashboard screen, stale banner, EventSource client with ticket renewal and polling fallback). It consumes the operations and event names below unchanged.
- **Spikes or open items:** [open] `maintenance` is a `SiteStatus` value but no column stores it; default: never produced in v1. [open] synthetic probe revenue (D-39) has no flag column; default: the probe customer id from setting `HUB_SYNTHETIC_CUSTOMER_IDS` is excluded from revenue. Spike S09-1 (Task 09.6): confirm uvicorn streams unbuffered through HAProxy in compose.

## Read first

- `docs/00-shared-context.md#D-18 Live dashboard stream` (section 7.4), `#D-22 Database roles and functions`
- `docs/adr/0009-caching-strategy.md#Decision`
- `docs/adr/0011-server-sent-events-for-live-updates.md#Decision`
- `docs/adr/0015-postgresql-topology-and-recovery.md#Decision` (replica is asynchronous)
- `docs/02-system-architecture.md#2.f Live dashboard: cache plus SSE fed by Redis pub/sub (FR-13)`
- `docs/02-system-architecture.md#4.2 Cache catalogue`, `#4.3 Never cached`, `#4.4 Redis failure behaviour per use`, `#3.8 SSE through the proxy`
- `docs/03-code-architecture.md#2.10 analytics (FR-12, FR-13)`, `#7.4 Database access` (read-replica routing), `#7.7 API layer conventions (Django REST framework)` item 9
- `docs/05-api-design.md#8. Server-Sent Events (D-18)`, `#10. Rate-limit tiers`
- `docs/01-prd.md` acceptance criteria AC-FR-13-1 to AC-FR-13-5
- `docs/09-devops-sre.md#7.2 Scenarios and thresholds` (scenario S3) and `#1.4 Load balancer configuration (`deploy/compose/haproxy.cfg`)` (be_stream)
- `db/schema.sql` tables `sites`, `meters`, `meter_state`, `gateways`, `alerts`, `top_ups`, `meter_tokens`, `users`; the `hub_api` grants

## Global constraints

- `GET /dashboard/summary` is cached 5 s, p95 under 500 ms, supports `ETag` and `If-None-Match` (304); customers and integrator keys get 403; `support_agent` never receives revenue.
- Snapshot cache: soft expiry with jitter of plus or minus 20%, single-flight lock `SET NX PX 3000`, stale-while-revalidate; losers serve stale. Redis down means a cache miss with in-process single-flight and a database concurrency cap.
- A wallet balance used in a money decision, authorization beyond the token life, and personal data are never cached.
- Stream ticket: single use, valid 30 seconds, bound to the caller IP, carried only as `?ticket=`; the balancer log format and the app log drop the query string.
- A stream lives at most 10 minutes (600 s), then `resync_required` with reason `max_age` and close; at most 3 concurrent streams per user.
- Role and token version `tv` are re-checked on every heartbeat; failure sends `resync_required` with reason `role_changed` and closes.
- SSE events are exactly `snapshot`, `site.status_changed`, `revenue.updated` (at most 1 per second; withheld without revenue access), `alert.raised`, `alert.updated`, `heartbeat` (every 20 s), `resync_required` (reasons `cursor_too_old, server_restart, max_age, role_changed`).
- Reconnect with `Last-Event-ID` replays from a 5-minute buffer, else `resync_required` then a snapshot.
- Revenue today is the sum of confirmed top-ups since local midnight of each site's time zone, per currency, never converted between currencies, within 60 s of the ledger.
- The stream pool uses ASGI (uvicorn) with the `hub_api` role on the replica alias only; async code stays inside `analytics/api/stream.py` and `shared/streaming.py`.
- No test-only endpoint; every route exists in `api/openapi.yaml`; the Redis adapter timeout stays 50 ms.

## Review focus

1. **Cache expiry under 200 concurrent cold requests** must cause exactly one database load. Pinned by `test_cold_stampede_single_query` in `tests/integration/analytics/test_summary_cache.py`; Task 09.4.
2. **Revenue today across local midnight for two zones** (UTC+1 Kinshasa, UTC+2 Lubumbashi). Pinned by `test_revenue_today_across_midnight_two_zones` in `tests/integration/analytics/test_read_models.py`; Task 09.3.
3. **Ticket replay, late use, and use from another IP.** Pinned by `test_ticket_single_use_30s_ip_bound` in `tests/integration/analytics/test_stream_tickets.py`; Task 09.5.
4. **A role downgrade or `tv` bump while a stream is open** must close it at the next heartbeat. Pinned by `test_role_change_stops_stream_at_heartbeat` in `tests/integration/analytics/test_stream.py`; Task 09.6.
5. **Replica unreachable or lagging.** The summary keeps answering from the stale snapshot with a growing `cache_age_seconds`. Pinned by `test_replica_down_serves_stale_with_age` in `test_summary_cache.py`; Task 09.4.

## Files

Create (under `backend/src/hub/`, tests under `backend/tests/`):

- `shared/caching.py`: `SwrCache` (soft and hard TTL, jitter, single-flight, stale-if-error).
- `shared/replica.py`: `ReplicaLagMonitor`, `read_alias` pin helpers (extends `shared/db.py` if slice 00 created it; do not duplicate).
- `shared/streaming.py`: async Redis helpers: `EventBuffer` (Redis stream), `FanOut` (one listener per process), `ConnectionLimiter`.
- `analytics/domain/site_health.py`: pure computation of `operational, degraded, offline`.
- `analytics/domain/alert_summary.py`: English summary text for an alert row (clients localise).
- `analytics/infrastructure/read_models.py`: raw SQL on the replica alias: site status, alert counts, revenue today, pending counts, one alert by id.
- `analytics/infrastructure/ticket_store.py`: Redis ticket storage.
- `analytics/infrastructure/principal_state.py`: sync read of `users` status, `token_version`, roles for the heartbeat re-check.
- `analytics/application/summary.py`: `DashboardSummaryService`.
- `analytics/application/stream_tickets.py`: `StreamTicketService`.
- `analytics/application/projector.py`: event handlers and the coalescing publisher.
- `analytics/application/wiring.py`: registers consumers and the 1-second revenue ticker.
- `analytics/api/serializers.py`: `DashboardSummarySerializer`, `SiteStatusSerializer`, `StreamTicketSerializer`.
- `analytics/api/views.py`: `DashboardSummaryView`, `StreamTicketView` (hub-api pool).
- `analytics/api/stream.py`: `dashboard_stream` async view (stream pool).
- `analytics/api/urls_stream.py`: mounted by `hub.urls.stream`.
- `tools/loadtest/dashboard.js`: k6 scenario S3, cold and warm.
- Tests: `tests/unit/shared/test_caching.py`, `tests/unit/shared/test_log_scrub.py`, `tests/unit/analytics/test_site_health.py`, `tests/integration/shared/test_replica.py`, `tests/integration/analytics/test_read_models.py`, `test_summary_cache.py`, `test_summary_api.py`, `test_stream_tickets.py`, `test_stream.py`, `test_projector.py`, `tests/architecture/test_haproxy_log_format.py`.

Modify:

- `analytics/contracts.py`: add `DashboardSummaryService`, `SiteStatus` and `Summary` DTOs.
- `analytics/events.py`: `DashboardUpdated` (pub/sub only).
- `hub/urls/api.py`, `hub/urls/stream.py`: mount the routes.
- `hub/settings/stream.py`: replica alias only, uvicorn-safe settings, stream settings.
- `infra/lb/haproxy.cfg`: log format without the query string for `be_stream`; `timeout server 75s`.
- `Makefile`, `tools/verify/slice_09.sh`: verify target.

## Interfaces

**Consumes:**

```python
# shared (earlier slices)
class Clock(Protocol): def now(self) -> datetime: ...
class RedisAdapter(Protocol):                        # circuit breaker, 50 ms timeout
    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, *, ex: int | None = None, nx: bool = False, px: int | None = None) -> bool: ...
    def publish(self, channel: str, message: bytes) -> int: ...
# shared.db
def read_alias(principal_id: UUID | None = None) -> Literal["default", "replica"]: ...
# identity.contracts
class Principal: subject_id: UUID; roles: frozenset[str]; token_version: int; site_ids: frozenset[UUID]; session_id: UUID | None
class Authorizer(Protocol): def require_site(self, principal: Principal, site_id: UUID) -> None: ...
# events consumed (names from docs/03 section 4 and slice 08): payment.confirmed, meter.offline, meter.online,
# site.offline, alert.raised, alert.updated
```

**Produces:**

```python
# shared/caching.py
class SwrCache(Protocol):
    def get_or_refresh(self, key: str, loader: Callable[[], T], *, soft_ttl_s: float, hard_ttl_s: float,
                       jitter: float = 0.2, serialize: Callable[[T], bytes], deserialize: Callable[[bytes], T]) -> CacheResult[T]: ...
    def mark_stale(self, key: str) -> None: ...          # soft expiry := now, value kept
@dataclass(frozen=True, slots=True)
class CacheResult(Generic[T]): value: T; computed_at: datetime; stale: bool

# shared/replica.py
class ReplicaLagMonitor(Protocol):
    def lag_seconds(self) -> float | None: ...           # None when the replica is unreachable; cached 2 s
def read_alias_for_dashboard() -> Literal["default", "replica"]: ...

# analytics/contracts.py
class DashboardSummaryService(Protocol):
    def summary(self, principal: Principal, *, site_id: UUID | None, if_none_match: str | None) -> SummaryResponse: ...
class StreamTicketService(Protocol):
    def issue(self, principal: Principal, *, client_ip: str) -> StreamTicket: ...
    def redeem(self, ticket: str, *, client_ip: str) -> StreamGrant | None: ...   # GETDEL; None for unknown, used, expired or IP mismatch
@dataclass(frozen=True, slots=True)
class StreamGrant: user_id: UUID; token_version: int; roles: frozenset[str]; revenue_allowed: bool
                   site_scope: frozenset[UUID] | None   # None means all sites
```

**Redis keys:** `dash:v1:summary` (snapshot `{data, computed_at, soft_expiry}`), `dash:v1:site:{site_id}` (last published status hash), `dash:ticket:{sha256(ticket)}` (30 s), `dash:events` (Redis stream, trimmed to 5 minutes), `dash:pub` (pub/sub wake-up with the entry id), `dash:conn:{user_id}` (sorted set of live stream ids).

**SSE frames** (`id` is `<unix_seconds>-<stream sequence>`): `snapshot` = `DashboardSummary`; `site.status_changed` = `SseSiteStatusChanged {site_id, status, meters_online}`; `revenue.updated` = `SseRevenueUpdated {revenue_today[], top_ups_today}`; `alert.raised` and `alert.updated` = `Alert`; `heartbeat` = `{server_time}`; `resync_required` = `{reason}`.

## Tasks

### Task 09.1: Stale-while-revalidate cache with single-flight

- [ ] **Step 1: failing tests.** `tests/unit/shared/test_caching.py` with a fake Redis and fake clock: `test_hit_returns_without_loading`, `test_miss_loads_once_and_stores` (hard TTL stored), `test_soft_expired_winner_refreshes_losers_serve_stale` (two callers: loader runs once, second gets `stale=True`), `test_jitter_stays_within_twenty_percent` (1,000 draws between 0.8 and 1.2 of soft TTL, not all equal), `test_loader_error_serves_stale_if_present` and `test_loader_error_without_value_raises`, `test_redis_down_uses_local_single_flight` (fake raises `RedisUnavailable`: 50 threads, loader called once per 50 ms window, concurrency cap 4 never exceeded), `test_mark_stale_keeps_value`, `test_lock_expires_after_3s_so_crashed_winner_does_not_block`.
- [ ] **Step 2: run.** `cd backend && uv run pytest tests/unit/shared/test_caching.py -q`. Expected: ImportError for `hub.shared.caching`.
- [ ] **Step 3: implement.** Value format `{"d": <bytes>, "c": computed_at, "s": soft_expiry}`; Redis TTL is the hard TTL (default 300 s for the dashboard so stale-if-error can outlive a replica outage; the soft TTL is 5 s, setting `HUB_DASH_SOFT_TTL_S`). `get_or_refresh`: read; if fresh return; if soft-expired, try `SET key:lock NX PX 3000`; the winner calls the loader, writes, releases with a token compare; losers return the stale value, or when no value exists poll every 50 ms up to 3 s and then load under a per-process `BoundedSemaphore(4)`. On `RedisUnavailable` use an in-process dict with a per-key `threading.Lock` (one loader call per key per pod), same semaphore, entries expiring at the soft TTL. Soft TTL for each write is `soft_ttl_s * uniform(1 - jitter, 1 + jitter)` using an injectable random source.
- [ ] **Step 4: run until green.** same command.
- [ ] **Step 5: commit.** `feat(shared): stale-while-revalidate cache with single-flight and jitter`

### Task 09.2: Replica routing and lag handling

- [ ] **Step 1: failing tests.** `tests/integration/shared/test_replica.py` (compose Postgres primary plus replica fixture; a fake lag source for unit speed): `test_read_alias_returns_replica_normally`, `test_read_alias_pinned_after_write_for_five_seconds` (Redis key per principal; at 5.1 s back to replica), `test_read_alias_primary_when_lag_over_five_seconds`, `test_lag_none_when_replica_unreachable_and_alias_falls_back_to_primary`, `test_lag_value_cached_two_seconds` (one query per 2 s window), `test_payment_paths_never_use_replica` (architecture assertion: `top_ups` confirmation and token code passes `using="default"`).
- [ ] **Step 2: run.** `uv run pytest tests/integration/shared/test_replica.py -q`. Expected: ImportError for `hub.shared.replica`.
- [ ] **Step 3: implement.** `ReplicaLagMonitor.lag_seconds()` runs on the replica connection `SELECT COALESCE(EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp()), 0)` (zero when no replay yet) and caches 2 s in process; connection error returns `None`. `read_alias` returns `"default"` when pinned or lag is above 5 s or `None`, otherwise `"replica"`. The dashboard loader uses `read_alias_for_dashboard()`: when it returns `"default"` the loader still runs once under single-flight, so the primary sees one query, not one per user. Expose gauge `replica_lag_seconds`.
- [ ] **Step 4: run until green.** same command.
- [ ] **Step 5: commit.** `feat(shared): replica lag monitor and read alias pinning`

### Task 09.3: Read models and revenue today

- [ ] **Step 1: failing tests.** `tests/unit/analytics/test_site_health.py`: `test_offline_when_all_gateways_silent_15_minutes`, `test_degraded_when_some_gateway_silent`, `test_degraded_when_online_meters_below_80_percent`, `test_operational_otherwise`, `test_no_gateway_site_follows_meters_only`. `tests/integration/analytics/test_read_models.py`: `test_revenue_today_across_midnight_two_zones` (top-ups confirmed at 21:30, 22:30 and 23:30 UTC; at 23:45 UTC the Lubumbashi site counts the last two, the Kinshasa site only the last), `test_revenue_per_currency_never_converted` (USD and CDF sites give two Money entries), `test_reversed_and_late_top_ups` (`reversed` excluded; a `late=true` confirmed one included), `test_revenue_matches_ledger_topup_received` (sum equals credits of `topup_received` for the same window; TC-FR-13-1), `test_synthetic_customers_excluded`, `test_alert_counts_by_severity_ignore_resolved`, `test_pending_and_token_backlog_counts`, `test_queries_use_replica_alias` (assert connection alias in captured queries).
- [ ] **Step 2: run.** `uv run pytest tests/unit/analytics/test_site_health.py -q`. Expected: ImportError for `site_health`.
- [ ] **Step 3: implement.** `site_health.compute(meters_total, meters_online, gateways_total, gateways_silent, degraded_ratio=0.8)` (pure). `read_models.site_rows()` joins `sites`, counts of `meters` (status `active`; online means `meter_state.last_seen_at >= now - 90 minutes`) and `gateways` (active; silent means `last_heartbeat_at < now - 15 minutes`, never-reported counts from `created_at`). `revenue_rows()` groups `top_ups` (`status = 'confirmed'`, join `meters.site_id`, join `sites.timezone`) by site and currency where `confirmed_at >= (date_trunc('day', now() AT TIME ZONE s.timezone)) AT TIME ZONE s.timezone` and `< that + interval '1 day'`, using `ix_top_ups_confirmed_at`. `alert_rows()` counts unresolved alerts by site and severity. All methods take the alias from the caller, use raw cursors, and bound every time predicate on both sides.
- [ ] **Step 4: run until green.** `uv run pytest tests/unit/analytics tests/integration/analytics/test_read_models.py -q`.
- [ ] **Step 5: commit.** `feat(analytics): dashboard read models with per-site-timezone revenue`

### Task 09.4: Summary service and endpoint

- [ ] **Step 1: failing tests.** `tests/integration/analytics/test_summary_cache.py`: `test_cold_stampede_single_query` (200 threads, flushed Redis: query counter equals 1, all receive the same `generated_at`), `test_warm_hit_makes_zero_queries`, `test_replica_down_serves_stale_with_age` (TC-FR-13-3: stop the replica fixture, `cache_age_seconds` grows past 60, status stays 200), `test_event_marks_snapshot_stale_not_deleted` (a `payment.confirmed` handler call keeps the value readable), `test_redis_flush_recovers`. `tests/integration/analytics/test_summary_api.py`: `test_summary_shape_matches_openapi` (schemathesis for `getDashboardSummary`), `test_etag_and_304`, `test_support_agent_has_no_revenue_fields`, `test_finance_and_operations_see_revenue`, `test_site_scope_filters_sites_alerts_and_revenue` (an engineer assigned to site A: site B absent from every field), `test_site_id_outside_scope_is_403`, `test_customer_and_integrator_key_get_403` (AC-FR-13-5), `test_totals_equal_registry_after_refresh` (TC-FR-13-1).
- [ ] **Step 2: run.** `uv run pytest tests/integration/analytics/test_summary_cache.py -q`. Expected: ImportError for `analytics.application.summary`.
- [ ] **Step 3: implement.** `DashboardSummaryService.summary` loads one global snapshot under key `dash:v1:summary` through `SwrCache` (the loader runs `site_rows`, `alert_rows`, `revenue_rows`, pending and token counts in one replica transaction `REPEATABLE READ, READ ONLY`); the snapshot keeps per-site figures internally. `project(snapshot, principal, site_id)` filters to the principal's sites (all sites when the user flag says so), sums revenue per currency over the visible sites, drops `revenue_today` and `top_ups_today` when the roles lack `finance_officer` and `operations_engineer`, and sets `cache_age_seconds = now - computed_at`. The ETag is a weak hash of the projected body without `generated_at` and `cache_age_seconds`. `DashboardSummaryView` (`@operation("getDashboardSummary")`) answers 304 when `If-None-Match` equals the ETag, sets `Cache-Control: private, no-cache`. Handlers `on_event` in `projector.py` call `SwrCache.mark_stale("dash:v1:summary")` for `payment.confirmed`, `meter.offline`, `meter.online`, `site.offline`, `alert.raised`, `alert.updated`.
- [ ] **Step 4: run until green.** `uv run pytest tests/integration/analytics/test_summary_cache.py tests/integration/analytics/test_summary_api.py -q`.
- [ ] **Step 5: commit.** `feat(analytics): cached dashboard summary with etag and site scoping`

### Task 09.5: Stream tickets and log hygiene

- [ ] **Step 1: failing tests.** `tests/integration/analytics/test_stream_tickets.py`: `test_ticket_single_use_30s_ip_bound` (TC-FR-13-5: second redeem returns None; redeem at 30.1 s returns None; redeem from another IP returns None and the ticket is gone), `test_ticket_response_matches_openapi` (201, `stream_max_seconds == 600`, `expires_at` 30 s ahead), `test_ticket_is_not_stored_in_clear` (Redis key holds only the SHA-256), `test_ticket_carries_role_and_token_version`, `test_customer_cannot_issue_ticket`, `test_redis_down_returns_503_with_retry_after`. `tests/unit/shared/test_log_scrub.py::test_ticket_param_removed_from_logged_url` (a logged path `/api/v1/dashboard/stream?ticket=abc&site_id=1` becomes `/api/v1/dashboard/stream`). `tests/architecture/test_haproxy_log_format.py::test_log_format_has_no_query_string` (`infra/lb/haproxy.cfg` log-format contains `%HP` and not `%HU` or `%r`).
- [ ] **Step 2: run.** `uv run pytest tests/integration/analytics/test_stream_tickets.py tests/architecture/test_haproxy_log_format.py -q`. Expected: ImportError, and the config test fails because `%r` is present.
- [ ] **Step 3: implement.** `issue`: 32 random bytes URL-safe, `SET dash:ticket:{sha256} <json {user_id, tv, roles, revenue_allowed, site_scope, ip}> EX 30`. `redeem`: `GETDEL`, compare `ip` with `client_ip`, return `StreamGrant` or None. `client_ip` comes from the last hop of `X-Forwarded-For` appended by HAProxy (`HUB_TRUSTED_PROXY_COUNT = 1`), never from a client-supplied header otherwise. `StreamTicketView` (`@operation("createStreamTicket")`, `staff` tier). Add the log filter in `hub/shared/logging.py` that strips query strings from any logged URL, run uvicorn with `--no-access-log`, and change the HAProxy `log-format` to use `%HP`.
- [ ] **Step 4: run until green.** same command, then `make contracts`.
- [ ] **Step 5: commit.** `feat(analytics): single-use ip-bound stream tickets and query-free logs`

### Task 09.6: The SSE stream (ASGI)

- [ ] **Step 1: failing tests.** `tests/integration/analytics/test_stream.py` (httpx async client against the ASGI app; settings `HUB_STREAM_HEARTBEAT_S=0.1`, `HUB_STREAM_MAX_AGE_S=1` in the relevant tests): `test_first_event_is_snapshot_with_sse_headers` (`Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`), `test_heartbeat_event_every_interval`, `test_stream_ends_at_max_age_with_resync_max_age` (TC-FR-13-6), `test_role_change_stops_stream_at_heartbeat` (remove the role, next heartbeat yields `resync_required` reason `role_changed`, connection closes; same for `token_version + 1`, and for user status `locked`), `test_fourth_concurrent_stream_gets_429_with_retry_after`, `test_slots_released_on_disconnect`, `test_support_role_never_receives_revenue_updated`, `test_site_filter_and_scope_applied_to_events`, `test_last_event_id_replays_missed_events` (TC-FR-13-2), `test_last_event_id_older_than_buffer_gives_cursor_too_old_then_snapshot`, `test_slow_consumer_dropped_with_resync`, `test_redis_down_falls_back_to_polling_snapshots` (TC-FR-13-3 companion), `test_stream_served_without_buffering_by_uvicorn` (TC-FR-13-7: real uvicorn subprocess, first byte within 200 ms, heartbeat chunks arrive separately).
- [ ] **Step 2: run.** `uv run pytest tests/integration/analytics/test_stream.py -q`. Expected: 404 on `/api/v1/dashboard/stream`.
- [ ] **Step 3: implement.** `dashboard_stream(request)` is `async def`: read `ticket` and optional `site_id`; `redeem` (missing or None: 401 problem `unauthorized`); `ConnectionLimiter.acquire(user_id)` using sorted set `dash:conn:{user_id}` scored by last heartbeat, ignoring members older than 45 s, rejecting at 3 with 429 `rate_limited`. Return `StreamingHttpResponse(generator, content_type="text/event-stream")`. The generator sends `snapshot` first (`DashboardSummaryService` projected for the grant, read through `sync_to_async(thread_sensitive=False)`), then loops on a per-connection `asyncio.Queue(maxsize=100)` fed by the process-wide `FanOut` (one subscriber on `dash:pub`, reading new entries from `dash:events`). A frame is `id: {epoch}-{seq}\nevent: {name}\ndata: {json}\n\n`. Every `HUB_STREAM_HEARTBEAT_S` (20) emit `heartbeat`, refresh the limiter score, and call `principal_state.read(user_id)` through `sync_to_async(thread_sensitive=False)`; if status is not `active`, `token_version` differs from the grant, or the roles no longer include a dashboard role, emit `resync_required {"reason": "role_changed"}` and return. At `HUB_STREAM_MAX_AGE_S` (600) on the monotonic clock emit `resync_required {"reason": "max_age"}` and return. `Last-Event-ID` is read with `XRANGE dash:events (id +`; when the id predates the oldest buffered entry send `resync_required {"reason": "cursor_too_old"}` followed by a snapshot. Events are filtered by the grant's site scope and `site_id`, and `revenue.updated` is skipped when `revenue_allowed` is false. A full queue sends `resync_required cursor_too_old` and closes. On Redis loss poll the summary every 10 s and send a `snapshot` when the ETag changed. Use `finally` to release the limiter slot. Run Spike S09-1 against compose HAProxy and record the result in `progress.md`.
- [ ] **Step 4: run until green.** `uv run pytest tests/integration/analytics/test_stream.py -q`.
- [ ] **Step 5: commit.** `feat(analytics): ticketed SSE dashboard stream with heartbeat re-checks`

### Task 09.7: Projector, publisher and load script

- [ ] **Step 1: failing tests.** `tests/integration/analytics/test_projector.py`: `test_payment_confirmed_publishes_revenue_updated_coalesced` (20 events inside 1 s give at most 1 `revenue.updated`), `test_site_status_event_publishes_only_on_change` (status unchanged: nothing published; changed: one `site.status_changed` with `meters_online`), `test_alert_events_publish_alert_payload` (payload validates against the `Alert` schema, `summary` non-empty), `test_events_buffer_trimmed_to_five_minutes`, `test_consumer_is_idempotent_on_redelivery` (same `event_id` twice, one publish; uses `consumer_inbox`), `test_end_to_end_ingest_to_sse_within_15_seconds` (TC-FR-13-2: raise an alert through slice 08's engine, the open stream receives `alert.raised` within 15 s).
- [ ] **Step 2: run.** `uv run pytest tests/integration/analytics/test_projector.py -q`. Expected: ImportError for `projector`.
- [ ] **Step 3: implement.** `projector.py` registers a consumer group `analytics` on `events.payments`, `events.tokens` and `events.alerts` through `wiring.py`. For each handled event: `mark_stale`, then publish: `XADD dash:events MINID ~ <now - 5 min> * name <event> data <json>` and `PUBLISH dash:pub <entry id>`. A 1-second ticker per worker (singleton by advisory lock) recomputes revenue only when a `payment.confirmed` arrived since the last tick. Site status events recompute the one site through `read_models.site_rows(site_ids=[id])` on the primary alias (a changed status must not wait for replica lag) and compare with the hash `dash:v1:site:{id}`; an hourly job rebuilds those hashes from the database. Add `tools/loadtest/dashboard.js` for scenario S3 (cold and warm, 200 users, 20% holding streams) with the thresholds of `docs/09-devops-sre.md` section 7.2 and a hit-ratio check.
- [ ] **Step 4: run until green.** `uv run pytest tests/integration/analytics -q`; then `make verify SLICE=09`.
- [ ] **Step 5: commit.** `feat(analytics): dashboard projector, event buffer and load scenario`

## Verification

`make verify SLICE=09` runs from the repository root:

1. `make lint`: clean (import-linter confirms `analytics` imports only shared, identity and ai_gateway; async code only in the two allowed files).
2. `cd backend && uv run pytest tests/unit/shared tests/unit/analytics -q`: all pass.
3. `cd backend && uv run pytest tests/integration/shared/test_replica.py tests/integration/analytics -q`: all pass.
4. `cd backend && uv run pytest tests/architecture -k "haproxy or django_config" -q`: pass.
5. `cd backend && uv run pytest tests/contract -k "dashboard" -q`: schemathesis passes for `getDashboardSummary` and `createStreamTicket`.
6. `make contracts`: green.
7. `k6 run --vus 50 --duration 60s tools/loadtest/dashboard.js` against compose: warm p95 under 500 ms, cache hit ratio above 90%.

## Demo script

1. `make up && make seed && make simulator`. Log in as an operations engineer in `hub-web` and open the dashboard. Observe site tiles, revenue per currency and alert counts from the first request, `cache_age_seconds` under 10.
2. Run `curl -i` for `GET /api/v1/dashboard/summary` twice with the returned `ETag` as `If-None-Match`. Observe 200 then 304.
3. `redis-cli FLUSHALL`, then fire 200 parallel requests (`k6 run -u 200 -i 200 tools/loadtest/dashboard.js`). Observe a single summary query in `pg_stat_statements` for that second.
4. In a terminal request a ticket, then open the stream twice with the same ticket. Observe the second attempt is 401.
5. Trigger a top-up through the provider stub; observe `revenue.updated` on the stream within seconds, and a stopped simulated gateway produce `site.status_changed`.
6. Change the engineer's role to `support_agent` (an approved role grant). Observe `resync_required` with reason `role_changed` at the next heartbeat and the stream closing.
7. Wait 10 minutes (or set `HUB_STREAM_MAX_AGE_S=30`). Observe `resync_required` reason `max_age`, then the client reconnect with a new ticket.
8. Stop the replica container. Observe the summary still answers and `cache_age_seconds` passes 60 so the stale banner shows.

## Done criteria

Requirements covered: FR-13, with NFR-02 and NFR-03 targets for the dashboard. Test cases implemented and passing: TC-FR-13-1, TC-FR-13-2, TC-FR-13-3, TC-FR-13-4, TC-FR-13-5, TC-FR-13-6, TC-FR-13-7. TC-NFR-02-2 is delivered as `tools/loadtest/dashboard.js` (nightly run owned by slice 17).

## Plan notes for the tech lead

1. **Cache TTL.** `api/openapi.yaml` says the summary is cached 5 s; `docs/02-system-architecture.md` section 4.2 says 30 s with jitter and a hard expiry of twice the soft one. Followed OpenAPI for the soft TTL (5 s, setting) and set the hard TTL to 300 s so a replica outage serves stale data with a visible age; `docs/02` says twice the soft TTL.
2. **Heartbeat interval.** `docs/02` section 3.8 and `docs/03` 7.7 say 15 s; OpenAPI and `docs/09` (HAProxy `timeout server 75s`) say 20 s. Followed OpenAPI.
3. **Async thread mode.** `docs/03` 7.7 says `sync_to_async(thread_sensitive=False)`; `docs/04` 10.10 says `thread_sensitive=True`. Followed `docs/03` (it names the stream view and D-42), with the replica alias only.
4. **Event buffer and fan-out.** ADR-0011 specifies pub/sub fan-out and `docs/05` a 5-minute `Last-Event-ID` buffer; pub/sub has no replay. The plan keeps pub/sub as the wake-up and a Redis stream `dash:events` as the buffer. Redis stream names `events.*` of `docs/03` are unchanged.
5. **Cross-module read SQL.** `analytics` may import only shared, identity and ai_gateway, yet the dashboard reads sites, meters, gateways, alerts and top-ups. The plan uses raw SQL in `read_models.py` as the same controlled exception `docs/03` 2.10 grants for the published read views, pinned by `test_read_models.py`. Needs the tech lead's explicit acceptance; the alternative is views in an `analytics_dash` schema.
6. **Revenue meaning.** D-10 recognises revenue at token issuance; AC-FR-13-3 and `docs/02` define "revenue today" as confirmed top-ups. Followed the PRD and labelled it cash received, equal to ledger `topup_received` credits. `docs/02`'s `HINCRBY` revenue cache is dropped in favour of the snapshot recompute.
7. **Role views.** `docs/05` says support sees "tickets and alerts counts" while `DashboardSummary` has no ticket fields; the plan gives support sites and alert counts and omits the money fields.
8. **Missing schema support.** `SiteStatus.maintenance` has no storage; D-39 synthetic activity has no flag column. Defaults are in the open items.
9. **Alert events.** `alert.updated` is not in the `docs/03` catalogue; slice 08 adds it and this slice depends on it.
10. **Revocation read.** The heartbeat re-check reads `users` from the replica (stream pool is replica-only, D-49), so a revocation can lag by the replica delay on top of 20 s; `docs/07` 4.2 wants privileged routes on the primary, but the stream is not a privileged route.
11. **Balancer config path.** `docs/03` section 1 puts HAProxy files in `infra/lb/`; `docs/09` sketches `deploy/compose/haproxy.cfg`. Followed `docs/03`.
