# Slice 03: Ledger core

- **Goal:** the `ledger` module: accounts, the only write path (`hub.post_ledger_transaction`, `hub.reverse_ledger_transaction`), wallet balances, system accounts with daily snapshots, accounting periods locked through maker-checker, statement views, manual adjustment and refund through approvals, reversals with the `chargeback_receivable` shortfall rule, and the nightly drift job.
- **Depends on:** slice 00 (skeleton: `shared` app, `UnitOfWork`, `call_function`, `Clock`, `IdGenerator`, problem handler, job framework stubs), slice 01 (identity: `Principal`, `ApprovalService`, `ApprovalExecutorRegistry`, `AuditLog`, step-up), slice 02 (customers exist, so wallets can be created).
- **Requirements served:** FR-04, NFR-05 (also FR-02 for the refund endpoint, FR-14 for the period lock rule).
- **Size:** L, 8 days for a cheaper agent.
- **Companion files:** [fe-03-ledger-view.md](frontend/fe-03-ledger-view.md).
- **Spikes or open items:** [open] revenue recognition at token issuance (D-10): default, ledger accepts `energy_purchase` as posted by slice 06 and does nothing else. [open] overdraft on `suspense`: default, system accounts are never row-locked and may go either way; only wallets have the no-overdraft CHECK. [open] reopening a locked period: default, implemented as a second approval action `period.reopen`, no HTTP route (see Plan notes).

## Read first

- `docs/00-shared-context.md#3. Architecture decisions made up front` (invariants 3 and 4, money sign convention)
- `docs/00-shared-context.md#7.3 Money rules` (D-05, D-06, D-10)
- `docs/00-shared-context.md#7.5 Data, infrastructure and operations` (D-22, D-23) and `#7.1 Authority and naming` (D-03)
- `docs/00-shared-context.md#7.6 AI` (only D-40, accounting periods)
- `docs/adr/0002-postgresql-double-entry-ledger.md`, `docs/adr/0007-maker-checker-approvals.md`, `docs/adr/0018-per-deployable-database-roles.md`, `docs/adr/0020-sql-first-schema-unmanaged-django-models.md`
- `docs/03-code-architecture.md#2.5 ledger (FR-04, NFR-05)`, `#2.14 Approvals (maker-checker) (D-15, D-16)`, `#7.1 Error taxonomy`, `#7.4 Database access`, `#7.5 Background jobs and scheduling`, `#1.2 Layout of a module (a Django app)`
- `docs/04-data-model.md#5. Concurrency design` and `#10.7 Writes that never go through the ORM`
- `db/schema.sql`: sections 4 and 4b (tables `accounting_periods`, `ledger_accounts`, `ledger_transactions`, `ledger_entries`, `wallet_balances`, `ledger_balance_snapshots`, view `v_wallet_balance_drift`, functions `post_ledger_transaction`, `reverse_ledger_transaction`, `snapshot_ledger_balances`, `create_wallet_account`), `approvals` and `approvals_guard`, and the grants block
- `docs/01-prd.md#FR-04` (AC-FR-04-1 to AC-FR-04-5)
- `docs/10-test-strategy.md#2.1 Ledger invariants (FR-04, NFR-05)`
- `api/openapi.yaml` operations: `getCustomerWallet`, `listLedgerAccounts`, `getLedgerAccount`, `listLedgerTransactions`, `getLedgerTransaction`, `createLedgerAdjustment`, `createRefund`, `listAccountingPeriods`, `lockAccountingPeriod`, `approveApproval`

## Global constraints

- Sign convention: `amount_minor > 0` is a debit, `< 0` a credit; wallet balance = minus the sum of its entries; the API shows `direction` plus a positive amount (D-03).
- Money is integer minor units plus ISO-4217 `USD` or `CDF`; never float; every line is nonzero; amounts below 2^63.
- Every ledger write goes through `hub.post_ledger_transaction` or `hub.reverse_ledger_transaction`; no ORM write, no `INSERT INTO hub.ledger`, no `UPDATE hub.wallet_balances` outside `hub.ledger.infrastructure.writer`.
- Application roles `hub_api`, `hub_ingest`, `hub_worker` hold EXECUTE on the definer functions and no INSERT, UPDATE or DELETE on `ledger_*` or `wallet_balances`.
- Definer functions are owned by `ledger_owner` (no login) with `search_path = pg_catalog, hub, pg_temp`; immutability triggers are `ENABLE ALWAYS`.
- `posting_key` is unique; a replay returns the existing transaction; a transaction is reversed at most once (`uq_ledger_transactions_reverses`).
- Manual kinds (`adjustment`, `refund`, `reconciliation_fix`) need an approved approval whose `payload_hash` equals `hub.payload_hash(p_lines)`; the approval is consumed by the posting.
- Maker-checker threshold is zero; approver differs from maker by user and session; conflict check on `staff_customer_link`; step-up within 300 seconds (D-15, D-16).
- Period lock and reopen: maker-checker, approver holds attribute `finance_lead` (D-40); a posting dated in a locked period fails with problem code `period_locked`, HTTP 409; corrections after the lock are reversing postings dated in an open period.
- A provider reversal credits back what the wallet holds; the shortfall posts to `chargeback_receivable` and emits `ledger.chargeback_opened` (D-06).
- Idempotency window for `POST /ledger/adjustments`, `POST /refunds` and the lock: 24 hours (D-12).
- No overdraft on wallets (`ck_wallet_balances_no_overdraft`); system accounts are not row-locked; their balance is materialised daily in `ledger_balance_snapshots`.
- `ledger` is a leaf: it imports `shared` only (and `identity.contracts` in its `api` layer for `Principal`); it never knows what a top-up, token or meter is.
- Time through `Clock`, ids through `IdGenerator`; no `datetime.now()` in domain or application code.

