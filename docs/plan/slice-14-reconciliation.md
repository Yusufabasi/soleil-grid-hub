# Slice 14: Reconciliation

- **Goal:** import provider statements (uploaded or fetched daily), match them against the ledger, raise the five exception kinds, run the exception queue, and resolve money differences only through maker-checker with system-obtained provider evidence.
- **Depends on:** slice 00 (skeleton, jobs framework), slice 01 (identity: `ApprovalService`, `approval.rejected` event, conflict check, step-up), slice 03 (ledger: `LedgerService.post`, accounting periods, `OpenExceptionCounter`), slice 04 (payments: `PaymentService`, `ProviderGateway`, `payment.reversed`), slice 05 (worker, cron, consumers), slice 06 (meters: `TokenService.confirm_non_delivery`, `token.failed`).
- **Requirements served:** FR-14 (also FR-04 for the posting rules and the period lock).
- **Size:** L, 7 days for a cheaper agent.
- **Companion files:** [fe-14-reconciliation.md](frontend/fe-14-reconciliation.md).
- **Spikes or open items:** [open] provider settlement currency per provider: default `USD` for `mpesa_cd`, `airtel_cd`, `orange_cd`, held in setting `RECON_PROVIDER_CURRENCY`. [open] `refund_customer` pays money out: default, the resolution only books the amount to `suspense` and notes that finance pays the customer outside the hub. [open] fuzzy candidate window: default 10 minutes (PRD). Spike (half a day, Task 14.3): confirm 100,000 lines match in under 60 seconds with the chunked algorithm; the 1.2 million line figure of AC-FR-14-1 is a nightly test.

## Read first

- `docs/00-shared-context.md#7.2 Canonical state machines` (the `reconciliation_items.kind` row)
- `docs/00-shared-context.md#7.3 Money rules` (D-05, D-06), `#7.4 Authorization and authentication` (D-15, D-16), `#7.5 Data, infrastructure and operations` (D-30), `#7.6 AI` (D-40 only)
- `docs/adr/0007-maker-checker-approvals.md`, `docs/adr/0002-postgresql-double-entry-ledger.md`
- `docs/03-code-architecture.md#2.12 reconciliation (FR-14)`, `#2.14 Approvals (maker-checker) (D-15, D-16)`, `#4. Domain event catalogue`, `#5. Ports and adapters`, `#7.5 Background jobs and scheduling`
- `docs/04-data-model.md#10.8 Bulk and partitioned inserts` and `#5.3 Isolation levels`
- `db/schema.sql`: tables `reconciliation_batches`, `reconciliation_items` (and their CHECK constraints), `approvals`, `top_ups`; function `post_ledger_transaction`
- `docs/01-prd.md#FR-14` (AC-FR-14-1 to AC-FR-14-6)
- `docs/07-security-threat-model.md#T-39` and `#T-46`
- `docs/10-test-strategy.md#2.10 Controls added in revision 1` (rows "Maker-checker" and "Reconciliation evidence and conflicts")
- `api/openapi.yaml` operations: `uploadProviderStatement`, `listReconBatches`, `getReconBatch`, `listReconExceptions`, `getReconException`, `requestReconEvidence`, `resolveReconException`, `listAccountingPeriods`, `lockAccountingPeriod`; schemas `ReconBatch`, `ReconException`, `ReconResolve`, `ReconStatementUpload`
- `docs/plan/slice-03-ledger-core.md#Interfaces` (what the ledger produces)

## Global constraints

- Statement upload: CSV, at most 10 MB and 100,000 rows; over size is HTTP 413, over rows is 422; finance officer only (D-30, T-46).
- Cells beginning `=`, `+`, `-` or `@` are neutralised (prefixed with a single quote) in text columns on import and on every export.
- The same SHA-256 of the file bytes for the same provider gives 409 `statement_already_imported` (`uq_reconciliation_batches_statement`).
- Item kinds are exactly `statement_only`, `ledger_only`, `amount_mismatch`, `duplicate_in_statement`, `currency_mismatch`; statuses `open`, `pending_approval`, `resolved`, `written_off`; batch statuses `open`, `matching`, `needs_review`, `completed`, `failed`.
- A resolution note is mandatory (5 to 1,000 characters) as soon as an item leaves `open`.
- Every resolution that posts money, and every other resolution except `mark_explained` on a `duplicate_in_statement`, is an approval (action `recon.resolve`), threshold zero; approver differs from maker by user and session; hash bound to the exact payload; conflict check via `staff_customer_link`; step-up within 300 seconds.
- `match_to_top_up` needs `evidence_id` of evidence the system fetched (`status_pull` or `statement_fetch`); an upload never counts; missing evidence is 422 `evidence_required`.
- Reconciliation never edits `top_ups` or `ledger_*` directly and never auto-corrects money; fuzzy candidates (same amount and payer phone within 10 minutes) are suggestions only.
- Postings use kind `reconciliation_fix`, posting key `recon:<item_id>:resolve`, through `LedgerService.post`; a posting dated in a locked period fails with `period_locked` (409) and the approval stays pending.
- A period cannot be locked while any item of an overlapping batch is `open` or `pending_approval` (409 with the count).
- Reconciliation run reads a consistent snapshot (`REPEATABLE READ, READ ONLY`); exceptions are created in small separate transactions (docs/02 table).
- The administrator has no reconciliation operation (D-14).
- Never log MSISDNs or raw statement rows; the payer number is used in memory for fuzzy matching and is not stored.

