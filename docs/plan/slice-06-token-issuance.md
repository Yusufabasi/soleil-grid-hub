# Slice 06: Energy-credit token issuance

- **Goal:** the `meters` token path. A consumer of `payment.confirmed` re-checks meter ownership and status inside its transaction, takes the stored quote, allocates a monotonic per-meter counter and a 72-hour expiry, posts the energy purchase and inserts a posting-bound token. A sender pushes the token to the site gateway through the outbound adapter, retries with the same token id on a 30 s doubling schedule capped at 30 minutes for up to 72 hours, and moves silent tokens to `unknown`. The gateway acknowledge and heartbeat routes, rejection and reversal rules (D-07), and the staff token operations complete FR-05.
- **Depends on:** slice 03 (`LedgerService`), slice 04 (`payment.confirmed`, `top_ups`), slice 05 (consumers, pollers, `JobQueue`, `OutboxReemitter`), slice 02 (meters, gateways, `CustomerDirectory`), slice 01 (`Principal`, permission class), slice T2 ([meter simulator](slice-T2-meter-simulator.md), the gateway double).
- **Requirements served:** FR-05 (FR-04 and NFR-05 exercised).
- **Size:** L, 9 days for a cheaper agent.
- **Companion files:** none (token screens belong to later frontend slices).
- **Spikes or open items:** **[open]** real meter token format (STS or vendor): default is the signed credit instruction defined in Task 06.1, which the simulator verifies with the same shared keys. **[open]** revenue recognition at issuance (D-10): default as designed, the `energy_purchase` posting is made at issuance. One migration is instructed (Task 06.2) because the frozen schema has no function that reverses an `energy_purchase` posting (Plan note 1).

## Read first

- `docs/00-shared-context.md#7.2 Canonical state machines` (`meter_tokens.status`), `#7.3 Money rules` (D-07, D-08, D-10), `#7.4` (D-19, D-20)
- `docs/adr/0017-token-issuance-sole-write-to-site-systems.md`
- `docs/03-code-architecture.md#2.6 meters (FR-01 device part, FR-05, part of FR-07)`, `#6.2 Token issuance (FR-05)`, `#5. Ports and adapters` (`MeterGateway`)
- `docs/02-system-architecture.md#2.b Energy-credit token issuance saga` and `#5.3 Consumer idempotency and the Redis-loss gap`
- `db/schema.sql`: `hub.meters`, `hub.meter_assignments`, `hub.gateways`, `hub.meter_tokens` and `hub.meter_tokens_guard`, `hub.post_ledger_transaction` (the `energy_purchase` branch), `hub.verify_api_key`, grants of `hub_ingest` and `hub_worker`
- `docs/10-test-strategy.md#2.5 Token issuance saga and compensation`
- `docs/plan/slice-T2-meter-simulator.md` (push receiver, ack modes) and `docs/plan/slice-05-outbox-relay-worker.md` Interfaces
- Operations in `api/openapi.yaml`: `acknowledgeToken`, `gatewayHeartbeat`, `listTokens`, `getToken`, `retryToken`, `listMeterTokens`

## Global constraints

- Exactly one token per top-up: unique `meter_tokens.top_up_id`; every token has `ledger_transaction_id` NOT NULL, a counter strictly increasing per meter, and `expires_at = created_at + 72 h`.
- Issuance runs in one transaction on the primary: lock `top_ups` then `meters` (`FOR UPDATE`, fixed order), re-check the meter belongs to the top-up's customer and is `active`, take `energy_wh` from the stored quote (never re-quote), `UPDATE meters SET token_counter = token_counter + 1 RETURNING`, post `token:<token_id>:purchase`, insert the token, add `token.requested`.
- The purchase amount is `ceil(energy_wh * tariff_minor_per_kwh / 1000)`, never more than the top-up amount; the remainder stays in the wallet (D-08).
- The push happens outside any database transaction; every resend uses the same token id, counter and payload.
- Retry delay after attempt n is `min(30 * 2^(n-1), 1800)` seconds (30 s, 1, 2, 4, 8, 16 minutes, then 30 minutes) for up to 72 hours from creation; then `unknown`, never `failed`.
- Reverse the posting only on a definitive rejection (`invalid_meter`, `counter_stale`, `expired`) or after an approved reconciliation confirms non-delivery. An `unknown` token is never reversed automatically.
- Token issuance is the only write command to site systems; there is no pull endpoint, no remote disconnect and no configuration command.
- Gateway keys: header `X-API-Key`, kind `gateway`, scopes `tokens:acknowledge` and `gateway:heartbeat`, bound to sites and a source range; the token's meter must be in the key's sites; out-of-site tokens answer 404.
- Token payloads are stored encrypted (`token_enc`); the plaintext, the code and the counter never appear in logs.
- The ingest role may update only the `meter_tokens` columns granted to it; reversal runs only on the worker (the new function checks the caller).
- Consumers use `consumer_inbox` through the slice 05 framework; handlers raise `Retryable` for transient failures and `Permanent` otherwise.