## Review focus

1. **Concurrent debits on one wallet** (twenty or a hundred threads, one overdraft attempt): final balance correct, overdraft rejected, no deadlock. Pinned by `test_100_concurrent_debits_keep_balance` (`TC-FR-04-4`), owned by Task 03.3.
2. **Approval replay and payload swap**: the same approval used for a second posting, or approved lines differing by one cent from the posted lines, must fail with `approval_required`. Pinned by `test_approval_cannot_be_reused_or_altered`, owned by Task 03.5.
3. **Posting dated on the last or first day of a locked period** (UTC date boundary, `23:59:59+00` versus a `+02` offset that falls on the next day): the lock must compare the UTC date. Pinned by `test_period_lock_boundaries_use_utc_date`, owned by Task 03.6.
4. **Refund or reversal after the energy was issued** (wallet holds less than the credit): the shortfall goes to `chargeback_receivable`, the reversal is posted once, repeating the call is a no-op. Pinned by `test_reverse_with_shortfall_posts_chargeback`, owned by Task 03.4.
5. **Locking a period while exceptions are open**, or an approver who is also linked to the customer: 409 with the open count, 403 `conflict_of_interest`. Pinned by `test_lock_refused_with_open_exceptions_and_conflict`, owned by Task 03.6.

## Files

Paths are under `backend/src/hub/` unless stated; tests under `backend/tests/`.

- `ledger/apps.py`, `ledger/models.py` (one-line shim): Django app config, label `ledger`.
- `ledger/contracts.py`: public Protocols and DTOs (section Interfaces).
- `ledger/events.py`: `LedgerPosted`, `WalletLowBalance`, `ChargebackOpened` payload dataclasses.
- `ledger/domain/errors.py`: `UnbalancedPosting`, `PeriodLocked`, `ApprovalRequired`, `EvidenceRequired`, `InsufficientWalletBalance`, `CurrencyMismatch` (re-exported).
- `ledger/domain/posting.py`: pure validation and conversion of `PostingRequest` to signed lines.
- `ledger/infrastructure/models.py`: unmanaged read-only models `LedgerAccount`, `LedgerTransaction`, `LedgerEntry`, `WalletBalance`, `LedgerBalanceSnapshot`, `AccountingPeriod`.
- `ledger/infrastructure/writer.py`: the only caller of `post_ledger_transaction`, `reverse_ledger_transaction`, `create_wallet_account`, `snapshot_ledger_balances` through `shared.db.call_function`; maps SQLSTATE and message prefixes to domain errors.
- `ledger/infrastructure/reader.py`: `LedgerReader` implementation (balances, statements, transaction lines).
- `ledger/infrastructure/queries.py`: keyset-paginated account and transaction lists, drift query, system balance query.
- `ledger/application/ledger_service.py`: `LedgerService` implementation.
- `ledger/application/adjustments.py`: `ProposeAdjustment`, `ProposeRefund` use cases.
- `ledger/application/periods.py`: `ProposePeriodLock`, `ProposePeriodReopen`, `ListPeriods` use cases.
- `ledger/application/executors.py`: `AdjustmentExecutor`, `RefundExecutor`, `PeriodLockExecutor`, `PeriodReopenExecutor` (`ApprovalExecutor` implementations).
- `ledger/application/jobs.py`: `snapshot_balances_job`, `wallet_drift_job`, `imbalance_check_job`.
- `ledger/application/wiring.py`: registers executors, cron jobs, the service in the container.
- `ledger/api/serializers.py`, `views.py`, `urls.py`: DRF serializers `MoneySerializer`, `LedgerAccountSerializer`, `LedgerTransactionSerializer`, `WalletSerializer`, `AdjustmentCreateSerializer`, `RefundCreateSerializer`, `AccountingPeriodSerializer`, `PeriodLockSerializer`; viewsets mounted in `urls/api.py`.
- `urls/api.py` (modify): include `hub.ledger.api.urls`.
- `shared/migrations/00NN_ledger_system_accounts.py` and `db/migrations/NNNN_ledger_system_accounts.sql`: creates the system accounts (`provider_clearing.<provider>.<ccy>`, `revenue_energy.<ccy>`, `fees.<ccy>`, `suspense.<ccy>`, `chargeback_receivable.<ccy>`) for USD and CDF, run as the migrator; NNNN is the next free number.
- `db/migrations/NNNN_approvals_period_actions.sql` (conditional, Task 03.6): adds `period.lock` and `period.reopen` to `ck_approvals_action`.
- `db/seed/ledger_dev.sql`: dev opening data (one wallet per demo customer).
- `bootstrap.py` (modify): build the ledger container entries.
- `tests/unit/ledger/test_posting_domain.py`, `test_posting_properties.py`: domain and hypothesis tests.
- `tests/integration/ledger/test_db_defences.py`, `test_post_function.py`, `test_concurrency.py`, `test_reversal.py`, `test_adjustment_flow.py`, `test_periods.py`, `test_statements_api.py`, `test_snapshots_drift.py`, `test_state_machine.py`.
- `tests/architecture/test_ledger_leaf.py`: import rule and grant checks.
- `tests/integration/ledger/conftest.py`: fixtures `ledger_db`, `wallet`, `finance_lead_maker`, `finance_lead_checker`.