## Review focus

1. **A statement with negative amounts** (provider reversals, `-500`) must parse as numbers while text cells such as `=HYPERLINK(...)` are neutralised; a naive neutraliser corrupts amounts. Pinned by `test_negative_amount_parses_formula_text_neutralised`, Task 14.1.
2. **Awkward CSV and cap boundaries**: BOM, CRLF, a quoted newline inside a cell, a semicolon-delimited file, exactly 10 MB versus one byte more, exactly 100,000 rows versus 100,001. Pinned by `test_csv_edges_and_caps`, Task 14.1.
3. **Same file again, altered file again, concurrent double upload**: identical bytes give 409; a one-byte altered copy of the same period must not recreate exceptions already pending or resolved; two simultaneous uploads create one batch. Pinned by `test_reimport_is_noop_and_overlap_skips_handled_lines`, Task 14.2.
4. **Evidence that belongs to something else**: evidence for another transaction, a failed provider status, a different amount, or a forged upload reference; also evidence valid at proposal but stale at approval. Pinned by `test_match_to_top_up_evidence_rules`, Task 14.5.
5. **Approval after the period was locked, a rejected approval, or an approver linked to the customer**: posting fails with `period_locked` and the item stays `pending_approval`; rejection returns the item to `open`; the link gives 403 `conflict_of_interest`. Pinned by `test_resolution_failure_and_rejection_paths`, Task 14.5.

## Files

Paths are under `backend/src/hub/` unless stated; tests under `backend/tests/`.

- `reconciliation/apps.py`, `models.py` (shim): app config, label `reconciliation`.
- `reconciliation/contracts.py`: public Protocols and DTOs; implements `OpenExceptionCounter` (slice 03).
- `reconciliation/events.py`: `ReconciliationCompleted`, `ReconciliationException` payloads.
- `reconciliation/domain/statement.py`: CSV parsing, caps, neutralisation, `StatementLine`.
- `reconciliation/domain/matching.py`: pure classification of a line against a payment (`classify`), duplicate tracking, fuzzy candidate rule.
- `reconciliation/domain/resolution.py`: resolution kinds, allowed kinds per item kind, posting-line builders (pure), state transitions.
- `reconciliation/domain/errors.py`: `StatementAlreadyImported`, `StatementTooLarge`, `StatementInvalid`, `EvidenceRequired`, `InvalidResolution`, `OpenExceptionsRemain`.
- `reconciliation/infrastructure/models.py`: unmanaged `ReconciliationBatch`, `ReconciliationItem`, `ReconciliationEvidence`.
- `reconciliation/infrastructure/repositories.py`: `BatchRepository`, `ItemRepository`, `EvidenceRepository`.
- `reconciliation/infrastructure/queries.py`: keyset lists for batches and exceptions, open-count query.
- `reconciliation/application/ports.py`: `StatementSource`, `PaymentLookup`, `TokenNonDelivery` Protocols (container adapters over payments and meters contracts).
- `reconciliation/application/import_statement.py`: `ImportStatement`.
- `reconciliation/application/run_matching.py`: `RunMatching`.
- `reconciliation/application/evidence.py`: `RequestEvidence`, `PullEvidenceJob`.
- `reconciliation/application/resolve.py`: `ProposeResolution`, `ReconResolveExecutor`, `OnApprovalRejected`.
- `reconciliation/application/fetch_daily.py`: `FetchDailyStatements` cron job.
- `reconciliation/application/system_items.py`: consumers of `payment.reversed` and `token.failed`.
- `reconciliation/application/wiring.py`: registers executor, jobs, consumers, counter.
- `reconciliation/api/serializers.py`, `views.py`, `urls.py`: `ReconStatementUploadSerializer`, `ReconBatchSerializer`, `ReconExceptionSerializer`, `ReconResolveSerializer`; multipart parser limited to 10 MB.
- `urls/api.py` (modify): include `hub.reconciliation.api.urls`.
- `db/migrations/NNNN_reconciliation_evidence.sql` and `shared/migrations/00NN_reconciliation_evidence.py`: expand-only (see Task 14.1); NNNN is the next free number.
- `tools/provider_stub/statement.py` (modify): serves a statement for a day in the CSV format below, scriptable with missing, extra, duplicate, mismatched lines.
- `tests/unit/reconciliation/test_statement_parser.py`, `test_matching_domain.py`, `test_resolution_domain.py`.
- `tests/integration/reconciliation/conftest.py`, `test_import.py`, `test_matching.py`, `test_exception_api.py`, `test_evidence.py`, `test_resolution_flow.py`, `test_daily_fetch.py`, `test_system_items.py`, `test_matching_scale.py`.
- `tests/e2e/test_reconciliation_flow.py`: API-level end-to-end.