## Review focus

1. **Meter reassigned or deactivated between confirmation and issuance (TOCTOU).** The meter row lock must serialise issuance with reassignment: across 200 interleavings no token exists for a customer who no longer owned the meter at issuance. Pinned by `test_toctou_reassignment_vs_issuance_200_interleavings` (Task 06.3).
2. **Reversing a token the meter actually applied.** An `unknown` token must never be reversed automatically, and a late `applied` acknowledgement after `unknown` must still win. Pinned by `test_unknown_token_is_never_reversed_automatically` (Task 06.6) and `test_late_ack_after_unknown_acknowledges` (Task 06.5).
3. **Duplicate, replayed and out-of-site acknowledgements.** Pinned by `test_duplicate_ack_is_idempotent`, `test_ack_from_other_site_is_404_and_no_effect`, `test_counter_mismatch_is_422` (Task 06.5).
4. **Silent gateway for hours.** No hot loop, no second token, retries stop at 72 hours. Pinned by `test_retry_schedule_caps_and_stops_at_72h` and `test_ack_lost_never_creates_second_token` (Task 06.4).
5. **Two top-ups for one meter at once.** Counters must be consecutive and unique. Pinned by `test_two_top_ups_same_meter_get_consecutive_counters` (Task 06.3).

## Files

Paths relative to `backend/src/hub/` unless stated.

- `meters/domain/token.py` (`TokenStatus`, `retry_delay`, `purchase_minor`, `horizon_reached`), `domain/payload.py` (credit instruction, sign, encrypt), `domain/errors.py`.
- `meters/contracts.py`: `TokenService`, `TokenView`; `meters/events.py`: `TokenRequested`, `TokenIssued`, `TokenFailed`, `TokenRejected`.
- `meters/application/`: `issue_token.py`, `send_due_tokens.py`, `acknowledge.py`, `reject_and_reverse.py`, `expire_unknown.py`, `retry_now.py`, `reemit_missing.py`, `heartbeat.py`, `ports.py` (`MeterGateway`, `TokenCipher`), `wiring.py`.
- `meters/infrastructure/`: `models.py` (`MeterToken`; `Meter` and `Gateway` exist from slice 02), `repositories.py` (`TokenRepository`), `gateway_push.py` (`HttpMeterGateway`), `fake_gateway.py`, `cipher.py`.
- `meters/api/serializers.py` (`TokenSerializer`, `TokenAckSerializer`, `GatewayHeartbeatSerializer`, `GatewayHeartbeatAckSerializer`), `views.py` (`TokenViewSet`), `gateway.py` (`acknowledge_token`, `gateway_heartbeat`), `urls.py`.
- `identity/api/authentication.py` (modify): `ApiKeyAuthentication` for gateway keys only (Plan note 3).
- `ledger/contracts.py` and `ledger/infrastructure/writer.py` (modify): `LedgerService.reverse_token_purchase`.
- `shared/jobs/outbox_repository.py` (modify): `find_event_id(aggregate_type, aggregate_id, event_type)`.
- `db/migrations/NNNN_reverse_token_purchase.sql` and `shared/migrations/00NN_reverse_token_purchase.py`.
- Settings (`settings/base.py`): `TOKEN_TTL_HOURS=72`, `TOKEN_RETRY_BASE_S=30`, `TOKEN_RETRY_CAP_S=1800`, `TOKEN_PUSH_TIMEOUT_S=10`, `TOKEN_SENDER_BATCH=50`, `TOKEN_MISSING_SWEEP_MINUTES=5`, `GATEWAY_PUSH_ENDPOINTS` (JSON map gateway id to URL), `GATEWAY_PUSH_DEFAULT_URL`, `TOKEN_PAYLOAD_KEY`, `TOKEN_SIGNING_KEY` (secrets, never in the repository).
- Tests under `backend/tests/`: `unit/meters/test_token_domain.py`, `test_payload.py`, `test_retry_schedule.py`; `integration/meters/test_issue_token.py`, `test_toctou.py`, `test_sender.py`, `test_acknowledge_api.py`, `test_rejection_reversal.py`, `test_token_api.py`, `test_reemit_missing.py`, `test_reverse_migration.py`; `property/test_token_saga_schedules.py`; `e2e/test_token_with_simulator.py`.

## Interfaces

**Consumes:**