## Interfaces

**Consumes** (earlier slices):

```python
# shared (slice 00)
def call_function(name: str, *args) -> Any: ...      # shared.db; maps SQLSTATE to DomainError
class UnitOfWork(Protocol): outbox: OutboxWriter; def savepoint(self) -> ContextManager[None]: ...
class Clock(Protocol): def now(self) -> datetime: ...
class IdGenerator(Protocol): def new_id(self) -> UUID: ...
class ApprovalExecutor(Protocol): action: str; required_permission: str; def execute(self, payload, approval, *, uow) -> None: ...
class JobRegistry(Protocol): def register_cron(self, *, name: str, cron: str, handler, singleton: bool = True) -> None: ...
# identity (slice 01)
class ApprovalService(Protocol): def propose(self, *, action, payload, subject, subject_customer_id, maker, uow) -> ApprovalView: ...
class Authorizer(Protocol): def require(self, principal, permission: str) -> None: ...
class AuditLog(Protocol): def record(self, *, actor, action, target_type, target_id, detail, uow) -> None: ...
```

**Produces** (later slices rely on these; all in `hub/ledger/contracts.py`):

```python
@dataclass(frozen=True, slots=True)
class AccountRef:
    kind: Literal["customer_wallet","provider_clearing","revenue_energy","fees","suspense","chargeback_receivable"]
    currency: Literal["USD","CDF"]
    customer_id: UUID | None = None
    provider: str | None = None

@dataclass(frozen=True, slots=True)
class PostingLine: account: AccountRef; direction: Literal["debit","credit"]; amount: Money
@dataclass(frozen=True, slots=True)
class PostingRequest:
    posting_key: str; kind: Literal["topup_received","energy_purchase","fee","refund","adjustment","reconciliation_fix"]
    lines: list[PostingLine]; effective_at: datetime; description: str | None
    source_top_up_id: UUID | None; approval_id: UUID | None
@dataclass(frozen=True, slots=True)
class PostingResult: transaction_id: UUID; replayed: bool
@dataclass(frozen=True, slots=True)
class ReversalResult: reversal_transaction_id: UUID; shortfall: Money; replayed: bool

class LedgerService(Protocol):
    def post(self, req: PostingRequest, *, uow: UnitOfWork) -> PostingResult: ...
    def reverse(self, original_transaction_id: UUID, *, posting_key: str, provider_event_id: UUID | None,
                approval_id: UUID | None, uow: UnitOfWork) -> ReversalResult: ...     # wraps reverse_ledger_transaction; shortfall to chargeback_receivable
    def ensure_wallet(self, customer_id: UUID, currency: str, *, uow: UnitOfWork) -> UUID: ...
    def balance(self, account: AccountRef) -> Money: ...
    def statement(self, account: AccountRef, page: PageRequest) -> Page[EntryView]: ...
    def transaction(self, transaction_id: UUID) -> TransactionView: ...
    def period_is_locked(self, on: date) -> bool: ...
```

