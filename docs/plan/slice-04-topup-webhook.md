# Slice 04: Top-up initiation and the exactly-once webhook

- **Goal:** the `payments` module: top-up initiation with the quoted tariff and energy snapshot and a 7-day idempotency window; cancel; the synchronous provider webhook (signature over `timestamp.event_id.body`, 300 s window, `provider_events` with both unique keys, payer check, pull verification at 100 USD or more, late credit with `late`, mismatch to `suspense`, reversals with `chargeback_receivable`, invalid signatures to the quarantine log only); the sweepers (`expire_top_ups`, retry of unconfirmed initiation, `pending_unknown` poll); the staff webhook-log operations. This is the critical exactly-once path (invariant 3, NFR-05).
- **Depends on:** slice 00 (shared kernel, `UnitOfWork`, `call_function`, problem handler, metrics helpers), slice 01 (`Principal`, `Authorizer`, permission class), slice 02 (`CustomerDirectory`, plans, meters, wallet owner), slice 03 (`LedgerService`, system accounts, `ensure_wallet`), slice T1 ([provider stub](slice-T1-provider-stub.md), the test double for the provider). Slice 05 later supplies the real `JobQueue`; until then replay is tested with a recording fake.
- **Requirements served:** FR-02, FR-03 (FR-04 and NFR-05 exercised, not owned; NFR-02 ack latency is smoke-tested here, load-tested in slice 17).
- **Size:** L, 10 days for a cheaper agent.
- **Companion files:** [fe-04-portal-topup.md](frontend/fe-04-portal-topup.md).
- **Spikes or open items:** **[open]** pull-verification threshold, default 100 USD or more (`PULL_VERIFY_THRESHOLD_MINOR = {"USD": 10000, "CDF": 28000000}`, assuming 2,800 CDF per USD; a setting). **[open]** provider fee lines: no fee in the envelope, so no fee posting in v1. One migration is instructed here (Task 04.7) because the frozen schema has no automatic path to `suspense` and no `hub_ingest` access to ledger account ids (Plan notes 1 and 2).

## Read first

- `docs/00-shared-context.md#7.3 Money rules` (D-05, D-06, D-08, D-09, D-12) and `#7.2 Canonical state machines`
- `docs/adr/0004-synchronous-webhook-exactly-once.md`
- `docs/03-code-architecture.md#2.4 payments (FR-02, FR-03)`, `#6.1 Top-up confirmation (FR-02, FR-03, FR-04; NFR-01, NFR-05)`, `#7.7 API layer conventions (Django REST framework)`
- `db/schema.sql`: `hub.provider_events`, `hub.webhook_quarantine`, `hub.top_ups` and `hub.top_ups_guard`, `hub.post_ledger_transaction`, `hub.reverse_ledger_transaction`, grants of `hub_ingest`
- `docs/10-test-strategy.md#2.2 Idempotency and concurrency`
- `api/openapi.yaml` operations `createTopUp`, `listTopUps`, `getTopUp`, `cancelTopUp`, `receiveProviderWebhook`, `listWebhookEvents`, `getWebhookEvent`, `replayWebhookEvent`
- `docs/plan/slice-T1-provider-stub.md` (signing, scenario modes, reversal id `-R`)

## Global constraints

- The signed string is `{X-Webhook-Timestamp}.{event_id}.{raw body}`; header `X-Webhook-Signature: v1=<hex>`; HMAC-SHA256; constant-time compare; timestamp is Unix seconds, tolerance 300 s; `X-Webhook-Id` must equal `body.event_id`.
- Webhook body cap 64 KB, checked before the body is read. The webhook view is a plain Django function view and never DRF; it reads `request.body` once.
- Invalid signatures answer 401 `invalid_signature`; valid signature with a stale timestamp answers 400 `stale_timestamp`; neither stores anything in `provider_events` (a `webhook_quarantine` row only, body hash and length, never the body).
- Uniqueness is `(provider, event_id)` and `(provider, provider_txn_id)` in PostgreSQL only. Redis is a hint, and it is written only after commit, never before.
- One transaction (`with uow:`) around T1 only: event row, `top_ups` row lock, state change, ledger post, outbox row. Signature check, payer pre-read, pull verification and the Redis hint are outside it. `ATOMIC_REQUESTS` stays off.
- Retry only deadlock and serialization failures (`shared.db.retry_transient`, 3 attempts). Anything else: 503, nothing committed.
- Late confirmation credits once and sets `late`; only mismatch, erased customer, decommissioned meter, pull disagreement or a second payment go to `suspense` with an exception (D-05). Reversals post reversing entries, shortfall to `chargeback_receivable` (D-06).
- Initiation: `Idempotency-Key` mandatory, 7-day window, same key with another body is 422 `idempotency_key_reuse`; tariff and `energy_wh` stored at initiation, never re-quoted, rounded down to the watt-hour (D-08).
- No staff role may initiate (D-14); a customer's id comes from the token.
- Money is integer minor units (two decimals for both currencies); ids from `IdGenerator`, time from `Clock`; never log bodies, signatures, secrets or full phone numbers.
- Secrets: `PROVIDER_WEBHOOK_SECRETS = {provider: [current, previous]}`, both accepted (24 h overlap).