```python
# slice 05
class EventHandler(Protocol): name: str; event_names: frozenset[str]; def __call__(self, event: EventEnvelope, uow: UnitOfWork) -> None
class JobRegistry(Protocol): register_consumer(...); register_poller(...); register_cron(...)
class JobQueue(Protocol): def enqueue(self, name, payload, *, delay=..., dedupe_key=None) -> None
class OutboxReemitter(Protocol): def reemit(self, event_id: UUID) -> bool
# slice 03
class LedgerService(Protocol):
    def post(self, req: PostingRequest, *, uow: UnitOfWork) -> PostingResult        # kind "energy_purchase", source_top_up_id set
    def balance(self, account: AccountRef) -> Money
# slice 04
class PaymentService(Protocol): def confirmed_between(self, start, end, provider) -> list[ConfirmedPaymentRef]  # assumed fields top_up_id, confirmed_at
# slice 02: CustomerDirectory.customer_for_meter(meter_id), meters and gateways rows through its own registry models
# slice 01: Principal (kind "gateway": subject_id is the api key id, site_ids from the key), Authorizer.require
```

**Produces:**

```python
class TokenService(Protocol):                                   # docs/03 section 2.6
    def issue_for_payment(self, payment: PaymentConfirmed) -> TokenView: ...      # idempotent on top_up_id
    def acknowledge(self, token_id: UUID, *, gateway: Principal, counter: int, result: Literal["applied","rejected","counter_replayed"], reason: str | None) -> TokenView: ...
    def retry_now(self, token_id: UUID, *, actor: Principal) -> TokenView: ...    # resets next_attempt_at, same token id
    def status_for_top_up(self, top_up_id: UUID) -> TokenView | None: ...
    def confirm_non_delivery(self, token_id: UUID, evidence: ReconciliationEvidence, *, uow: UnitOfWork) -> None: ...  # slice 14 executor; worker role only
class MeterGateway(Protocol):                                   # docs/03 section 5
    def push_token(self, gateway_ref: str, token: TokenInstruction) -> PushResult: ...
@dataclass(frozen=True, slots=True)
class TokenInstruction: token_id: UUID; meter_serial: str; counter: int; expires_at: datetime; energy_wh: int; payload: str
PushResult = Accepted | Rejected(reason: Literal["invalid_meter","counter_stale","expired"]) | Unreachable(detail: str, retry_after_s: int | None)
# ledger addition
class LedgerService(Protocol):
    def reverse_token_purchase(self, token_id: UUID, *, posting_key: str, uow: UnitOfWork) -> ReversalResult: ...
```

Events: `token.requested` (`token_id, top_up_id, meter_id, counter, energy_wh, expires_at, ledger_transaction_id`), `token.issued` (`token_id, top_up_id, customer_id, meter_id, energy_wh, counter, acked_at, attempts`), `token.failed` (`token_id, top_up_id, customer_id, meter_id, reason_code, status, attempts, final`; for a top-up with no token `token_id` is null and `reason_code` is `meter_inactive` or `insufficient_wallet`), and the new internal `token.rejected` (`token_id, meter_id, reason_code`) that the ingest process emits so that the worker performs the reversal (Plan note 2). Consumers registered: `meters.on_payment_confirmed` (group `meters`, stream `events.payments`), `meters.on_token_rejected` (group `meters`, stream `events.tokens`). Poller `meters.send_due_tokens` (every 2 s, skip locked); cron `meters.expire_unknown_tokens` and `meters.reemit_missing_tokens` (every minute); job `meters.retry_now`. Push wire format: `POST {endpoint}/gateway/push/tokens` with `{token_id, meter_serial, counter, expires_at, energy_wh, payload}` as the simulator receiver defines; `202 accepted_for_delivery`, `422 rejected` with a reason, a timeout or 5xx is `Unreachable`.

## Tasks

### Task 06.1: Token domain, payload crypto, models and repository