Events (outbox event types): `ledger.posted` (`transaction_id`, `kind`, `posting_key`), `wallet.low_balance` (`customer_id`, `balance_minor`, `currency`), `ledger.chargeback_opened` (written by the SQL function; `original_transaction_id`, `shortfall_minor`, `currency`). Approval actions executed here: `ledger.adjustment`, `ledger.refund`, `period.lock`, `period.reopen`. Cron jobs: `ledger.snapshot_balances` (daily 00:30 UTC), `ledger.wallet_drift` (daily 01:00 UTC), `ledger.imbalance_check` (daily 01:30 UTC). Serializer names above. Slice 14 uses `LedgerService.post` with kind `reconciliation_fix` and `period.lock` open-exception counts through `ReconciliationCounts` (slice 14 produces; this slice consumes it only through a Protocol defined here: `class OpenExceptionCounter(Protocol): def open_count(self, period_start: date, period_end: date) -> int`, default implementation returns 0 until slice 14 registers its own).

## Tasks

### Task 03.1: Domain posting rules and the unmanaged models

- [ ] **1. Failing tests.** `backend/tests/unit/ledger/test_posting_domain.py`: `test_to_signed_lines_debit_positive_credit_negative`, `test_unbalanced_raises`, `test_mixed_currency_raises`, `test_zero_and_float_rejected`, `test_overflow_rejected`, `test_single_line_rejected`. Assertions, short: `to_signed_lines(req)` returns `[(acct, +500), (acct2, -500)]`; `pytest.raises(UnbalancedPosting)` when one amount is perturbed by 1; `pytest.raises(CurrencyMismatch)` for USD and CDF in one request without balancing per currency; `Money(amount_minor=1.5, ...)` raises `TypeError`; `2**63` raises `ValueError`. Mark `@pytest.mark.tc("TC-FR-04-1")` on the first two. Also `backend/tests/architecture/test_ledger_models.py::test_ledger_models_unmanaged_and_read_only` asserts every ledger model has `Meta.managed is False` and the repository exposes no `save`.
- [ ] **2. Run:** `cd backend && pytest tests/unit/ledger/test_posting_domain.py -q`. Expect failure `ModuleNotFoundError: hub.ledger.domain.posting`.
- [ ] **3. Implement.** `domain/posting.py`: `def to_signed_lines(req: PostingRequest, resolve: Callable[[AccountRef], UUID]) -> list[SignedLine]`, where `SignedLine` is `(account_id: UUID, currency: str, amount_minor: int)`. Validate at least two lines, positive amounts, per-currency sum of debits equals credits, no float or `bool`, bound `abs(amount) < 2**63`. `infrastructure/models.py`: the six unmanaged models mirroring `db/schema.sql` columns exactly, with `db_table` unqualified. Add `apps.py`, `models.py` shim, `contracts.py` dataclasses, `domain/errors.py`.
- [ ] **4. Run** the two test files until they pass, plus `make lint`.
- [ ] **5. Commit:** `feat(ledger): posting domain rules and unmanaged ledger models`.

### Task 03.2: Writer, grants and database defences