## Review focus

1. **A hint written before commit loses money.** If the Redis key is set before T1 and the commit fails with 503, the provider's retry gets `duplicate` for a payment never recorded. Pinned by `test_hint_is_not_written_when_transaction_fails` (Task 04.5).
2. **Fifty parallel identical deliveries:** one posting, one outbox row, 49 `duplicate`, zero 5xx. Pinned by `test_fifty_parallel_identical_webhooks_credit_once` (Task 04.6, owner of the concurrency suite).
3. **A forged event cannot squat a real event id**, and tampering with the signed string fails. Pinned by `test_invalid_signature_does_not_squat_event_id` (Task 04.4).
4. **Late and mismatched payments are never dropped:** late credits once with `late`; wrong payer, amount, currency, erased customer or decommissioned meter posts to `suspense` with an exception. Pinned by `test_late_confirmation_after_expiry_credits_once` and `test_mismatches_route_to_suspense` (Task 04.8, mapping logic) and `test_suspense_receipt_posts_clearing_to_suspense` (Task 04.7).
5. **Reversal after the energy was issued:** the shortfall goes to `chargeback_receivable`. Pinned by `test_reversal_after_energy_issued_posts_shortfall` (Task 04.9).

## Files

Paths relative to `backend/src/hub/` unless stated; tests relative to `backend/tests/`.

- `payments/domain/top_up.py`: `TopUp` entity and `TopUpStatus` constants; pure transition rules mirroring `top_ups_guard`.
- `payments/domain/errors.py`: domain errors (`AmountOutOfRange`, `TopUpLimit`, `InvalidTopUpState`, `InvalidSignature`, `StaleTimestamp`, `MalformedWebhook`).
- `payments/domain/signature.py` (pure HMAC verify) and `routing.py` (`classify_confirmation`).
- `payments/contracts.py`: `PaymentService`, `TopUpView`, `InitiateTopUp`, `ConfirmedPaymentRef`, `ProviderEvidence`.
- `payments/events.py`: `TopUpInitiated`, `PaymentConfirmed`, `PaymentFailed`, `PaymentReversed`, `ReconciliationExceptionRaised`.
- `payments/application/ports.py`: `ProviderGateway`, `EligibilityChecker`, `JobQueue` re-export.
- `payments/application/`: `initiate_top_up.py`, `cancel_top_up.py`, `confirm_payment.py`, `replay_event.py`, `sweepers.py`, `wiring.py`.
- `payments/infrastructure/`: `models.py` (unmanaged), `repositories.py`, `mappers.py`, `provider_http.py`, `eligibility.py`, `fake_provider.py`.
- `payments/api/serializers.py` (`TopUpCreateSerializer`, `TopUpSerializer`, `WebhookEventLogSerializer`), `views.py` (`TopUpViewSet`, `WebhookEventViewSet`), `webhooks.py` (`provider_webhook`), `urls.py`; `urls/api.py` and `urls/webhooks.py` mount them.
- `ledger/contracts.py` and `ledger/infrastructure/writer.py` (modify): add `LedgerService.post_suspense(req: SuspenseRequest, *, uow)`.
- `db/migrations/NNNN_payments_ingest_support.sql` (NNNN is the next free number) and `backend/src/hub/shared/migrations/00NN_payments_ingest_support.py` (one `RunSQL` calling `apply_sql_file`): grants and `hub.post_suspense_receipt`.
- Tests: named in each task, under `unit/payments`, `integration/payments`, `e2e`.

## Interfaces

**Consumes** (earlier slices; signatures as in docs/03 and the slice plans, field lists marked as assumptions):

```python
# slice 02 customers.contracts
class CustomerDirectory(Protocol):
    def get(self, customer_id: UUID) -> CustomerView: ...                  # CustomerView.status in active|suspended|closed|erased
    def current_plan(self, customer_id: UUID) -> PlanView: ...             # assumed fields: id, min_topup_minor, max_topup_minor, currency
    def quote_energy(self, customer_id: UUID, amount: Money, at: datetime) -> EnergyQuote: ...  # assumed fields: plan_tariff_id, tariff_minor_per_kwh, energy_wh
    def meter_for_customer(self, customer_id: UUID) -> MeterRef | None: ...  # assumed fields: id, status, site_id
    def customer_for_meter(self, meter_id: UUID) -> UUID | None: ...       # None when decommissioned or unassigned
# slice 03 ledger.contracts
class LedgerService(Protocol):
    def post(self, req: PostingRequest, *, uow: UnitOfWork) -> PostingResult: ...
    def reverse(self, original_transaction_id: UUID, *, posting_key: str, provider_event_id: UUID | None,
                approval_id: UUID | None, uow: UnitOfWork) -> ReversalResult: ...
    def ensure_wallet(self, customer_id: UUID, currency: str, *, uow: UnitOfWork) -> UUID: ...
# slice 00 shared: retry_transient(max_attempts=3); JobQueue.enqueue(name, payload, *, delay, dedupe_key)
# slice 01 identity.contracts: Principal, Authorizer.require(principal, permission)
```

**Produces:**