CSV format (the "stub format", header required, comma delimiter, UTF-8, optional BOM): `provider_txn_id,amount_minor,currency,occurred_at,msisdn`.

## Interfaces

**Consumes:**

```python
# ledger (slice 03)
class LedgerService(Protocol): def post(self, req: PostingRequest, *, uow: UnitOfWork) -> PostingResult: ...
class OpenExceptionCounter(Protocol): def open_count(self, period_start: date, period_end: date) -> int: ...
# identity (slice 01)
class ApprovalService(Protocol): def propose(self, *, action, payload, subject, subject_customer_id, maker, uow) -> ApprovalView: ...
# event: approval.rejected (approval_id, action) ; ApprovalExecutor, ApprovalExecutorRegistry from shared.contracts
# payments (slices 04) through the container, adapters in reconciliation/application/ports.py
class PaymentLookup(Protocol):
    def confirmed_by_txn_ids(self, provider: str, txn_ids: Sequence[str]) -> list[ConfirmedPaymentRef]: ...
    def confirmed_between(self, start: datetime, end: datetime, provider: str, *, after: UUID | None, limit: int) -> list[ConfirmedPaymentRef]: ...
    def top_up(self, top_up_id: UUID) -> TopUpView: ...
    def pull_status(self, provider: str, provider_txn_id: str) -> ProviderEvidence: ...
    def payer_candidates(self, provider: str, amount: Money, around: datetime, window: timedelta) -> list[UUID]: ...
class ProviderGateway(Protocol): def fetch_statement(self, day: date) -> list[StatementLine]: ...
# meters (slice 06)
class TokenService(Protocol): def confirm_non_delivery(self, token_id: UUID, evidence: ReconciliationEvidence, *, uow: UnitOfWork) -> None: ...
# shared: Clock, IdGenerator, UnitOfWork, JobRegistry, ObjectStore (not used here)
```

`ConfirmedPaymentRef` carries `top_up_id`, `customer_id`, `provider_txn_id`, `amount: Money`, `ledger_transaction_id`, `confirmed_at`.

**Produces:**

```python
class ReconciliationService(Protocol):
    def import_statement(self, provider: str, period_start: date, period_end: date, source: StatementSource,
                         *, actor: Principal | None, uow: UnitOfWork) -> UUID: ...        # returns batch id; raises StatementAlreadyImported
    def run(self, batch_id: UUID) -> RunSummary: ...                                    # matched, per-kind counts, per currency
    def propose_resolution(self, item_id: UUID, resolution: Resolution, *, actor: Principal, uow: UnitOfWork) -> ApprovalView: ...
    def open_count(self, period_start: date, period_end: date) -> int: ...              # OpenExceptionCounter
```

Events: `reconciliation.completed` (`batch_id, provider, day, matched, items_open`), `reconciliation.exception` (`item_id, batch_id, kind, top_up_id | null, amount_minor, currency`). Jobs: `reconciliation.import_daily` (cron 04:00 UTC), `reconciliation.run` (queued per batch), `reconciliation.pull_evidence` (queued per request). Approval executor action `recon.resolve`. Serializer names above.

## Tasks

### Task 14.1: Statement parser, caps, neutralisation, migration and models