- [ ] **1. Failing tests.** `tests/integration/ledger/test_db_defences.py`, connecting as `hub_api`: `test_app_role_cannot_write_ledger_tables` (INSERT, UPDATE, DELETE on `ledger_entries`, `ledger_transactions`, `ledger_accounts`; UPDATE on `wallet_balances`; each raises `InsufficientPrivilege`), `test_unbalanced_commit_rejected` (call the function path with lines summing to 1, expect `CheckViolation` at commit), `test_entry_into_committed_transaction_rejected`, `test_definer_search_path_pinned` (`pg_proc.proconfig` contains `search_path=pg_catalog, hub, pg_temp` for the eight ledger functions and `proowner` is `ledger_owner`), `test_triggers_enable_always` (`pg_trigger.tgenabled = 'A'` for the nine ledger triggers), `test_update_delete_truncate_refused_as_owner_session`. Mark `TC-FR-04-3` on the first three, `TC-FR-04-6` on the next two. `test_post_function.py::test_replay_returns_same_transaction` (same `posting_key` twice gives one row, `replayed=True`).
- [ ] **2. Run:** `pytest tests/integration/ledger/test_db_defences.py tests/integration/ledger/test_post_function.py -q`. Expect `ImportError: hub.ledger.infrastructure.writer`.
- [ ] **3. Implement.** `infrastructure/writer.py` with `class LedgerWriter`: `post(request, signed_lines, uow) -> PostingResult` calls `call_function("post_ledger_transaction", posting_key, kind, effective_at, description, approval_id, source_top_up_id, json.dumps(lines))` (lines as `[{"account_id","currency","amount_minor"}]`); `reverse(...)` calls `reverse_ledger_transaction` with `(original, posting_key, source_event, approval_id)` and reads `(reversal_tx, shortfall_minor)`; `ensure_wallet` calls `create_wallet_account`; `snapshot(date)`. Error mapping by message prefix: `period_locked` to `PeriodLocked` (409), `approval_required` to `ApprovalRequired`, `evidence_required` to `EvidenceRequired`, `23505` on `uq_ledger_transactions_posting_key` to an idempotent success, `23514` check violations to `UnbalancedPosting` or `InsufficientWalletBalance` by constraint name `ck_wallet_balances_no_overdraft`, anything else `InvariantViolation`. The test database is built from `db/schema.sql`, so grants already exist; the task proves them and adds nothing to SQL.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(ledger): definer-function writer with error mapping and database defence tests`.

### Task 03.3: LedgerService, system accounts, wallet rules and concurrency

- [ ] **1. Failing tests.** `tests/integration/ledger/test_concurrency.py::test_100_concurrent_debits_keep_balance` (wallet funded 10,000; 100 threads each post `energy_purchase` debit 100 against its own top-up fixture; final balance 0; a 101st debit raises `InsufficientWalletBalance`; no `DeadlockDetected`), `test_20_concurrent_same_posting_key_one_winner` (one `replayed=False`, nineteen `True`, one wallet credit), `test_two_wallet_postings_do_not_deadlock`. `test_post_function.py::test_system_accounts_exist_per_currency` (five kinds times USD and CDF), `test_system_accounts_not_row_locked` (a posting touching only system accounts does not create a `wallet_balances` row). Tags: `TC-FR-04-4` on the first, `TC-NFR-05-2` on the replay test.
- [ ] **2. Run:** `pytest tests/integration/ledger/test_concurrency.py -q`. Expect `NotImplementedError` from `LedgerServiceImpl.post`.
- [ ] **3. Implement.** `application/ledger_service.py` `LedgerServiceImpl(writer, reader, accounts, clock)`: `post` resolves each `AccountRef` to an account id (wallet by `(customer_id, currency)`; system accounts by code through a cached lookup), calls `to_signed_lines`, then `writer.post`; stages `ledger.posted` on `uow.outbox`; after a posting that touches a wallet, if the new balance is below the configured `LEDGER_LOW_BALANCE_MINOR` (setting, default 100 USD cents equivalent per currency, [open] threshold by finance), stage `wallet.low_balance`. `ensure_wallet` is idempotent. Seed migration `db/migrations/NNNN_ledger_system_accounts.sql` inserts the ten system accounts with ids from `hub.uuid_v7()` and `ON CONFLICT (code) DO NOTHING`, run as `migrator`, with its Django `RunSQL` wrapper. Write `db/seed/ledger_dev.sql`.
- [ ] **4. Run** until green; run the file three times to confirm no flake.
- [ ] **5. Commit:** `feat(ledger): ledger service, system accounts and wallet concurrency tests`.

### Task 03.4: Reversals and chargeback_receivable

- [ ] **1. Failing tests.** `tests/integration/ledger/test_reversal.py`: `test_reverse_full_when_wallet_holds_credit` (credit 500 posted, reversal restores zero on every account, both rows remain), `test_reverse_with_shortfall_posts_chargeback` (wallet debited 300 first; reversal: wallet gets 200 back, `chargeback_receivable` debit 300, outbox row `ledger.chargeback_opened` with `shortfall_minor == 300`), `test_reverse_is_at_most_once` (second call returns `replayed=True`, one reversal row), `test_reverse_requires_evidence_or_approval` (both null raises `EvidenceRequired`), `test_only_received_topups_reversible`. Tags `TC-FR-04-1`, `TC-NFR-05-2`.
- [ ] **2. Run:** `pytest tests/integration/ledger/test_reversal.py -q`; expect `AttributeError: reverse`.
- [ ] **3. Implement.** `LedgerServiceImpl.reverse` wraps `writer.reverse`, converts `shortfall_minor` to `Money`, emits nothing itself for the chargeback (the SQL function writes the outbox row), stages `ledger.posted` for the reversal. Never build reversal lines in Python.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(ledger): reversals with chargeback receivable shortfall`.

### Task 03.5: Adjustment and refund through approvals