```python
# payments/contracts.py
class PaymentService(Protocol):
    def initiate_top_up(self, cmd: InitiateTopUp, *, idempotency_key: str) -> TopUpView: ...
    def get_top_up(self, top_up_id: UUID) -> TopUpView: ...
    def confirmed_between(self, start: datetime, end: datetime, provider: str) -> list[ConfirmedPaymentRef]: ...
    def provider_evidence(self, top_up_id: UUID) -> ProviderEvidence: ...     # pull stored in status_pull_at; slice 14

# payments/application/ports.py
class ProviderGateway(Protocol):               # docs/03 section 5
    def initiate_collection(self, req: CollectionRequest) -> CollectionAck: ...   # idempotent on req.reference (the top-up id)
    def verify_webhook(self, headers: Mapping[str, str], body: bytes, *, now: datetime) -> VerifiedEvent: ...
    def fetch_status(self, provider_txn_id: str) -> ProviderTxnStatus: ...
@dataclass(frozen=True, slots=True)
class VerifiedEvent:
    event_id: str; type: Literal["payment.confirmed","payment.failed","payment.reversed"]
    occurred_at: datetime; provider_reference: str; external_reference: UUID
    amount: Money; payer_msisdn: str | None; failure_code: str | None; payload: Mapping[str, Any]
# ProviderTxnStatus: status in successful|pending|failed|reversed|not_found, amount, payer_msisdn

# ledger/contracts.py (added here)
@dataclass(frozen=True, slots=True)
class SuspenseRequest:
    posting_key: str; provider: str; amount: Money; provider_event_id: UUID; source_top_up_id: UUID | None
class LedgerService(Protocol):
    def post_suspense(self, req: SuspenseRequest, *, uow: UnitOfWork) -> PostingResult: ...
```

Events (outbox, payload fields as docs/03 section 4): `topup.initiated`, `payment.confirmed` (`top_up_id, customer_id, meter_id, amount_minor, currency, provider, provider_txn_id, confirmed_at, late, quoted_energy_wh, tariff_version, ledger_transaction_id`), `payment.failed`, `payment.reversed` (`reversed_minor, shortfall_minor, ledger_transaction_id, chargeback_transaction_id`), `reconciliation.exception` (catalogue fields with `item_id` and `batch_id` null, plus added `provider_event_id` and `reason_code`; Plan note 5). Jobs in `wiring.py`: cron `payments.expire_top_ups`, `payments.retry_initiation` (every minute), `payments.sweep_pending_unknown` (every 2 minutes), job `payments.replay_event`. Settings: `TOPUP_EXPIRY_MINUTES=15`, `TOPUP_MAX_PER_METER_24H=10`, `TOPUP_MAX_PER_PAYER_1H=5`, `PULL_VERIFY_THRESHOLD_MINOR`, `PULL_TIMEOUT_S=1.5`, `PROVIDER_INITIATE_TIMEOUT_S=10`, `PROVIDER_WEBHOOK_SECRETS`, `WEBHOOK_MAX_BODY_BYTES=65536`, `PENDING_UNKNOWN_POLL_MINUTES=30`.

## Tasks

### Task 04.1: Models, repositories and the top-up domain

- [ ] **1. Failing tests.** `unit/payments/test_top_up_domain.py`: `test_allowed_transitions_match_database_guard` (the pairs copied from `top_ups_guard`, all 64 combinations), `test_confirm_after_expiry_sets_late` (`top_up.confirm(now)` on expired returns `late is True`), `test_confirm_from_initiated_passes_through_pending` (returns steps `["pending", "confirmed"]`), `test_failed_cannot_confirm` (raises `InvalidTopUpState`). `integration/payments/test_models_alignment.py`: `test_models_match_schema` runs `check_db_alignment` for the `payments` app.
- [ ] **2. Run.** `cd backend && DJANGO_SETTINGS_MODULE=hub.settings.test uv run pytest tests/unit/payments/test_top_up_domain.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `domain/top_up.py` with `TopUpStatus` string constants equal to the schema enum, `can_transition(a, b) -> bool`, `TopUp.confirm(now) -> ConfirmOutcome(steps: list[str], late: bool)`. `infrastructure/models.py` with columns exactly as `db/schema.sql`, `managed = False`. `repositories.py`: `TopUpRepository.add`, `get`, `lock_by_provider_ref(provider, ref)` (`select_for_update(of=("self",))`, calls `uow.require_active()`), `peek_by_provider_ref` (no lock), `update_fields(top_up_id, **cols)` limited to the `hub_ingest` column grants, `count_for_meter_since`, `count_for_payer_since`, `list_expirable`; `ProviderEventRepository.insert_if_new(...) -> UUID | None` using raw `INSERT ... ON CONFLICT DO NOTHING RETURNING id` (an ORM `get_or_create` is forbidden here), `mark(event_id, status, last_error)`, `quarantine(...)`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(payments): unmanaged models, repositories and top-up state rules`

### Task 04.2: Initiation use case with quote, limits and idempotency