- [ ] **1. Failing tests.** `tests/unit/reconciliation/test_statement_parser.py`: `test_negative_amount_parses_formula_text_neutralised` (row `txn1,-500,USD,2026-10-01T10:00:00Z,+243900000001` gives `amount_minor == -500`; a `provider_txn_id` cell `=HYPERLINK("http://x")` is stored as `'=HYPERLINK("http://x")`), `test_csv_edges_and_caps` (parametrised: BOM, CRLF, quoted newline, semicolon delimiter raises `StatementInvalid`, 10 MB exactly parses, 10 MB plus 1 byte raises `StatementTooLarge`, 100,000 rows ok, 100,001 rows raises `StatementTooLarge(kind="rows")`, missing header, non-integer amount `12.5`, unknown currency `EUR`, amount `1e3` rejected, provider_txn_id over 128 characters), `test_malformed_quote_rejected`. Tag `TC-FR-14-2` on the caps test. `tests/architecture/test_recon_models.py::test_models_unmanaged_and_match_schema` after the migration.
- [ ] **2. Run:** `cd backend && pytest tests/unit/reconciliation/test_statement_parser.py -q`. Expect `ModuleNotFoundError: hub.reconciliation.domain.statement`.
- [ ] **3. Implement.** `domain/statement.py`: `def parse_statement(data: bytes, *, max_bytes: int = 10_485_760, max_rows: int = 100_000) -> Iterator[StatementLine]` using the standard `csv` module on a decoded stream (`utf-8-sig`), checking `len(data)` first, counting rows lazily, parsing `amount_minor` with `int(..., 10)` guarded by a regex `^-?\d{1,18}$` before any neutralisation, validating currency in `{"USD","CDF"}`, then `neutralise(cell)` on text columns only (`provider_txn_id`, `msisdn`) prefixing `'` when the first character is one of `= + - @`, tab or carriage return. Limits are parameters so the fetch job can pass a larger row cap (`RECON_FETCH_MAX_ROWS`, default 2,000,000). Add the expand-only migration: new table `hub.reconciliation_evidence (id uuid pk, item_id uuid not null references reconciliation_items, source text check in ('status_pull','statement_fetch'), provider text, provider_txn_id text, provider_status text, amount_minor bigint, currency char(3), fetched_at timestamptz default now())` with an index on `item_id`, plus `ALTER TABLE hub.reconciliation_items ADD COLUMN resolution text, ADD COLUMN occurred_at timestamptz, ADD COLUMN candidate_top_up_ids uuid[]`, with `GRANT SELECT ON ... TO hub_api; GRANT INSERT, SELECT ON hub.reconciliation_evidence TO hub_worker; GRANT UPDATE (resolution, ...)` as needed by the resolve path (hub_api and hub_worker already hold INSERT, UPDATE on `reconciliation_items`). Apply with a `RunSQL` wrapper. Unmanaged models mirror the columns.
- [ ] **4. Run** the unit tests and the architecture test until green; `make contracts`.
- [ ] **5. Commit:** `feat(reconciliation): statement parser with caps and formula neutralisation, evidence table`.

### Task 14.2: Statement import and the upload endpoint