- [ ] **1. Failing tests.** `tests/integration/ledger/test_adjustment_flow.py`: `test_adjustment_proposed_then_posted_after_second_approver` (POST `/api/v1/ledger/adjustments` as maker with step-up returns 202 and an `Approval` with `status == "pending"`; no ledger row yet; a different `finance_lead` approves via `/approvals/{id}/approve` with the payload hash; one balanced `adjustment` transaction exists with `approval_id` set), `test_self_approval_forbidden` (403 `self_approval_forbidden`, by user and by second session of the maker), `test_approval_cannot_be_reused_or_altered` (call `writer.post` with the consumed approval: `ApprovalRequired`; with lines changed by one minor unit: `ApprovalRequired`), `test_conflict_of_interest_blocks_approver`, `test_adjustment_without_step_up_is_401`, `test_adjustment_overdraft_debit_rejected` (debit larger than wallet: 409 on execution and the approval stays pending), `test_refund_executes_reverse_ledger_transaction` (approval payload carries `original_transaction_id`; reversal posted; shortfall rule applies). Tags `TC-FR-04-1` on the flow test, `TC-FR-04-4` on the overdraft case; the other approval tests are untagged security tests (`TC-FR-14-4` belongs to slice 14).
- [ ] **2. Run:** `pytest tests/integration/ledger/test_adjustment_flow.py -q`; expect 404 on the route.
- [ ] **3. Implement.** `application/adjustments.py`: `ProposeAdjustment.__call__(cmd: AdjustmentCreate, maker: Principal, uow) -> ApprovalView` validates the customer has a wallet in the currency, builds the exact signed lines server-side (`credit_wallet` debits `suspense.<ccy>` and credits the wallet; `debit_wallet` debits the wallet and credits `suspense.<ccy>`; [open] finance may choose another counter-account, default `suspense`). Because `post_ledger_transaction` compares the approval's `payload_hash` with `hub.payload_hash(p_lines)`, the approval payload for `ledger.adjustment` is exactly the JSON array of lines `[{"account_id","currency","amount_minor"}]` and nothing else; the customer goes in `subject_customer_id`, the reason and ticket id go into the `AuditLog` record written at proposal, and the posting description is `"adjustment " || approval id`. The executor passes the stored array unchanged. `ProposeRefund` validates the top-up through a `TopUpLookup` Protocol defined in `ledger/contracts.py` and implemented by `payments` in slice 04 (until then a fake); its payload holds `original_transaction_id`. `application/executors.py`: `AdjustmentExecutor.execute` calls `LedgerService.post(kind="adjustment", approval_id=approval.id, ...)`; `RefundExecutor.execute` calls `LedgerService.reverse(..., approval_id=approval.id)`; both audit through `AuditLog`. `api/views.py`: `LedgerAdjustmentView` (`@operation("createLedgerAdjustment")`, `@idempotent(window=24h)`), `RefundView` (`createRefund`); register executors in `wiring.py`. The refund payload is an object with `original_transaction_id` (the function reads that key), so no line-hash applies to it.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(ledger): manual adjustments and refunds through maker-checker approvals`.

### Task 03.6: Accounting periods and the lock