- [ ] **1. Failing tests.** `unit/meters/test_retry_schedule.py`: `test_delays_30_60_120_240_480_960_1800_cap` (attempts 1 to 9), `test_next_attempt_never_before_now_plus_delay`, `test_horizon_reached_at_72h_exactly`. `test_token_domain.py`: `test_purchase_minor_is_ceil_and_not_above_amount` (amount 1001, tariff 3000, energy 333: purchase 1000; hypothesis: for amounts 1 to 10^9 and tariffs 1 to 10^7, `purchase_minor <= amount` and `energy_wh == amount*1000//tariff`), `test_allowed_transitions_match_database_guard` (pending to sent, failed, cancelled; sent to acknowledged, pending, failed, unknown, cancelled; unknown to acknowledged, failed, cancelled; all else refused), `test_unknown_reached_from_pending_goes_through_sent`. `test_payload.py`: `test_signature_covers_token_meter_counter_energy_expiry`, `test_encrypt_round_trip_and_wrong_key_fails`, `test_repr_never_contains_payload`. `integration/meters/test_issue_token.py::test_models_match_schema`, `test_token_without_posting_counter_or_expiry_is_rejected` (TC-FR-05-10: inserts missing `ledger_transaction_id`, a non-increasing counter, `expires_at <= created_at` each fail at the database), `test_expired_token_is_refused_by_domain`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/unit/meters -q`. Expected: ImportError.
- [ ] **3. Implement.** `retry_delay(attempts) = min(base * 2**(attempts-1), cap)`; `purchase_minor(energy_wh, tariff, amount) = -(-energy_wh * tariff // 1000)`; `TokenStatus` constants and `can_transition`. `payload.py`: `build_instruction(token) -> dict`, `sign(key, ...)` HMAC-SHA256 over `{token_id}.{meter_serial}.{counter}.{energy_wh}.{expires_at_epoch}`, `TokenCipher.encrypt/decrypt` with AES-GCM from `TOKEN_PAYLOAD_KEY`. `MeterToken` unmanaged model as the schema; `TokenRepository`: `lock_for_issue(top_up_id)` (locks `top_ups` then `meters`), `next_counter(meter_id)` (`UPDATE ... RETURNING token_counter`), `insert`, `claim_due(limit)` (`skip_locked`), `set_status` limited to the column grants, `get`, `list_*`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(meters): token domain rules, signed encrypted payload and repository`

### Task 06.2: Migration to reverse a token purchase

- [ ] **1. Failing tests.** `integration/meters/test_reverse_migration.py`: `test_reverse_requires_failed_or_cancelled_token` (status `sent`: `restrict_violation`), `test_reverse_negates_every_entry_and_restores_wallet`, `test_reverse_is_idempotent_on_posting_key`, `test_reverse_only_once_per_original` (second key raises on the unique `reverses_transaction_id`), `test_hub_ingest_and_hub_api_cannot_execute` (`insufficient_privilege`), `test_function_is_definer_with_pinned_search_path`, `test_invariants_still_pass` (`db/invariant_tests.sql`, baseline comparison).
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/meters/test_reverse_migration.py -q`. Expected: `function hub.reverse_token_purchase does not exist`.
- [ ] **3. Implement.** `hub.reverse_token_purchase(p_token_id uuid, p_posting_key text) RETURNS uuid`, `SECURITY DEFINER SET search_path = pg_catalog, hub, pg_temp`, owner `ledger_owner` (add `GRANT SELECT ON hub.meter_tokens TO ledger_owner`): raises unless `pg_has_role(current_user, 'hub_worker', 'member')`; loads the token, requires status `failed` or `cancelled` and an original of kind `energy_purchase`; inserts a `reversal` transaction (`reverses_transaction_id` = original, `source_top_up_id` copied, `ON CONFLICT (posting_key) DO NOTHING`) and the negated entries; returns the id (existing id on replay). `REVOKE ALL FROM PUBLIC; GRANT EXECUTE TO hub_worker`. Add the `RunSQL` migration and `LedgerService.reverse_token_purchase` through `call_function`.
- [ ] **4. Run** until green, then the root contract tests.
- [ ] **5. Commit:** `feat(db): reverse_token_purchase definer function for rejected tokens`

### Task 06.3: Issuance (consumer, re-check, counter, posting)

- [ ] **1. Failing tests.** `integration/meters/test_issue_token.py` (role `hub_worker`): `test_issues_one_token_for_confirmed_top_up` (status `pending`, counter 1, `expires_at = created_at + 72h`, `energy_wh` equals the stored quote, a posting `token:<id>:purchase` debiting the wallet and crediting `revenue_energy`, `token.requested` outbox row, remainder left in the wallet), `test_duplicate_payment_confirmed_creates_one_token` (TC-FR-05-6: event delivered twice and also with a new event id: one token, one posting), `test_tariff_change_after_initiation_does_not_change_energy`, `test_meter_inactive_creates_no_token_and_keeps_credit` (`token.failed(reason_code=meter_inactive, final=true)`, wallet still holds the amount), `test_meter_reassigned_creates_no_token`, `test_decommissioned_meter_creates_no_token`, `test_reversed_top_up_creates_no_token`, `test_insufficient_wallet_creates_no_token_and_raises_failed_event`, `test_crash_after_posting_before_commit_leaves_nothing` (fault hook: no token, no posting), `test_two_top_ups_same_meter_get_consecutive_counters` (two events concurrently: counters 1 and 2, no gap or duplicate), `test_late_flag_still_issues` (`payment.confirmed.late=true`). `test_toctou.py::test_toctou_reassignment_vs_issuance_200_interleavings` (`track_commit_timestamp=on` in the test PostgreSQL; a barrier randomises which of issuance and `UPDATE meters SET customer_id` starts first and with what delay; after each run assert: if the reassignment committed before the token's transaction then no token exists, else the token's customer is the old owner and the invariant `meter_tokens_guard` holds; 200 iterations, zero violations).
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/meters/test_issue_token.py tests/integration/meters/test_toctou.py -q`. Expected: ImportError for `IssueToken`.
- [ ] **3. Implement.** `IssueToken.__call__(event: EventEnvelope, uow) -> None` as handler `meters.on_payment_confirmed`. Inside the framework's transaction: `repo.lock_for_issue(top_up_id)`; skip silently when a token already exists or the top-up status is not `confirmed`; check `meter.customer_id == top_up.customer_id and meter.status == "active"` else add `TokenFailed(token_id=None, reason_code="meter_inactive", final=True, status="failed")` and return; compute `purchase_minor`; `token_id = ids.new_id()`; `counter = repo.next_counter(meter_id)`; `LedgerService.post(PostingRequest(posting_key=f"token:{token_id}:purchase", kind="energy_purchase", lines=[debit wallet, credit revenue_energy], source_top_up_id=top_up_id))`, catching the wallet CHECK violation as `InsufficientWallet` and adding `TokenFailed(reason_code="insufficient_wallet")` without raising; build the instruction, sign and encrypt it into `token_enc`; insert the token (`expires_at = now + TOKEN_TTL_HOURS`, `next_attempt_at = now`); add `TokenRequested`. `TokenService.issue_for_payment` wraps the same code for tests.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(meters): token issuance with ownership re-check, counter and purchase posting`

### Task 06.4: Outbound adapter, sender, retry schedule and the unknown state

- [ ] **1. Failing tests.** `unit/meters/test_sender_logic.py`: `test_accepted_sets_sent_and_schedules_next_attempt`, `test_unreachable_sets_pending_with_error_and_backoff`, `test_retry_after_header_extends_delay`, `test_unknown_rejection_reason_is_treated_as_unreachable`. `integration/meters/test_sender.py` (`FakeMeterGateway`, `FrozenClock`): `test_gateway_timeout_then_success_issues_one_token` (TC-FR-05-1: same token id, counter and payload on both pushes), `test_resend_reuses_token_id_counter_and_payload` (TC-FR-05-11), `test_retry_schedule_caps_and_stops_at_72h` (clock advanced through the schedule: delays 30 s up to 30 minutes, no push closer than the delay, no push after 72 h, status `unknown`, `token.failed(status=unknown, final=false)`), `test_pending_at_horizon_goes_through_sent_to_unknown`, `test_no_hot_loop_when_gateway_down` (1,000 poll ticks inside one minute cause at most one push per token), `test_claim_bumps_next_attempt_before_push` (crash between claim and push: the token is retried after its backoff, no duplicate token), `test_ack_lost_never_creates_second_token` (TC-FR-05-2: push accepted, no ack, run to horizon: one token row, status `unknown`), `test_definitive_rejection_on_push_sets_failed_and_reverses` (the worker path, shares code with Task 06.6), `test_endpoint_resolved_from_gateway_map`, `test_push_never_runs_inside_transaction` (connection `in_atomic_block` is false during the fake's call), `test_worker_crash_resumes_from_persisted_state` (TC-FR-05-4). `test_http_gateway.py` (httpx mock transport): `test_wire_format_matches_t2_schema`, `test_timeout_maps_to_unreachable`, `test_422_rejected_maps_reason`, `test_429_maps_retry_after`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/unit/meters/test_sender_logic.py tests/integration/meters/test_sender.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `SendDueTokens` (poller `meters.send_due_tokens`): short transaction claiming up to `TOKEN_SENDER_BATCH` rows `status IN ('pending','sent') AND next_attempt_at <= now()` with `skip_locked`, bumping `attempts` and `next_attempt_at = now + retry_delay(attempts)`; commit; for each token decrypt the payload and call `MeterGateway.push_token(gateway_ref, TokenInstruction(...))`; second short transaction records the result: `Accepted` sets `sent` and `sent_at`; `Unreachable` sets `pending` (from `sent`) or keeps `pending` with `last_error` and honours `retry_after_s`; `Rejected` calls the shared rejection path (Task 06.6). `ExpireUnknown` (cron every minute): tokens in `pending` or `sent` with `now >= expires_at` become `unknown` (a `pending` token first goes to `sent`), `last_error='horizon_reached'`, add `TokenFailed(status="unknown", final=False)`. `HttpMeterGateway` (httpx, `TOKEN_PUSH_TIMEOUT_S`, optional bearer from `GATEWAY_PUSH_TOKEN`) resolves the endpoint from `GATEWAY_PUSH_ENDPOINTS[gateway_id]` or the default; `FakeMeterGateway` is scriptable (accept, timeout, reject).
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(meters): outbound gateway adapter, due-token sender and unknown state`

### Task 06.5: Gateway authentication, acknowledge and heartbeat routes

- [ ] **1. Failing tests.** `integration/meters/test_acknowledge_api.py` (role `hub_ingest`; key rows inserted by a fixture with the HMAC pepper): `test_applied_ack_sets_acknowledged_and_emits_token_issued`, `test_ack_on_pending_goes_through_sent`, `test_duplicate_ack_is_idempotent` (second call 200, status unchanged, no second outbox row; TC-FR-05-11), `test_ack_from_other_site_is_404_and_no_effect`, `test_counter_mismatch_is_422`, `test_counter_replayed_result_acknowledges_the_token` (the counter belongs to our token, Plan note 4), `test_late_ack_after_unknown_acknowledges` (status `unknown` becomes `acknowledged`, `token.issued` emitted), `test_ack_after_failed_is_409_and_counted` (metric `token_ack_after_failure_total`, no state change), `test_rejected_ack_sets_failed_and_emits_token_rejected` (no ledger call from ingest), `test_ack_requires_scope_tokens_acknowledge`, `test_integrator_key_is_403`, `test_source_ip_outside_cidr_is_401`, `test_unknown_token_id_is_404`. `test_heartbeat_api.py`: `test_heartbeat_updates_last_heartbeat_and_firmware`, `test_heartbeat_ack_has_server_time_and_300_seconds`, `test_heartbeat_requires_scope_gateway_heartbeat`, `test_heartbeat_key_without_gateway_row_is_403`, `test_gateway_tier_throttle_300_per_minute`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/meters/test_acknowledge_api.py tests/integration/meters/test_heartbeat_api.py -q`. Expected: 404 on routes.
- [ ] **3. Implement.** `ApiKeyAuthentication` (DRF) reads `X-API-Key`, parses `sgh_<env>_<keyid>_<secret>`, computes `HMAC-SHA256(pepper, raw key)`, calls `hub.verify_api_key(key_id, hash, ip)` and returns a `Principal(kind="gateway", subject_id=api_key_id, scopes, site_ids)`; integrator keys are refused here. `acknowledge_token` view (`@operation("acknowledgeToken")`): load the token with its meter and site, 404 when the site is not in `principal.site_ids`, validate `TokenAckSerializer` (`counter` must equal the token counter), apply the transition rules: `applied` or `counter_replayed` moves `pending`, `sent`, `unknown` to `acknowledged` (`pending` via `sent`), sets `acknowledged_at`, adds `TokenIssued`; `acknowledged` returns 200 unchanged; `rejected` with a reason sets `failed`, `last_error`, adds `TokenRejected`; `failed` or `cancelled` returns 409. `gateway_heartbeat` finds the gateway by `gateways.api_key_id = principal.subject_id`, updates `last_heartbeat_at` and `firmware`, returns `{server_time, next_heartbeat_seconds: 300}`. Both views use the `gateway` throttle tier.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(meters): gateway acknowledge and heartbeat routes with key authentication`

### Task 06.6: Rejection, reversal, non-delivery and manual retry

- [ ] **1. Failing tests.** `integration/meters/test_rejection_reversal.py` (role `hub_worker`): `test_token_rejected_event_reverses_posting_and_emits_final_failure` (status `failed`, reversal posting, wallet restored, `token.failed(final=true, status=failed)`; TC-FR-05-3), `test_reversal_is_idempotent_on_redelivery`, `test_unknown_token_is_never_reversed_automatically` (run the horizon, then the full sweeper set: no reversal posting), `test_confirm_non_delivery_cancels_and_reverses` (evidence object with kind `no_application_in_register`), `test_confirm_non_delivery_requires_unknown_status`, `test_confirm_non_delivery_runs_only_on_worker_role`. `test_token_api.py`: `test_customer_lists_only_own_tokens` (row-level security), `test_support_agent_site_scope`, `test_token_code_is_masked`, `test_retry_token_enqueues_job_and_returns_202`, `test_retry_job_resets_next_attempt_and_keeps_token_id_and_counter`, `test_retry_on_acknowledged_is_409`, `test_retry_requires_operations_engineer`, `test_get_token_404_for_other_customer`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/meters/test_rejection_reversal.py tests/integration/meters/test_token_api.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `ReverseRejectedToken` handler `meters.on_token_rejected`: lock the token, require `failed`, `LedgerService.reverse_token_purchase(token_id, posting_key=f"token:{id}:reverse")`, add `TokenFailed(final=True, status="failed", reason_code=<reason>)`; the sender's push rejection runs the same method in its own transaction (setting `failed` first). `TokenService.confirm_non_delivery` requires `unknown`, sets `cancelled`, reverses, emits `TokenFailed(status="failed", final=True, reason_code="non_delivery_confirmed")`; it is exposed only to the slice 14 executor and asserts the worker role. `RetryNow` job `meters.retry_now`: only `pending`, `sent`, `unknown`; sets `status` to `pending` where allowed and `next_attempt_at = now()`, never a new token. `TokenViewSet` serves `listTokens`, `getToken`, `retryToken` (enqueue and return the current token, 202) and `listMeterTokens`; `TokenSerializer.token_code_masked` shows only a hash prefix of the payload.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(meters): rejection reversal, non-delivery confirmation, manual retry and token reads`

### Task 06.7: Missing-token sweeper, property test, end to end with the simulator

- [ ] **1. Failing tests.** `integration/meters/test_reemit_missing.py`: `test_confirmed_top_up_without_token_after_5_minutes_is_reemitted`, `test_top_up_already_handled_is_not_reemitted` (inbox row exists, including the ineligible-meter case), `test_recent_top_up_is_left_alone`. `property/test_token_saga_schedules.py::test_customer_never_charged_without_token_or_reversal` (hypothesis stateful: random schedules of timeout, accept, reject, ack-lost, duplicate event, worker crash, clock jumps over the retry horizon; invariants after every step: sum of all ledger entries is zero, no top-up has two tokens, counters per meter are unique and increasing, and every `energy_purchase` posting belongs to a token that is `pending`, `sent`, `acknowledged` or `unknown` or has a reversal; TC-FR-05-8). `e2e/test_token_with_simulator.py` (`make up` with `meter-sim`): `test_paid_top_up_becomes_acknowledged_token` , `test_simulator_timeout_mode_retries_then_acks`, `test_simulator_reject_mode_reverses`, `test_replayed_token_is_rejected_by_simulator_and_not_credited_twice` (TC-FR-05-7: resend a token whose counter the meter has applied; the ack is `counter_replayed`, the token stays `acknowledged`, one posting), `test_noack_mode_ends_unknown_after_horizon_with_accelerated_clock`. `contract/test_openapi_conformance.py::test_schemathesis_tokens_and_gateway`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/meters/test_reemit_missing.py tests/property/test_token_saga_schedules.py -q`. Expected: ImportError, then invariant failures if any path leaks.
- [ ] **3. Implement.** `ReemitMissing` (cron every minute): for providers in settings, `PaymentService.confirmed_between(now - 14 days, now - TOKEN_MISSING_SWEEP_MINUTES, provider)`; for each top-up without a token whose `payment.confirmed` event id (`find_event_id`) has no `consumer_inbox` row for `meters.on_payment_confirmed`, call `OutboxReemitter.reemit`. Wire everything in `meters/application/wiring.py` and `bootstrap`; compose gives `GATEWAY_PUSH_DEFAULT_URL=http://meter-sim:9201` and the keys from a mounted file.
- [ ] **4. Run** the three test files until green.
- [ ] **5. Commit:** `feat(meters): missing-token sweeper, saga property test and simulator end-to-end tests`

## Verification

`make verify SLICE=06` runs from the repository root; each command must exit 0.

1. `cd backend && uv run ruff check . && uv run mypy --strict src/hub/meters src/hub/ledger && uv run lint-imports` (zero findings).
2. `cd backend && uv run pytest tests/unit/meters -q` (all passed).
3. `cd backend && uv run pytest tests/integration/meters -q` (all passed, including 200 TOCTOU interleavings).
4. `cd backend && uv run pytest tests/property/test_token_saga_schedules.py -q` (hypothesis, 200 examples, no failure).
5. `cd backend && uv run pytest tests/contract/test_openapi_conformance.py -q -k "tokens or gateway"` (no 5xx, schema conformance).
6. `make up && cd backend && uv run pytest tests/e2e/test_token_with_simulator.py -q` (all passed).
7. `python -m pytest tests/test_alignment.py tests/test_schema_in_postgres.py -q && python tools/check_traceability.py` from the repository root (green).
8. `python tools/check_django_config.py` (clean) and `cd backend && grep -rn "push_token" src/hub --include=*.py | grep -v meters` (no output: no other module sends commands to site systems).

## Demo script

1. `make up`; seed one active meter with a customer and a gateway key (fixtures command from slice T3).
2. Run a top-up through the provider stub (slice 04 demo). Within seconds `GET /api/v1/tokens` as the customer shows one token `acknowledged` with counter 1; the simulator control `stats` shows one applied token.
3. Simulator mode `token_timeout`; run a second top-up: the token stays `pending` with `attempts` rising on the 30 s, 1 minute, 2 minute schedule; switch the mode off and observe `acknowledged` with the same token id.
4. Mode `token_reject{reason:"invalid_meter"}`; a third top-up ends `failed`; the wallet shows the amount back and the ledger has a `reversal` row; `token.failed` has `final=true`.
5. Mode `token_noack`; fourth top-up; advance the accelerated simulator clock past 72 hours: status `unknown`, no reversal, a `token.failed(status=unknown)` event.
6. Call the acknowledge route for that token with `applied`: it becomes `acknowledged`.
7. As an operations engineer `POST /api/v1/tokens/{id}/retry` on a pending token: 202, `next_attempt_at` is now, token id and counter unchanged.
8. Replay the acknowledge call from step 6 with a key bound to another site: 404.

## Done criteria

Requirements: FR-05 (AC-FR-05-1, 05-2, 05-4, 05-6 fully; 05-3 and the reversal half of 05-5 depend on slice 14's executor, `confirm_non_delivery` is delivered here).

Test cases implemented and passing: TC-FR-05-1 (06.4, 06.7), TC-FR-05-2 (06.4), TC-FR-05-3 (06.6), TC-FR-05-4 (06.4), TC-FR-05-5 (06.4), TC-FR-05-6 (06.3), TC-FR-05-7 (06.7), TC-FR-05-8 (06.7), TC-FR-05-9 (06.3), TC-FR-05-10 (06.1, 06.3), TC-FR-05-11 (06.4, 06.5). TC-FR-07-4 (heartbeat silence raising `site_offline`) belongs to slice 08; this slice provides only the route that records the heartbeat.

## Plan notes for the tech lead

1. **No schema function reverses an `energy_purchase`.** D-07 requires the posting to be reversed on a definitive rejection, but `hub.reverse_ledger_transaction` accepts only a received top-up and `post_ledger_transaction` cannot credit a wallet under an automatic kind. I added `hub.reverse_token_purchase` by migration (Task 06.2), callable by `hub_worker` only. If you refuse a schema addition, the reversal would need an approval per token, which contradicts D-07.
2. **Reversal runs on the worker, not in the acknowledge request.** The ingest role must not be able to refund wallets. The acknowledge route only marks `failed` and emits `token.rejected` (not in docs/03 section 4; the catalogue needs a row), and a worker consumer reverses. The same reason makes retry and replay endpoints enqueue jobs (`hub_api` has no UPDATE on `meter_tokens`).
3. **API-key authentication is slice 15 in the build order** but the acknowledge and heartbeat routes need it now. This slice adds only the verification path for gateway keys over `hub.verify_api_key`. docs/03 says `Authorization: ApiKey ...`, the OpenAPI document says `X-API-Key`; I followed the OpenAPI. I assume the gateway `Principal.subject_id` is the api key id (docs/03 does not say).
4. **`counter_replayed` acknowledgement.** The schema forbids two tokens with one counter on a meter, so a replayed counter means the meter applied our token earlier (its acknowledgement was lost); I treat it as acknowledged. The T2 simulator applies the same meaning.
5. **Meter reassigned or deactivated at issuance.** docs/10 case T3b and TC-FR-05-9 say the credit goes to `suspense`; docs/03 section 6.2 says the wallet keeps the credit and a final `token.failed` and a reconciliation item follow. I followed docs/03 (the money is still the customer's and the wallet is not a loss).
6. **Gateway endpoint for the push.** `gateways` has no URL column and no document defines the push wire format; I followed slice T2's receiver (`POST /gateway/push/tokens`) and configured endpoints in settings.
7. **Token payload format** is my design (signed credit instruction, encrypted at rest); no document defines a real meter format.
8. **`confirm_non_delivery` runs on the worker** (it needs `reverse_token_purchase`), so slice 14's approval executor must dispatch there; docs/03 section 2.12 runs executors in the approval's request transaction on `hub_api`.
9. **Heartbeat** has `x-fr` FR-06 and FR-07 in the OpenAPI document, not FR-05; I build the route here as assigned, and the offline derivation is slice 08.
10. **Ack after `failed`.** A gateway claiming `applied` for a token that was already failed and reversed is a contradiction with money impact; the route answers 409 and counts it, but no `reconciliation_items.kind` fits it, so no exception event is raised (a metric alert only).