- [ ] **1. Failing tests.** `tests/integration/reconciliation/test_import.py`: `test_upload_creates_batch_and_returns_202` (finance officer, multipart with `file`, `provider=mpesa_cd`, dates; batch `status == "open"`, `source == "uploaded"`, `statement_sha256` equals the file hash), `test_reimport_is_noop_and_overlap_skips_handled_lines` (second identical upload 409 `statement_already_imported`; one-byte-altered copy creates a batch but items whose `provider_ref` is already `pending_approval` or `resolved` elsewhere for the same provider are skipped and counted `already_handled`; no second exception for them), `test_concurrent_double_upload_one_batch` (two threads, one 202 one 409), `test_upload_403_for_other_roles` (support_agent, administrator), `test_oversize_413_and_rows_422_over_http`, `test_wrong_provider_422`. Tags `TC-FR-14-2`.
- [ ] **2. Run:** `pytest tests/integration/reconciliation/test_import.py -q`; expect 404 on the route.
- [ ] **3. Implement.** `application/import_statement.py` `ImportStatement.__call__(provider, period_start, period_end, data: bytes, source, actor, uow) -> UUID`: compute `sha256(data)`; insert the batch (`currency` from `RECON_PROVIDER_CURRENCY[provider]`, `source`, `imported_by`) with the unique constraint as the arbiter (catch `23505` on `uq_reconciliation_batches_statement` and raise `StatementAlreadyImported`, 409); persist nothing of matched lines; enqueue job `reconciliation.run` with the batch id and the parsed lines held as a compact object-store-free stash: the run job re-reads the lines from a `reconciliation_stash` Redis list keyed by batch id with a 24-hour TTL written in chunks, and a failed run is retried from the stash or marked `failed` when the stash expired (the operator re-uploads). Record an audit entry (`recon.statement_imported`, row count only). View `UploadProviderStatementView` (`@operation("uploadProviderStatement")`, parser `MultiPartParser` with `DATA_UPLOAD_MAX_MEMORY_SIZE` guarded by an explicit length check returning 413 `payload_too_large`).
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(reconciliation): idempotent statement import and upload endpoint`.

### Task 14.3: Matching and the five exception kinds

- [ ] **1. Failing tests.** `tests/unit/reconciliation/test_matching_domain.py`: `classify(line, payment)` returns `matched` for equal txn id, amount and currency; `amount_mismatch` for a different amount; `currency_mismatch` for a different currency (and for a line currency different from the batch currency); `statement_only` when no payment; `test_duplicate_line_second_occurrence_is_duplicate_in_statement`; `test_fuzzy_candidates_same_amount_and_phone_within_10_minutes` (10 minutes ok, 10 minutes 1 second not). `tests/integration/reconciliation/test_matching.py`: `test_all_lines_match_leaves_no_exceptions` (`TC-FR-14-1`: batch status `completed`, `matched == total_lines`, `exceptions == 0`, event `reconciliation.completed`), `test_five_kinds_created_with_open_status` (fixture file with one missing in ledger, one missing in statement, one duplicate, one amount difference, one currency difference; exactly five items with the five kinds, all `open`; `ledger_only` comes from a confirmed payment inside the period absent from the file) tagged `TC-FR-14-2`, `test_fuzzy_candidates_are_suggested_never_applied` (item `candidate_top_up_ids` set, `top_up_id` null), `test_run_uses_repeatable_read_snapshot`, `test_run_is_idempotent` (second run adds no items, `UNIQUE (batch_id, provider_ref)`), `test_totals_per_currency_in_summary`. `tests/integration/reconciliation/test_matching_scale.py::test_100k_lines_under_60s` (marker `slow`, `TC-FR-14-1`) and `test_1_2m_lines_under_10_min` (marker `nightly`).
- [ ] **2. Run:** `pytest tests/unit/reconciliation/test_matching_domain.py tests/integration/reconciliation/test_matching.py -q`; expect `ModuleNotFoundError ... matching`.
- [ ] **3. Implement.** `domain/matching.py`: pure `classify`, `FuzzyRule(window=timedelta(minutes=10))`. `application/run_matching.py` `RunMatching.__call__(batch_id)`: set `matching`; open a `REPEATABLE READ, READ ONLY` transaction on the primary for the reads; pass 1 streams the stash in chunks of 10,000 lines, tracks seen txn ids in a `set[str]`, looks up payments for the chunk with `PaymentLookup.confirmed_by_txn_ids` (one query per chunk, never per line), classifies each line, and collects exception rows; pass 2 pages `confirmed_between(period_start, period_end + 1 day)` with keyset `after`/`limit=10_000` and creates `ledger_only` items for payments whose txn id was not seen. Exception rows are inserted per chunk in their own short transaction with `INSERT ... ON CONFLICT (batch_id, provider_ref) DO NOTHING` and each stages `reconciliation.exception`. Lines already handled elsewhere (see Task 14.2) are skipped. `statement_only` and `amount_mismatch` items store `occurred_at` and `candidate_top_up_ids` from `payer_candidates`; the MSISDN is discarded after use. At the end set `provider_total_minor`, `ledger_total_minor`, the batch status (`completed` when zero open items, else `needs_review`; `failed` on an unrecoverable error with the reason in the audit log) and stage `reconciliation.completed`.
- [ ] **4. Run** until green; run the 100k test once.
- [ ] **5. Commit:** `feat(reconciliation): chunked matching with the five exception kinds`.

### Task 14.4: Exception queue, batches and evidence requests

- [ ] **1. Failing tests.** `tests/integration/reconciliation/test_exception_api.py`: `test_list_exceptions_filters_status_kind_batch_ref_and_cursor`, `test_get_exception_includes_evidence_and_candidates`, `test_list_batches_filter_provider_status`, `test_batch_summary_counts`, `test_administrator_and_agent_403` (D-14), `test_amounts_serialised_as_money`, `test_export_neutralises_formula_cells` (CSV export helper used by the UI: a `provider_reference` stored as `'=...` stays neutralised; `TC-FR-14-2` not needed). `tests/integration/reconciliation/test_evidence.py`: `test_request_evidence_queues_status_pull_and_stores_row` (POST `/reconciliation/exceptions/{id}/evidence` returns 202; after the job runs one `reconciliation_evidence` row with `source == "status_pull"`, provider status and amount from the fake provider), `test_evidence_only_for_open_items_409`, `test_evidence_pull_failure_records_nothing`, `test_statement_fetch_items_get_evidence_automatically` (items created from a `source == "fetched"` batch carry a `statement_fetch` evidence row).
- [ ] **2. Run:** `pytest tests/integration/reconciliation/test_exception_api.py tests/integration/reconciliation/test_evidence.py -q`; expect 404.
- [ ] **3. Implement.** Query objects with keyset pagination on `id`; serializers produce `expected` and `actual` Money (expected from the matched payment, actual from the line), `evidence[]` from `EvidenceRepository`, `resolution` from the new column, `approval_id`. `application/evidence.py`: `RequestEvidence` validates item `open`, enqueues `reconciliation.pull_evidence` with dedupe key `evidence:<item_id>`; `PullEvidenceJob` calls `PaymentLookup.pull_status(provider, provider_ref)`, inserts a `reconciliation_evidence` row with source `status_pull` only on a successful pull, and sets `reconciliation_items.evidence_source = 'status_pull'`. For fetched batches `RunMatching` inserts `statement_fetch` evidence rows for the exceptions it creates.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(reconciliation): exception queue, batch views and provider evidence`.

### Task 14.5: Resolutions through maker-checker

- [ ] **1. Failing tests.** `tests/integration/reconciliation/test_resolution_flow.py`: `test_resolution_is_proposal_then_posts_after_second_approver` (`POST .../resolve` with `post_to_suspense` returns 202, item `pending_approval`, `approval_id` set, no posting; a different finance lead approves with the payload hash; one `reconciliation_fix` transaction exists, item `resolved`, `posted_transaction_id` set, `resolved_by` is the approver; resulting entries balance; `TC-FR-14-3` e2e variant in `tests/e2e`), `test_self_approval_forbidden_by_user_and_by_session` (`TC-FR-14-4`), `test_mutated_payload_hash_conflict` (409 `approval_payload_mismatch`), `test_match_to_top_up_evidence_rules` (`TC-FR-14-5`: no `evidence_id` is 422 `evidence_required`; evidence of another transaction, evidence with status `failed`, evidence with another amount, an `upload` source: all refused; valid `status_pull` evidence accepted; evidence deleted or changed between proposal and approval makes execution fail with `evidence_required` and the item stays `pending_approval`), `test_conflict_of_interest_blocks_approver` (`TC-FR-14-6`: approver has a `staff_customer_link` to the matched top-up's customer: 403 `conflict_of_interest`), `test_resolution_failure_and_rejection_paths` (period locked at approval: 409 `period_locked`, item stays `pending_approval`; approver rejects: item back to `open`, note kept in the approval; third approval attempt on a consumed approval refused), `test_note_mandatory` (422 below 5 characters), `test_mark_explained_duplicate_resolves_without_approval`, `test_mark_explained_other_kinds_need_approval`, `test_invalid_resolution_for_kind` (for example `write_off_fee` on `statement_only` is 422), `test_confirm_non_delivery_calls_token_service` (fake `TokenService` records the call with the evidence reference; executes only after approval), `test_posting_replay_is_noop` (key `recon:<item>:resolve`).
- [ ] **2. Run:** `pytest tests/integration/reconciliation/test_resolution_flow.py -q`; expect 404 on `/resolve`.
- [ ] **3. Implement.** `domain/resolution.py`: `ALLOWED = {"statement_only": {"match_to_top_up","post_to_suspense","refund_customer","mark_explained"}, "ledger_only": {"mark_explained","confirm_non_delivery"}, "amount_mismatch": {"write_off_fee","post_to_suspense","mark_explained"}, "duplicate_in_statement": {"mark_explained"}, "currency_mismatch": {"post_to_suspense","mark_explained"}}` and pure builders returning signed line arrays: `match_to_top_up` debits `provider_clearing.<ccy>` and credits the customer wallet by the line amount; `post_to_suspense` and `refund_customer` debit `provider_clearing` and credit `suspense` by the line amount (for `amount_mismatch` the difference only); `write_off_fee` debits `fees` and credits `provider_clearing` by the difference. `application/resolve.py` `ProposeResolution.__call__(item_id, ReconResolve, maker, uow) -> ApprovalView`: lock the item (`select_for_update`), validate kind rules, validate evidence (belongs to the item, source system, provider status successful, amount and currency equal, top-up not already confirmed), set item to `pending_approval` with `resolution`, `resolution_note`, `top_up_id`, `evidence_source`, then call `ApprovalService.propose(action="recon.resolve", payload=<lines array for posting resolutions, an object `{"item_id","resolution"}` otherwise>, subject_customer_id=<customer of the top-up or None>)` and store `approval_id`. The posting payload is the bare lines array because `post_ledger_transaction` checks the approval hash against the lines (see Plan notes). `ReconResolveExecutor(action="recon.resolve", required_permission="reconciliation:approve")`: find the item by `approval_id`, re-validate evidence and top-up state, post through `LedgerService.post(kind="reconciliation_fix", posting_key=f"recon:{item.id}:resolve", approval_id=approval.id, effective_at=clock.now())` or call `TokenService.confirm_non_delivery`, then mark `resolved` (`written_off` for `write_off_fee`) with `resolved_by` the approver and `resolved_at`. `OnApprovalRejected` consumes `approval.rejected` with action `recon.resolve` and returns the item to `open`. The `duplicate_in_statement` plus `mark_explained` case resolves immediately (schema allows no approval there) and is audited.
- [ ] **4. Run** until green, plus the e2e test `tests/e2e/test_reconciliation_flow.py::test_finance_resolves_exception_end_to_end` (upload, run, resolve, approve, ledger balanced).
- [ ] **5. Commit:** `feat(reconciliation): maker-checker resolutions with provider evidence`.

### Task 14.6: Daily statement fetch, system exceptions and the period lock

- [ ] **1. Failing tests.** `tests/integration/reconciliation/test_daily_fetch.py`: `test_daily_job_fetches_each_provider_for_yesterday` (fake `ProviderGateway` returns lines for the day; batch `source == "fetched"`, `imported_by` null; a second run the same day returns 409 internally and creates nothing), `test_fetch_failure_one_provider_does_not_block_others`, `test_fetch_cap_uses_fetch_limit` (150,000 lines allowed through the fetch path, rejected through upload). `test_system_items.py`: `test_payment_reversed_with_shortfall_opens_ledger_only_item` (event `payment.reversed` with `shortfall_minor > 0` creates an item in the day's system batch, `provider_ref = chargeback:<top_up_id>`; zero shortfall creates none), `test_token_unknown_opens_item_and_is_deduped` (`token.failed` with `status unknown`; delivered twice gives one item via `consumer_inbox`). `tests/integration/reconciliation/test_resolution_flow.py::test_period_lock_blocked_by_open_items_and_allowed_after_resolution` (`TC-FR-14-5` is the evidence test; this one is the AC-FR-14-5 lock test, untagged): with an open item, the lock proposal from slice 03 returns 409 with `open_exceptions == 1`; after resolution it proceeds.
- [ ] **2. Run:** `pytest tests/integration/reconciliation/test_daily_fetch.py tests/integration/reconciliation/test_system_items.py -q`; expect import errors.
- [ ] **3. Implement.** `application/fetch_daily.py` `FetchDailyStatements(day=None)`: for each provider in `settings.RECON_PROVIDERS`, call the container's `ProviderGateway.fetch_statement(day)`, serialise lines canonically to CSV bytes (sorted by `provider_txn_id`) so the hash is stable, call `ImportStatement(source="fetched", row cap = RECON_FETCH_MAX_ROWS)`, swallow `StatementAlreadyImported`, isolate per-provider failures with a logged `provider_unavailable` and a metric. `application/system_items.py`: consumers for `payment.reversed` and `token.failed`, each deduped through `consumer_inbox`, creating items in a synthetic per-provider per-day batch (`source 'fetched'`, `statement_sha256 = sha256("system:<provider>:<day>")`, status `needs_review`), kind `ledger_only`, `ledger_transaction_id` set. `wiring.py` registers the cron `reconciliation.import_daily` at 04:00 UTC (singleton), the consumers, the executor, and replaces slice 03's default `OpenExceptionCounter` with `reconciliation.open_count` (count of items in `open` or `pending_approval` whose batch period overlaps the dates).
- [ ] **4. Run** the full `tests/integration/reconciliation` directory, then `make verify SLICE=14`.
- [ ] **5. Commit:** `feat(reconciliation): daily statement fetch, system exceptions and period-lock counter`.

## Verification

`make verify SLICE=14` runs, from the repository root:

1. `cd backend && pytest tests/unit/reconciliation tests/architecture/test_recon_models.py -q`: all pass.
2. `cd backend && pytest tests/integration/reconciliation -q -m "not nightly"`: all pass.
3. `cd backend && pytest tests/e2e/test_reconciliation_flow.py -q`: passes against the compose stack with the provider stub.
4. `make lint`: clean; import-linter shows `reconciliation` importing only `shared`, `identity.contracts`, `ledger.contracts`, `payments.contracts`, `meters.contracts`; no `INSERT INTO hub.ledger` string.
5. `make contracts`: alignment, schema, django-alignment, traceability checks pass (the new migration keeps the model-versus-database test at zero differences).
6. `pytest -m "tc and FR_14" backend/tests -q`: markers `TC-FR-14-1` to `TC-FR-14-6` collected and passing.
7. `schemathesis run api/openapi.yaml --include-operation-id uploadProviderStatement --include-operation-id listReconExceptions --include-operation-id resolveReconException`: no server errors and no schema violations.

## Demo script

1. `make up`, load fixtures, start the provider stub with a scripted statement: one line missing from the ledger, one confirmed payment missing from the file, one duplicate line, one amount difference, one currency difference.
2. As finance officer A upload the CSV (`POST /reconciliation/statements`). Observe 202; after the run the batch shows `needs_review` with five open items of the five kinds. Upload the same file again: observe 409 `statement_already_imported`.
3. Open the `statement_only` item, request evidence, wait for the job: observe a `status_pull` evidence row. Try `match_to_top_up` without evidence: observe 422 `evidence_required`.
4. Resolve with `match_to_top_up`, evidence and a note: observe item `pending_approval`, no ledger change. Approve as A: observe 403 `self_approval_forbidden`.
5. Approve as finance officer B with the payload hash: observe one `reconciliation_fix` posting, balanced, item `resolved`.
6. Try to lock the month while other items are open: observe 409 with the open count; resolve or write them off, lock with the maker-checker flow of slice 03, then approve another pending resolution dated in it: observe `period_locked`.

## Done criteria

Covers FR-14. Implements TC-FR-14-1, TC-FR-14-2, TC-FR-14-3, TC-FR-14-4, TC-FR-14-5 and TC-FR-14-6. `progress.md` holds the `make verify SLICE=14` output, the demo result, assumptions, and any change request.

## Plan notes for the tech lead

- `ReconResolve.resolution` (OpenAPI) is `match_to_top_up`, `post_to_suspense`, `write_off_fee`, `refund_customer`, `mark_explained`; `docs/03#2.12` and the slice index name `post_adjustment`, `write_off` and `confirm_non_delivery`. Followed the OpenAPI five plus `confirm_non_delivery`, accepted by the serializer for `ledger_only` items only; the enum needs a change request.
- The schema has no place for evidence rows, a resolution kind, the line time or fuzzy candidates, while `ReconException` returns `evidence[]`, `resolution` and the PRD requires candidates. Added an expand-only migration (new table `reconciliation_evidence`, three columns); the frozen `db/schema.sql` needs the same change folded in.
- `post_ledger_transaction` compares the approval hash with `hub.payload_hash(p_lines)`, so the posting resolutions store the bare lines array as the approval payload; `ReconException.approval_id` and the item row carry the context. A richer payload needs a schema change.
- The schema action is `recon.resolve`; `docs/03#2.14` says `reconciliation.resolve_money`; OpenAPI kind is `reconciliation_resolution`. Followed the schema.
- `docs/03#2.4` `confirmed_between` returns an unpaged list; 1.2 million lines need paging, a lookup by transaction ids and a status pull by provider reference, so this plan assumes `PaymentLookup` (slice 04 must provide it). Also assumed: a way for a matched statement-only payment to reach `confirmed` is not defined anywhere, so `match_to_top_up` credits the wallet by `reconciliation_fix` and does not change the top-up status or issue a token.
- `ReconStatementUpload` has no currency while `reconciliation_batches.currency` is NOT NULL; defaulted from `RECON_PROVIDER_CURRENCY`. The schema invariant tests use provider `mtn_stub`, OpenAPI lists `mpesa_cd`, `airtel_cd`, `orange_cd`.
- `ReconException` has no `statement_only` token or reversal kinds; reversal shortfalls and unknown tokens are modelled as `ledger_only` items in a synthetic per-day batch because `batch_id` is NOT NULL.
- `lockAccountingPeriod` open-count rule is implemented through slice 03's `OpenExceptionCounter`; see that slice's notes on the maker-checker gap.
- The statement CSV format is not defined in any document ("stub format" in the PRD); this plan defines `provider_txn_id,amount_minor,currency,occurred_at,msisdn`.