- [ ] **1. Failing tests.** `tests/integration/ledger/test_periods.py`: `test_period_lock_boundaries_use_utc_date` (period 2026-09-01..2026-09-30 locked; posting `2026-09-30T23:59:59+00:00` fails `PeriodLocked`; posting `2026-10-01T00:30:00+02:00` (= 2026-09-30 UTC) fails; posting `2026-10-01T00:00:00+00:00` succeeds), `test_locked_period_returns_409_period_locked` over HTTP with problem `code == "period_locked"`, `test_correction_is_reversal_in_open_period` (reversal of a September posting, dated October, succeeds), `test_lock_needs_second_finance_lead` (maker proposes via `POST /accounting-periods/{id}/lock`; period still `open`; approver without `finance_lead` gets 403; with it the period becomes `locked`, `locked_by` is the approver, audit row `period.lock`), `test_lock_refused_with_open_exceptions_and_conflict` (counter fake returns 3: 409 with `open_exceptions == 3`), `test_reopen_needs_approval`, `test_list_periods_filter_status`. Tags `TC-FR-04-5` on the first two.
- [ ] **2. Run:** `pytest tests/integration/ledger/test_periods.py -q`; expect 404.
- [ ] **3. Implement.** First check `grep -c "period.lock" db/schema.sql`. If 0, add `db/migrations/NNNN_approvals_period_actions.sql` (drop and re-add `ck_approvals_action` with `period.lock`, `period.reopen` appended) and its `RunSQL` wrapper; if the tech lead already folded it into the schema, skip. `application/periods.py`: `ProposePeriodLock(period_id, note, maker)` checks the maker holds `finance_lead`, refuses with 409 `open_exceptions_remain` (details `open_exceptions`) when `OpenExceptionCounter.open_count` is above 0, then proposes `period.lock`. `PeriodLockExecutor.execute` runs inside the approval transaction: `UPDATE hub.accounting_periods SET status='locked', locked_by=:approver, locked_at=:now` through a narrow repository (the `hub_api` role already holds UPDATE on `accounting_periods`), after re-checking the open count. `PeriodReopenExecutor` sets `status='open'` with `locked_by` and `locked_at` null. Approval requires `finance_lead` on the approver (extend the `ApprovalExecutor.required_permission` check by an `required_attribute = "finance_lead"` class attribute read by `ApprovalService.approve`; if slice 01 lacks it, the executor itself raises `PermissionDenied` first thing and a note goes to progress.md). The route `lockAccountingPeriod` returns 200 with the unchanged `AccountingPeriod` plus header `Location` of the approval (see Plan notes). Create missing monthly period rows lazily with `ensure_period(date)`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(ledger): accounting periods locked through maker-checker`.

### Task 03.7: Read API, statements and views

- [ ] **1. Failing tests.** `tests/integration/ledger/test_statements_api.py`: `test_wallet_endpoint_matches_fold` (balance equals minus the sum of entries; `pending_top_ups` present), `test_customer_cannot_read_other_wallet` (404), `test_list_accounts_filter_type_and_cursor`, `test_transaction_shows_direction_and_positive_amounts`, `test_statement_keyset_pages_are_stable_during_inserts`, `test_agent_has_no_ledger_read` (support_agent 403 on `listLedgerAccounts`), `test_no_ledger_write_route_exists` (the OpenAPI paths under `/ledger` contain only `get` plus `/ledger/adjustments` `post`). Tag `TC-FR-04-1` for the fold test. `tests/integration/ledger/test_state_machine.py::test_stateful_ledger_machine`: hypothesis `RuleBasedStateMachine` with rules `top_up_confirmed`, `issue_token_debit`, `refund`, `reverse`, `fee_posting`, `reconcile_adjustment`, `replay_same_event`, `concurrent_pair`, invariants: global sum per currency zero, each wallet equals its fold, row hashes unchanged, at most one posting per `topup:<id>:confirm`; 200 examples (settings profile `ci`). Tags `TC-FR-04-2`. `tests/unit/ledger/test_posting_properties.py` properties P1 to P7 of docs/10 section 2.1, tagged `TC-FR-04-1`, `TC-NFR-05-1`.
- [ ] **2. Run:** `pytest tests/integration/ledger/test_statements_api.py tests/unit/ledger/test_posting_properties.py -q`; expect 404 and import errors.
- [ ] **3. Implement.** `infrastructure/reader.py` and `queries.py`: `wallet_for_customer`, `list_accounts(types, cursor, limit)` (keyset on `id`), `statement(account_id, after_id, limit)` using `ix_ledger_entries_account`, `transaction_with_lines`. System account balance reads the latest snapshot plus entries after `snapshot_date` (never a full scan). Serializers convert signed amounts into `direction` plus positive `Money`. Views with `@operation` names from the contract; `getCustomerWallet` is customer-scoped through the principal, never a client id for a customer.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(ledger): wallet, account, transaction and statement read API`.

### Task 03.8: Snapshots and the nightly drift job

- [ ] **1. Failing tests.** `tests/integration/ledger/test_snapshots_drift.py`: `test_snapshot_is_incremental_and_idempotent` (run for day D, post, run for D+1: balance equals prior plus delta; rerun D inserts zero rows), `test_system_balance_equals_entry_sum`, `test_drift_job_detects_tampered_wallet` (as `migrator` in a scratch database set `wallet_balances.balance_minor` off by 1; `wallet_drift_job` returns one row and emits a metric `ledger_wallet_drift_total == 1` and an alert event `ledger.drift_detected`), `test_drift_job_clean_returns_zero`, `test_imbalance_check_zero_rows` (query `HAVING sum(amount_minor) <> 0` per transaction and currency returns none; also checks at most one `topup_received` per `source_top_up_id`). Tags `TC-FR-04-3`, `TC-NFR-05-2`.
- [ ] **2. Run:** `pytest tests/integration/ledger/test_snapshots_drift.py -q`; expect `ImportError jobs`.
- [ ] **3. Implement.** `application/jobs.py`: `snapshot_balances_job(ctx)` calls `writer.snapshot(yesterday_utc)`; `wallet_drift_job(ctx)` selects from `hub.v_wallet_balance_drift` (read-only query object), logs account ids (not customer data), increments the metric, stages `ledger.drift_detected` when rows exist; `imbalance_check_job` runs the two SQL checks. Register the three crons in `wiring.py` as singletons. All idempotent.
- [ ] **4. Run** the whole `tests/integration/ledger` directory, then `make verify SLICE=03`.
- [ ] **5. Commit:** `feat(ledger): daily balance snapshots and nightly drift and imbalance jobs`.