- [ ] **1. Failing tests.** `unit/payments/test_initiate_top_up.py` (fakes): `test_stores_quote_snapshot` (amount 50000 at tariff 25000 per kWh stores `energy_wh == 2000` and the quote's `plan_tariff_id`), `test_energy_rounds_down` (1001 at 3000 gives 333), `test_amount_below_plan_minimum_raises_amount_out_of_range`, `test_meter_not_active_raises`, `test_meter_of_another_customer_raises_not_found`, `test_eleventh_top_up_for_meter_in_24h_raises_topup_limit`, `test_sixth_top_up_for_payer_in_1h_raises_topup_limit`, `test_provider_refusal_sets_failed`, `test_provider_timeout_sets_pending_unknown`, `test_provider_accept_sets_pending_and_provider_called_once`. `integration/payments/test_initiation_api.py`: `test_same_key_same_body_replays_and_provider_called_once`, `test_same_key_different_body_is_422_idempotency_key_reuse`, `test_missing_key_is_400_idempotency_key_required`, `test_key_expires_after_7_days` (clock +7 days and 1 s: a new top-up), `test_staff_token_is_403` (all four staff roles), `test_customer_id_in_body_is_ignored_for_customers`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/unit/payments/test_initiate_top_up.py -q`. Expected: ImportError, then assertion failures once stubs exist.
- [ ] **3. Implement.** `InitiateTopUp.__call__(cmd: InitiateTopUp, principal: Principal) -> TopUpView`. Transaction T0 (`with uow:`): resolve the customer (customer principal; integrator needs `cmd.customer_id` inside its key scope), check `meter_for_customer(customer).id == cmd.meter_id` and `active`, plan limits (422 `amount_out_of_range`), counters (429 `topup_limit`), `ensure_wallet`, `quote_energy`, insert `top_ups` (`initiated`, `provider_ref = str(top_up_id)`, `expires_at = now + TOPUP_EXPIRY_MINUTES`), add `topup.initiated`, commit. Then, outside any transaction, `ProviderGateway.initiate_collection` (timeout `PROVIDER_INITIATE_TIMEOUT_S`); a second short transaction sets `pending` (accepted), `failed` (definite refusal) or `pending_unknown` (timeout or 5xx). The view uses `@idempotent(scope="POST /api/v1/top-ups", window=timedelta(days=7))`; the stored response is the `TopUp` body with 202. `TopUpCreateSerializer` validates `payer_msisdn` against `^\+[1-9][0-9]{7,14}$`.
- [ ] **4. Run** unit and integration tests until green.
- [ ] **5. Commit:** `feat(payments): top-up initiation with quote snapshot, limits and 7-day idempotency`

### Task 04.3: Read and cancel operations

- [ ] **1. Failing tests.** `integration/payments/test_initiation_api.py`: `test_get_top_up_customer_sees_only_own` (404 for another's id), `test_list_top_ups_cursor_pagination`, `test_integrator_scope_topups_read_required`, `test_cancel_pending_sets_cancelled`, `test_cancel_confirmed_is_409_invalid_state_transition`, `test_cancel_twice_is_idempotent_200`, `test_masked_payer_number_in_response`.
- [ ] **2. Run** `cd backend && uv run pytest tests/integration/payments/test_initiation_api.py -q -k "get or list or cancel"`. Expected: 404 on routes.
- [ ] **3. Implement.** `TopUpViewSet` (`getTopUp`, `listTopUps`), `CancelTopUp.__call__(top_up_id, principal)` (lock row, allowed from `initiated` and `pending` only, else `InvalidTopUpState` 409; already `cancelled` returns the same view), serializer `TopUpSerializer` maps `provider_ref` to `provider_reference`, adds `payer_msisdn_masked`, `quoted_tariff`, and `fee` as zero. Handlers carry `@operation("<operationId>")`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(payments): get, list and cancel top-ups`

### Task 04.4: Signature verification, quarantine and the webhook shell

- [ ] **1. Failing tests.** `unit/payments/test_signature.py`: `test_signed_string_is_timestamp_event_id_body` (secret `b"k"`, timestamp `1700000000`, id `evt_1`, body `b'{"a":1}'` equals the HMAC of `1700000000.evt_1.{"a":1}` prefixed `v1=`), `test_tampered_body_fails`, `test_tampered_event_id_fails`, `test_previous_secret_accepted_during_overlap`, `test_300_seconds_accepted_301_stale`. `integration/payments/test_webhook_signature.py`: `test_bad_signature_is_401_and_quarantined_not_stored` (zero `provider_events`; one `webhook_quarantine` row, `reason='bad_signature'`, body hash only), `test_stale_timestamp_is_400`, `test_event_id_header_mismatch_is_400`, `test_unknown_provider_is_404`, `test_oversized_body_is_rejected_before_reading` (65,537 bytes), `test_malformed_json_with_valid_signature_is_422`, `test_invalid_signature_does_not_squat_event_id` (wrong-secret `evt_9`, then genuine `evt_9`: 200 `accepted`), `test_failure_counter_increments`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/unit/payments/test_signature.py tests/integration/payments/test_webhook_signature.py -q`. Expected: ImportError, then 404 on the route.
- [ ] **3. Implement.** `domain/signature.py`: `signed_string(timestamp: str, event_id: str, body: bytes) -> bytes`, `compute_signature(secret, timestamp, event_id, body) -> str`, `verify(secrets, header, timestamp, event_id, body, *, now: int, tolerance: int = 300) -> None` (signature first with `hmac.compare_digest` over each secret, then the window, so a forgery is 401, never 400). `provider_http.verify_webhook` calls it, parses JSON and builds `VerifiedEvent`. `api/webhooks.py::provider_webhook(request, provider)` rejects non-POST, enforces `WEBHOOK_MAX_BODY_BYTES` from `Content-Length` and a bounded read, calls `ConfirmPayment.run`, and maps errors to problem JSON (`InvalidSignature` 401, `StaleTimestamp` 400, `MalformedWebhook` 422, unknown provider 404). Quarantine rows are written in a short transaction with raw SQL and must never raise into the response; the counter increments first.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(payments): webhook signature verification and quarantine log`

### Task 04.5: ConfirmPayment core (success path, duplicates, outbox, hint after commit)

- [ ] **1. Failing tests.** `integration/payments/test_confirm_payment.py` (role `hub_ingest`, real PostgreSQL, fake provider, fakeredis): `test_confirmed_credits_wallet_once` (one transaction keyed `topup:<id>:confirm`, wallet balance equals the amount, top-up `confirmed` with `ledger_transaction_id`, event `processed`), `test_payment_confirmed_outbox_row_in_same_transaction` (catalogue fields, `late` false), `test_duplicate_event_returns_duplicate_with_no_second_effect`, `test_same_txn_different_event_id_is_duplicate` (S2), `test_confirm_from_initiated_passes_through_pending`, `test_response_body_is_webhook_ack`, `test_ledger_failure_rolls_back_event_row` (zero `provider_events`, 503 with `Retry-After`), `test_hint_is_not_written_when_transaction_fails`, `test_hint_hit_returns_duplicate_only_after_commit`, `test_unknown_reference_is_ignored_with_exception_event` (`ignored`, `statement_only`, 200), `test_failed_event_moves_pending_to_failed`, `test_failed_after_confirmed_does_not_downgrade` (event `processed`, `last_error='ignored_terminal'`).
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/payments/test_confirm_payment.py -q`. Expected: ImportError for `ConfirmPayment`.
- [ ] **3. Implement.** `ConfirmPayment.run(raw: bytes, headers: Mapping[str, str], provider: str) -> WebhookAck`, in the order the tests pin: (1) `verify_webhook`, outside any transaction; (2) Redis `GET provider_event:<provider>:<event_id>`: a hit returns `duplicate` (a pure read); (3) peek the top-up without a lock for the payer check and the pull decision (Task 04.8); (4) `retry_transient` around `with uow:` containing `insert_if_new` of the event (`provider_txn_id = data.provider_reference`, or `<ref>:failed` for `payment.failed`; `payment.confirmed` stored as `payment.succeeded`), where zero rows returns `duplicate`; then `lock_by_provider_ref(provider, str(external_reference))`, dispatch to `_confirm`, `_fail` or `_reverse`, mark the event `processed`; (5) after a clean exit set the hint (`SET NX`, TTL 72 h, errors swallowed); (6) return `accepted`. `_confirm` credit order is fixed because `post_ledger_transaction` reads the top-up's stored event: update `provider_event_id`, `provider_txn_id`, `payer_matched=True` (first `initiated` to `pending`); `LedgerService.post(PostingRequest(posting_key=f"topup:{id}:confirm", kind="topup_received", lines=[debit provider_clearing, credit customer_wallet], source_top_up_id=id))`; then `status='confirmed'`, `late`, `ledger_transaction_id`, `confirmed_at`; add `PaymentConfirmed`. `_fail` transitions only from `initiated`, `pending`, `pending_unknown`, else marks `ignored_terminal`. An unknown reference marks the event `ignored` and adds the exception event.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(payments): synchronous confirm-payment with exactly-once event recording`

### Task 04.6: The concurrency suite (critical)

- [ ] **1. Failing tests.** `integration/payments/test_webhook_concurrency.py` (marker `concurrency`; `django_db(transaction=True)`; each thread closes its connection; PostgreSQL `max_connections=200`): `test_fifty_parallel_identical_webhooks_credit_once` (50 threads released by a `threading.Barrier`, same signed bytes, real `ingest` URLConf; all 200; exactly one `accepted` and 49 `duplicate`; one `provider_events` row, one ledger transaction, one `payment.confirmed` outbox row, wallet credited once, p95 under 300 ms warm), `test_parallel_same_txn_different_event_ids_credit_once` (20 event ids, one `provider_reference`), `test_parallel_confirm_and_failed_never_downgrade` (30 races, ledger balanced), `test_redis_flush_between_deliveries_still_one_effect`, `test_crash_before_commit_leaves_no_partial_effect` (fault hook after the posting insert: zero rows everywhere; retry succeeds once), `test_crash_after_commit_before_response_retry_is_noop`. `e2e/test_topup_with_stub.py::test_four_gunicorn_workers_fifty_parallel` repeats the first test with four `be_webhooks` workers.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/payments/test_webhook_concurrency.py -q -m concurrency`. Expected: the tests of Task 04.5 already satisfy most; any failure here (a 500 from a unique-violation, or two postings) must be fixed in Task 04.5 code, not by loosening an assertion. If every test passes at once, run it 20 times (`for i in $(seq 20); do ...; done`) before moving on.
- [ ] **3. Implement.** Only fixes: the loser of a race blocks on the unique index until the winner commits, then sees zero rows from `ON CONFLICT DO NOTHING` and returns `duplicate`; no extra locking is added. Add the fault-hook parameter `ConfirmPayment(fault: FaultHooks = NoFaults())` with `after_ledger_post()` and `after_commit()` called only by tests. Add `webhook_ack_seconds` histogram and `webhook_events_total{provider,outcome}` counter.
- [ ] **4. Run** the suite 20 times in a loop; all green.
- [ ] **5. Commit:** `test(payments): fifty-way parallel delivery and crash-point suite for the webhook`

### Task 04.7: Migration for ingest support and the suspense receipt function

- [ ] **1. Failing tests.** `integration/payments/test_suspense_migration.py` (schema then the new migration): `test_hub_ingest_can_select_ledger_accounts_but_not_write`, `test_hub_ingest_cannot_read_wallet_balances`, `test_suspense_receipt_posts_clearing_to_suspense` (zero-sum entries, kind `topup_received`, no wallet touched), `test_suspense_receipt_requires_stored_success_event`, `test_suspense_receipt_is_idempotent_on_posting_key`, `test_suspense_receipt_rejects_non_success_event_type`, `test_function_owner_and_search_path` (`prosecdef`, owner `ledger_owner`, pinned `search_path`), `test_invariants_still_pass` (`db/invariant_tests.sql` and the baseline comparison).
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/payments/test_suspense_migration.py -q`. Expected: `permission denied for table ledger_accounts` and `function hub.post_suspense_receipt does not exist`.
- [ ] **3. Implement.** `db/migrations/NNNN_payments_ingest_support.sql`: `GRANT SELECT ON hub.ledger_accounts TO hub_ingest;` and a policy `ledger_accounts_ingest ... FOR SELECT TO hub_ingest USING (true)` (ids and codes, no balances). `hub.post_suspense_receipt(p_posting_key text, p_provider_event uuid, p_clearing_account uuid, p_source_top_up uuid) RETURNS uuid`, `SECURITY DEFINER SET search_path = pg_catalog, hub, pg_temp`, owner `ledger_owner`, `REVOKE ALL FROM PUBLIC`, `GRANT EXECUTE TO hub_ingest, hub_worker`: loads the event, requires `payment.succeeded` and a positive amount, resolves the `suspense` system account of the event currency, inserts the transaction (`topup_received`, `ON CONFLICT (posting_key) DO NOTHING`) and two entries (debit clearing, credit suspense), returns the id (the existing id on replay). Add the `RunSQL` migration. `LedgerService.post_suspense` calls it through `call_function`. `db/schema.sql` is not regenerated (frozen); the baseline comparison applies the baseline then numbered files.
- [ ] **4. Run** until green, then `python tools/check_traceability.py` and `uv run pytest tests/test_schema_in_postgres.py -q` from the repository root.
- [ ] **5. Commit:** `feat(db): ingest account lookup and suspense receipt function`

### Task 04.8: Late credit, mismatch routing and pull verification

- [ ] **1. Failing tests.** `unit/payments/test_routing.py`: `test_credit_when_everything_matches`, `test_late_is_credit_not_suspense`, one test per reason in priority order `erased_customer`, `meter_decommissioned`, `currency_mismatch`, `amount_mismatch`, `payer_mismatch`, `pull_disagrees`, `already_confirmed`, `top_up_failed_state`. `integration/payments/test_late_and_mismatch.py`: `test_late_confirmation_after_expiry_credits_once`, `test_late_confirmation_after_cancel_credits_once` (`late=true`, `confirmed`), `test_mismatches_route_to_suspense` (parametrised over the reasons: no wallet entry, posting `topup:<id>:suspense:<event_id>`, event `processed` with the reason, exception outbox row), `test_pull_verification_at_threshold` (10000 USD minor pulls once before the transaction; 9999 does not), `test_pull_disagreement_sets_pending_unknown_and_suspense`, `test_provider_unreachable_on_pull_is_503_and_nothing_stored`, `test_payer_mismatch_triggers_pull_and_stores_status_pull_at`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/unit/payments/test_routing.py tests/integration/payments/test_late_and_mismatch.py -q`. Expected: ImportError, then failures on suspense.
- [ ] **3. Implement.** `domain/routing.py`: frozen `Route(kind: Literal["credit","suspense"], reason: str | None)`; `classify_confirmation(top_up, event, payer_ok, eligibility, pull)` evaluates the reasons in the order above; `late` never changes the route. `_confirm` obtains eligibility from `EligibilityChecker`, decides the pull before T1 (success events at or above `PULL_VERIFY_THRESHOLD_MINOR[currency]`, or a payer mismatch), calls `fetch_status` with `PULL_TIMEOUT_S` (disagreement is status not `successful` or a different amount or currency; an exception is `ExternalUnavailable`, 503, nothing stored). The suspense route updates the allowed top-up flags (`payer_matched=False`, `status_pull_at`, and `pending` to `pending_unknown` for `pull_disagrees`), calls `LedgerService.post_suspense`, and adds `ReconciliationExceptionRaised` with kind `amount_mismatch`, `currency_mismatch` or `statement_only` (all other reasons). Unit tests use `FakeLedger.post_suspense`; the real one exists from Task 04.7.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(payments): late credit, mismatch routing and pull verification`

### Task 04.9: Provider reversals

- [ ] **1. Failing tests.** `integration/payments/test_reversal.py`: `test_reversal_before_energy_issued_returns_full_amount` (top-up `reversed`, `shortfall_minor=0`), `test_reversal_after_energy_issued_posts_shortfall` (a simulated `energy_purchase` of 80 percent: the shortfall goes to `chargeback_receivable`, `payment.reversed.shortfall_minor` equals it, the `ledger.chargeback_opened` outbox row exists), `test_reversal_event_has_own_txn_id` (`<ref>-R`), `test_duplicate_reversal_is_noop`, `test_reversal_of_unconfirmed_top_up_is_exception_not_posting`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/payments/test_reversal.py -q`. Expected: reversal path not implemented.
- [ ] **3. Implement.** `ConfirmPayment._reverse`: require status `confirmed` (else mark processed with `last_error='nothing_to_reverse'`, add the exception event); `LedgerService.reverse(original_transaction_id=top_up.ledger_transaction_id, posting_key=f"topup:{id}:reverse", provider_event_id=event_row_id, approval_id=None)`; update the top-up to `reversed` with `reversal_transaction_id`; `PaymentReversed(reversed_minor=amount - shortfall, shortfall_minor=shortfall, ledger_transaction_id=reversal_tx, chargeback_transaction_id=reversal_tx if shortfall else None)`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(payments): provider reversals with chargeback receivable shortfall`

### Task 04.10: Sweepers, webhook-log operations and replay

- [ ] **1. Failing tests.** `integration/payments/test_sweepers.py`: `test_expire_top_ups_moves_initiated_and_pending_only` (`pending_unknown` never auto-expires), `test_retry_initiation_for_initiated_older_than_60s` (same reference, becomes `pending`), `test_pending_unknown_poll_pending_returns_to_pending`, `test_pending_unknown_poll_failed_sets_failed`, `test_pending_unknown_poll_success_waits_for_webhook_then_raises_exception_after_30_minutes` (no credit from the poll; one `statement_only` exception), `test_sweepers_skip_locked_rows`. `integration/payments/test_webhook_events_api.py`: `test_list_requires_finance_or_ops_role`, `test_log_never_returns_payload`, `test_replay_enqueues_job_and_returns_202`, `test_replay_job_is_noop_when_posting_exists`, `test_replay_credits_ignored_event_after_top_up_appears` (job under `hub_worker`).
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/payments/test_sweepers.py tests/integration/payments/test_webhook_events_api.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `sweepers.py` (`ExpireTopUps`, `RetryInitiation`, `SweepPendingUnknown`) claims rows with `select_for_update(skip_locked=True)` in a short transaction, provider calls outside it. `ReplayEvent.__call__(event_id: UUID) -> None` rebuilds `VerifiedEvent` from the stored payload and runs the same `_confirm` or `_reverse` path (the row is already verified), registered as job `payments.replay_event`; `replayWebhookEvent` calls `JobQueue.enqueue("payments.replay_event", {"event_id": ...}, dedupe_key=str(event_id))` and returns 202. `WebhookEventViewSet` reads `provider_events` through the narrowed `hub_api` column grant (no payload).
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(payments): sweepers, webhook log operations and replay job`

### Task 04.11: Conformance, end to end with the stub, latency smoke

- [ ] **1. Failing tests.** `contract/test_openapi_conformance.py::test_schemathesis_top_ups_and_webhooks`. `e2e/test_topup_with_stub.py`: `test_initiate_then_confirmed_via_stub` (TC-FR-02-1), `test_stub_duplicate_mode_credits_once`, `test_stub_late_mode_after_cancel`, `test_stub_wrong_amount_goes_to_suspense`, `test_stub_reverse_after_posts_reversal`, `test_ack_p95_under_300ms_smoke` (200 sequential signed deliveries, warm stack).
- [ ] **2. Run.** `make up && cd backend && uv run pytest tests/e2e/test_topup_with_stub.py -q`. Expected: failures where wiring is missing.
- [ ] **3. Implement.** Wire `payments` in `bootstrap` (`wiring.py`), mount `urls/api.py` and `urls/webhooks.py`, register the jobs, give compose the provider secrets from a mounted secrets file (never in the repository), and select the `stub` provider adapter in `make up`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(payments): wiring, conformance and end-to-end tests with the provider stub`

## Verification

`make verify SLICE=04` runs, from the repository root, in this order. Each must exit 0.

1. `cd backend && uv run ruff check . && uv run mypy --strict src/hub/payments src/hub/ledger && uv run lint-imports` (zero errors).
2. `cd backend && uv run pytest tests/unit/payments -q && uv run pytest tests/integration/payments -q -m "not concurrency"` (all passed).
3. `cd backend && for i in $(seq 5); do uv run pytest tests/integration/payments/test_webhook_concurrency.py -q -m concurrency || exit 1; done` (five consecutive green runs; nightly CI runs 20).
4. `cd backend && uv run pytest tests/contract/test_openapi_conformance.py -q -k "top_ups or webhooks or webhook_events"` (schemathesis passes; no 5xx).
5. `python -m pytest tests/test_alignment.py tests/test_schema_in_postgres.py -q && python tools/check_traceability.py` (green from the repository root).
6. `make up && cd backend && uv run pytest tests/e2e/test_topup_with_stub.py -q` (all passed, including the four-worker run).
7. `python tools/check_django_config.py` (no admin, `ATOMIC_REQUESTS` false, models unmanaged).

## Demo script

1. `make up`; wait for health through the balancer.
2. As a seeded customer, `POST /api/v1/top-ups` with an `Idempotency-Key`; observe 202, `pending`, `energy_wh` set. Repeat with the same key: same id, one stub collection. Different amount: 422 `idempotency_key_reuse`.
3. Stub control: scenario `duplicate{count:20}` then complete the collection; observe `confirmed`, one ledger transaction, 20 stub deliveries of which 19 were answered `duplicate`.
4. Send a delivery signed with the wrong secret: 401; `GET /api/v1/webhook-events` shows no row for it; the quarantine counter rose.
5. Cancel a second top-up, then stub scenario `late`: it confirms with `late=true`. Scenario `wrong_payer`: no credit, a suspense posting and an exception event. `reverse_after`: status `reversed`, wallet back to zero.
6. Kill `be_webhooks` mid-burst: after restart, redeliveries leave exactly one posting.

## Done criteria

Requirements: FR-02 and FR-03 fully (AC-FR-02-1 to 02-8, AC-FR-03-1 to 03-7).

Test cases implemented and passing: TC-FR-02-1 (Task 04.11), TC-FR-02-2 (04.6), TC-FR-02-3 (04.2), TC-FR-02-4 (API half in 04.11, browser half in [fe-04-portal-topup.md](frontend/fe-04-portal-topup.md)), TC-FR-02-5 (04.2, with a tariff-change-after-initiation case), TC-FR-02-6 (04.8), TC-FR-03-1 (04.6), TC-FR-03-2 (04.5, 04.6), TC-FR-03-3 (04.5, 04.6), TC-FR-03-4 (04.8), TC-FR-03-5 (04.4), TC-FR-03-6 (04.6), TC-FR-03-7 (04.4), TC-FR-03-8 (04.8, 04.7), TC-FR-03-9 (04.9), TC-FR-03-10 (04.2 for the 7-day window; 24 hours elsewhere is the database trigger, covered by `db/invariant_tests.sql`).

## Plan notes for the tech lead

1. **No schema path to `suspense`.** D-05, D-09 and docs/03 section 6.1 route mismatches to `suspense`, but `hub.post_ledger_transaction` accepts `topup_received` only for a matching event and a full wallet credit; manual kinds need an approval. I followed D-05 (decisions outrank the frozen schema) with the migration `hub.post_suspense_receipt` (Task 04.7). If refused, fall back to an exception event without a posting.
2. **`hub_ingest` cannot resolve ledger account ids** (no SELECT, no policy under forced row-level security, no EXECUTE on `create_wallet_account`). The same migration adds a read grant and policy; wallets are created at initiation (`ensure_wallet`).
3. **Redis hint before the transaction.** docs/03 section 6.1 step 2 and docs/02 section 2.a set the hint before the database work, which can turn a retry into a false `duplicate`. I read first and write after commit; docs/03 should be amended.
4. **Provider transaction id.** `provider_txn_id` is NOT NULL and unique but the envelope has only `provider_reference`. I store it for confirmed and reversed events (slice T1 suffixes reversals `-R`) and `<provider_reference>:failed` for failures, so a later success is not swallowed. Envelope `payment.confirmed` is stored as `payment.succeeded` (D-01).
5. **`reconciliation.exception` has no item to point to yet.** The catalogue lists `item_id` and `batch_id`; slice 14 creates items. This slice emits the event with both null and two extra fields (`provider_event_id`, `reason_code`); docs/03 section 4 needs the change, and slice 14 must create the item from it.
6. **Pull failure.** docs/01 AC-FR-03-6 sends the money to suspense when the pull fails; docs/03 section 6.1 returns 503 so the provider retries. I followed docs/03; a disagreeing pull goes to suspense with `pending_unknown`. TC-FR-02-6 verifies a payer mismatch by pull while AC-FR-03-5 routes it to suspense; I do both.
7. **Failed top-up and a success event.** docs/03 says credit and set `late`; the schema trigger and ledger function forbid it. I route to suspense with reason `top_up_failed_state` (last in priority).
9. **Replay needs slice 05's `JobQueue`**, though 05 depends on 04. Tested here with a fake; slice 05 Task 05.6 adds the end-to-end test.
8. docs/02 section 2.a shows `idem_key` on `top_ups`; the schema uses `idempotency_keys`. Followed the schema and docs/03. Limits come from `plans.min_topup_minor` and `max_topup_minor`; the per-payer limit of 5 per hour is from the docs/05 tier table.