## Verification

`make verify SLICE=03` runs, from the repository root:

1. `cd backend && pytest tests/unit/ledger tests/architecture/test_ledger_models.py tests/architecture/test_ledger_leaf.py -q`: all pass.
2. `cd backend && pytest tests/integration/ledger -q`: all pass, including concurrency (run twice).
3. `make lint`: clean; import-linter confirms `ledger` imports only `shared` (and `identity.contracts` in `api`), and the lint rule finds no ledger SQL outside `ledger/infrastructure/writer.py`.
4. `make contracts`: `tests/test_alignment.py`, `tests/test_schema_in_postgres.py`, `tools/check_traceability.py` pass; `tests/test_django_alignment.py` shows zero model differences.
5. `psql -f db/invariant_tests.sql` against a fresh database: zero unexpected results.
6. `pytest -m "tc and (FR_04 or NFR_05)" backend/tests -q`: every `TC-FR-04-1` to `TC-FR-04-6` and `TC-NFR-05-1`, `TC-NFR-05-2` marker is collected and passes.

## Demo script

1. `make up`, `manage.py load_fixtures`, log in as finance lead A and B (two sessions).
2. As A, step up, `POST /api/v1/ledger/adjustments` for a demo customer (credit 5.00 USD, reason "goodwill for outage"). Observe 202, pending approval, wallet unchanged.
3. As A, approve the same approval: observe 403 `self_approval_forbidden`.
4. As B, step up and approve with the payload hash: observe one `adjustment` transaction, wallet plus 500, balanced lines in `GET /ledger/transactions/{id}`.
5. Try `UPDATE hub.ledger_entries ...` as `hub_api` in psql: observe permission denied.
6. Lock the previous month as A, approve as B; post a `fee` dated inside it through the test shell: observe 409 `period_locked`.
7. Run `manage.py run_scheduler --once ledger.wallet_drift`: observe zero drift; tamper a wallet row in a scratch database and see one.

## Done criteria

Covers FR-04 and NFR-05. Implements TC-FR-04-1, TC-FR-04-2, TC-FR-04-3, TC-FR-04-4, TC-FR-04-5, TC-FR-04-6, TC-NFR-05-1 and TC-NFR-05-2. `progress.md` holds the output of `make verify SLICE=03`, the demo result, assumptions, and any change request.

## Plan notes for the tech lead

- `docs/03-code-architecture.md#2.5` `PostingRequest` has `created_by_user_id` and `reference`; `db/schema.sql` `post_ledger_transaction` takes `(posting_key, kind, effective_at, description, approval_id, source_top_up_id, lines jsonb)` and derives the user from the approval. Followed the schema (D-01); the Python DTO here has `approval_id` and `source_top_up_id` instead.
- `docs/03` section 2.5 `reverse(posting_key, reason)` differs from schema `reverse_ledger_transaction(original, posting_key, source_event, approval_id)`; followed the schema.
- Approval action names: `docs/03#2.14` uses `ledger.adjustment`, `ledger.refund`, `reconciliation.resolve_money`; the schema CHECK uses `ledger.adjustment`, `ledger.refund`, `recon.resolve`; OpenAPI `Approval.kind` uses `manual_adjustment`, `refund`, `reconciliation_resolution`. Followed the schema for stored actions; the serializer maps to OpenAPI kinds.
- D-40 requires maker-checker for period lock and reopen, but `ck_approvals_action` has no `period.lock` or `period.reopen`, the OpenAPI `Approval.kind` enum has no period kind, `lockAccountingPeriod` has no `x-approval` and returns 200 with an `AccountingPeriod`, and no reopen route exists. Followed D-40 with a conditional additive migration and 200-plus-`Location` behaviour; the lock semantics need a change request to the frozen files.
- `approvals_guard` hashes the whole approval payload, while `post_ledger_transaction` compares that hash with `hub.payload_hash(p_lines)`. They agree only when the approval payload is exactly the lines array. Followed the schema: the adjustment payload is the bare lines array, the reason lives in the audit record. A richer payload (customer, reason) needs a schema change (for example hashing `payload->'lines'`).
- No application role can create system accounts (`ledger_owner` has INSERT but no function creates them); the plan adds a migrator-run seed migration.
- `docs/04#10.7` puts the wrappers in `hub.ledger.functions`; `docs/03#7.4` says `ledger.infrastructure.writer`. Followed docs/03.
- `ReconException` and invariant tests use provider `mtn_stub`, OpenAPI enumerates `mpesa_cd`, `airtel_cd`, `orange_cd`; system account codes here use the provider as data, not an enum.
