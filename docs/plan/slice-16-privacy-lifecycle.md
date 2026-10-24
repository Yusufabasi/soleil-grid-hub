# Slice 16: Privacy and lifecycle

- **Goal:** customer data export (60-second presigned link), retention sweeps and telemetry partition drops driven by one retention table, customer erasure by pseudonymisation through `hub.erase_customer` with its blocking rules, 7-day cooling-off, two approvers, the erasure log and its replay after a restore, per-module erasure participants, and an audit trail for all of it.
- **Depends on:** slices 00 to 05 (skeleton, identity with approvals and audit, registry, ledger, payments, outbox and worker), slice 06 (tokens, for the unacknowledged-token blocker), slices 07 to 12 for the modules that hold personal data (notifications, telemetry, support, ai_gateway) because each registers a participant, slice 14 (open exceptions as an erasure blocker).
- **Requirements served:** FR-20, NFR-07 (erasure removes personal data from database, cache and object storage).
- **Size:** L, 8 days for a cheaper agent.
- **Companion files:** [fe-16-privacy.md](frontend/fe-16-privacy.md).
- **Spikes or open items:** [open] legal sign-off of the retention table (D-28): default, the values of `docs/04-data-model.md` section 6.2 are seeded as written and are data, changed by one migration. [open] per-administrator daily cap of export links: default 10, setting `EXPORT_LINK_DAILY_CAP`.

## Read first

- `#7.4 Authorization and authentication` (D-13, D-15, D-16, D-21), `#7.5 Data, infrastructure and operations` (D-22, D-28, D-29), `#2. Scope` (NFR-07 row)
- `docs/adr/0014-erasure-by-pseudonymisation.md`, `docs/adr/0007-maker-checker-approvals.md`
- `docs/03-code-architecture.md#2.13 admin (FR-15 views, FR-18 admin, FR-20)`, `#2.14 Approvals (maker-checker) (D-15, D-16)`, `#5. Ports and adapters`, `#7.5 Background jobs and scheduling`
- `docs/04-data-model.md#6. Partitioning, retention and erasure`, `#10.7 Writes that never go through the ORM`
- `db/schema.sql`: tables `data_exports`, `data_erasure_requests`, `erasure_log`, `approvals`; functions `scrub_customer`, `erase_customer`, `replay_erasure_log`, `ensure_meter_reading_partitions`, `audit_append`; the grants block for `privacy_owner` and `hub_worker`
- `docs/01-prd.md#FR-20`
- `docs/07-security-threat-model.md#T-27`, `#T-47`
- `docs/10-test-strategy.md#2.10 Controls added in revision 1` (row "Erasure")
- `api/openapi.yaml` operations: `createDataExport`, `listDataExports`, `getDataExport`, `createDataExportLink`, `createErasureRequest`, `listErasureRequests`, `getErasureRequest`, `verifyErasureRequest`, `approveErasureRequest`, `rejectErasureRequest`, `cancelErasureRequest`, `listRetentionPolicies`, `approveApproval`; schemas `DataExport`, `ErasureRequest`, `RetentionPolicy`

## Global constraints

- Export: one subject per request; scope only `profile`, `tickets`, `top_ups`, `tokens`, `notifications` (never `audit` or raw `ledger`); staff path needs administrator with attribute `dp_admin`, permission `export:bulk`, step-up within 300 seconds and an approval by a second administrator (action `data.export`); customer path needs a second proof of identity (D-21) and the same approval.
- A download link is presigned for exactly 60 seconds, issued only for status `ready` and not expired, needs step-up, every issue is audited, and no standing URL exists. A bundle lives at most 7 days (`ck_data_exports_expiry`).
- A bundle is JSON plus CSV, produced within 72 hours of approval, with cells beginning `=`, `+`, `-` or `@` neutralised in the CSV.
- Erasure is pseudonymisation in place by `hub.erase_customer(p_customer, p_request, p_performed_by)` only; ledger rows, accounts, amounts and wallet balances are never touched; no crypto-shredding in v1.
- Erasure is blocked while the wallet is non-zero, tokens are `pending`, `sent` or `unknown`, a legal hold exists, or an open reconciliation exception exists (error `erasure_blocked`, HTTP 409).
- Erasure needs: a second proof of identity, a 7-day cooling-off from verification with a notice to the old contact, an operator who is an active `dp_admin`, two distinct approvals (action `erasure.approve`, payload carries `request_id`) whose approvers differ from each other and from the requester.
- The erasure log is append-only, holds only customer id, request id and date, is never deleted, is copied off-database nightly, and `hub.replay_erasure_log()` runs after every restore.
- One retention table, owned by `docs/04-data-model.md` section 6.2; sweeps use batches (delete by id from a `LIMIT` subquery, short sleep), never one long transaction; the job's counts are audited.
- All privacy actions are audited in `audit_append` stream `privacy`; no personal data in audit detail, logs or outbox payloads.
- Export bundles reach the administrator only as the 60-second link; participants run as `hub_worker`.
- Every sweep and participant call is idempotent; POST endpoints use the 24-hour window (D-12).

## Review focus

1. **Erasure blockers not obvious from one table**: a zero wallet but a token still `sent`, a legal hold, an open reconciliation item, an approver who is the requester, a cooling-off one second short, and the same request executed twice. Pinned by `test_erase_customer_guards_each_block` and `test_execution_blockers_and_idempotence`, Task 16.4.
2. **A restore brings erased people back**: after restoring a backup taken before the erasure, replay must remove identifiers and a ticket body again, including orphan provider events carrying the phone number. Pinned by `test_restore_then_replay_erases_again`, Task 16.6.
3. **Presigned link misuse**: link requested for a non-`ready` export, after expiry, twice within the daily cap, by a customer for another customer's export, and a link that must stop working after 60 seconds. Pinned by `test_presigned_link_rules`, Task 16.2.
4. **Export contains too much or the wrong person**: scope excludes audit and ledger, another customer's rows never appear, formula cells are neutralised, a customer request without second proof is refused. Pinned by `test_bundle_contains_only_subject_data`, Task 16.2.
5. **Retention sweeps deleting what must stay**: ledger, approvals, erasure log, `top_ups` and tokens never swept; a provider event referenced by a top-up keeps its row. Pinned by `test_sweep_never_touches_protected_tables`, Task 16.5.

## Files

Paths are under `backend/src/hub/` unless stated; tests under `backend/tests/`. The `admin` app has label `hub_admin`.

- `admin/contracts.py`: `RetentionPolicy`, `Blocker`, `PrivacyService` Protocols; `admin/events.py`: `ErasureVerified`, `ErasureApproved`, `CustomerErased`, `ExportReady`.
- `admin/domain/erasure.py`: request state machine (pure), blocker aggregation, cooling-off rule.
- `admin/domain/export.py`: bundle assembly (pure), CSV neutralisation, scope validation.
- `admin/domain/retention.py`: `RetentionPolicy` value object and class-to-owner mapping.
- `admin/domain/errors.py`: `ErasureBlocked`, `ErasureInvalid`, `ExportNotReady`, `IdentityProofRequired`, `ExportCapReached`.
- `admin/infrastructure/models.py`: unmanaged `DataExport`, `DataErasureRequest`, `ErasureLog` (read only), `RetentionPolicyRow`.
- `admin/infrastructure/erasure.py`: the only caller of `erase_customer` and `replay_erasure_log` through `call_function`; maps `erasure_blocked` and `erasure_invalid` messages.
- `admin/infrastructure/retention.py`: calls `hub.retention_purge`, the partition functions and `hub.retention_policies`.
- `admin/infrastructure/repositories.py`: `ExportRepository`, `ErasureRepository`.
- `admin/application/exports.py`: `RequestExport`, `ReleaseExportExecutor`, `BuildExportJob`, `IssueExportLink`, `ExpireExportsJob`.
- `admin/application/erasure.py`: `RequestErasure`, `VerifyErasure`, `ProposeErasureApproval`, `ErasureApproveExecutor`, `RejectErasure`, `CancelErasure`, `ExecuteErasureJob`, `ExportErasureLogJob`.
- `admin/application/retention.py`: `RetentionSweepJob`, `ListRetentionPolicies`.
- `admin/application/wiring.py`: registers executors, cron jobs, consumers.
- `admin/api/serializers.py`, `views.py`, `urls.py`: `DataExportSerializer`, `DataExportCreateSerializer`, `ErasureRequestSerializer`, `ErasureCreateSerializer`, `ErasureDecisionSerializer`, `RetentionPolicySerializer`.
- `urls/api.py` (modify): include `hub.admin.api.urls`.
- `shared/contracts.py` (modify): extend `ErasureParticipant` with `blockers(customer_id) -> list[Blocker]` (default empty) and add `Blocker`.
- Participants, one `<module>/application/privacy.py` registered in each `wiring.py`, for: customers, payments, ledger, meters, telemetry, support, ai_gateway, notifications, reconciliation, identity, analytics.
- `db/migrations/NNNN_privacy_lifecycle.sql` and `shared/migrations/00NN_privacy_lifecycle.py`: table `hub.retention_policies` seeded from docs/04 section 6.2; definer functions `hub.retention_purge(p_class text, p_limit integer)`, `hub.detach_expired_meter_partition()`, `hub.drop_detached_meter_partition(p_partition text, p_rows bigint)`; grants to `hub_worker`.
- `tools/restore_replay.sh`: restore helper (import `erasure_log`, run `replay_erasure_log()`).
- Tests: `tests/unit/admin/test_erasure_domain.py`, `test_export_domain.py`; `tests/integration/admin/test_export_flow.py`, `test_export_link.py`, `test_erasure_api.py`, `test_erase_customer.py`, `test_erasure_execution.py`, `test_participants.py`, `test_retention.py`, `test_partition_drop.py`, `test_replay_after_restore.py`, `test_privacy_audit.py`; `tests/e2e/test_privacy_flow.py`; `tests/architecture/test_privacy_models.py`.

## Interfaces

**Consumes:**

```python
# identity (slice 01)
class ApprovalService(Protocol): def propose(self, *, action, payload, subject, subject_customer_id, maker, uow) -> ApprovalView: ...
class CustomerIdentityProof(Protocol): def verify(self, customer_id: UUID, proof: str) -> bool: ...    # second proof of identity (D-21)
class AuditLog(Protocol): def record(self, *, actor, action, target_type, target_id, detail, uow) -> None: ...   # stream "privacy" for this slice
# shared
class ObjectStore(Protocol): put(...); presigned_get(self, key: str, ttl_s: int) -> str; delete(self, key: str) -> None
class ArchiveStore(Protocol): def export_table(self, schema_table: str, key_prefix: str) -> ExportReceipt: ...   # rows, sha256; adapter over ObjectStore, defined here in shared/ports.py
class ApprovalExecutor, ApprovalExecutorRegistry, JobRegistry, Clock, IdGenerator, UnitOfWork, call_function
# events consumed: attachment.purge, payload.purge (queued by hub.scrub_customer), customer.erased
```

**Produces:**

```python
class ErasureParticipant(Protocol):                                   # shared/contracts.py
    name: str
    def export_subject(self, customer_id: UUID) -> SubjectExport: ...                  # {scope: str, rows: list[Mapping]}; scope in the five allowed
    def erase_subject(self, customer_id: UUID, *, uow: UnitOfWork) -> ErasureReport: ...   # idempotent; non-database residue only
    def retention_sweep(self, policy: RetentionPolicy, *, uow: UnitOfWork) -> int: ...     # rows affected
    def blockers(self, customer_id: UUID) -> list[Blocker]: ...

class PrivacyService(Protocol):                                       # admin/contracts.py
    def request_export(self, cmd: ExportCreate, actor: Principal, *, uow: UnitOfWork) -> ExportView: ...
    def issue_link(self, export_id: UUID, actor: Principal, *, uow: UnitOfWork) -> PresignedLink: ...   # ttl 60 s
    def request_erasure(self, cmd: ErasureCreate, actor: Principal, *, uow: UnitOfWork) -> ErasureView: ...
    def verify_erasure(self, request_id: UUID, actor: Principal, *, uow: UnitOfWork) -> ErasureView: ...
    def blockers(self, customer_id: UUID) -> list[Blocker]: ...
```

Events: `erasure.verified` (`request_id`, `customer_id`; triggers the notice to the old contact, `notice_sent_at` is set when staged), `erasure.approved` (`request_id`, `performed_by`), `customer.erased` (`customer_id`, `request_id`), `export.ready` (`export_id`, `customer_id`). Approval executors: `data.export` (permission `export:approve`), `erasure.approve` (permission `erasure:approve`). Cron jobs: `admin.retention_sweep` (02:00 UTC), `admin.expire_exports` (hourly), `admin.execute_erasure` (consumer of `erasure.approved`, plus a 15-minute poller as backstop), `admin.export_erasure_log` (03:00 UTC, copies `erasure_log` rows to the write-once bucket through `ArchiveStore`). Audit actions (stream `privacy`): `export.requested`, `export.approved`, `export.link_issued`, `erasure.requested`, `erasure.verified`, `erasure.approved`, `erasure.rejected`, `erasure.cancelled`, `customer.erase` (written by the function), `retention.sweep`, `restore.replay`. Seeded table `hub.retention_policies(data_class, retention_days, action, legal_basis)`.

## Tasks

### Task 16.1: Retention table, purge functions and the policy endpoint

- [ ] **1. Failing tests.** `tests/integration/admin/test_retention.py`: `test_retention_table_matches_docs_04` (expected dict in the test copied from section 6.2: `telemetry_raw` 90 `delete`, `reading_batches` 7 `delete`, `tickets` 1095 `anonymise`, `ai_runs` 395 `delete`, `ai_runs_output` 90 `anonymise`, `provider_events` 395 `delete`, `webhook_quarantine` 30 `delete`, `notifications` 395 `delete`, `alerts` 395 `delete`, `data_exports` 395 `delete`, `data_erasure_requests` 1825 `delete`, `meter_daily_usage` 1825 `delete`), `test_list_retention_policies_endpoint` (finance, agent get 403; administrator gets the rows with `legal_basis`), `test_purge_function_enforces_minimum_age` (calling `hub.retention_purge('ai_runs', 1000)` removes only rows older than the seeded days), `test_purge_function_rejects_unknown_class` (raises), `test_purge_not_callable_by_hub_api`. `tests/architecture/test_privacy_models.py::test_models_unmanaged`.
- [ ] **2. Run:** `cd backend && pytest tests/integration/admin/test_retention.py -q`. Expect failure `relation "hub.retention_policies" does not exist`.
- [ ] **3. Implement.** Migration `NNNN_privacy_lifecycle.sql` (next free number; expand only): table `hub.retention_policies` seeded as above; `hub.retention_purge(p_class text, p_limit integer) RETURNS integer`, `SECURITY DEFINER SET search_path = pg_catalog, hub, pg_temp`, owner `migrator`, `REVOKE ALL FROM PUBLIC`, `GRANT EXECUTE TO hub_worker`; it reads the days from `hub.retention_policies`, allows only the in-scope classes (a fixed `CASE`), and per class acts on one batch of `p_limit` ids: delete (`webhook_quarantine` by `received_at`, `ai_runs` by `created_at`, `notifications` and `alerts` by `created_at` and `opened_at`, `data_erasure_requests` by `completed_at`, `data_exports` rows by `created_at`, `meter_daily_usage`, `reading_batches`), null (`ai_runs.output`, `generated_sql`, `payload_ref` at 90 days), pseudonymise (`tickets` resolved at least 1,095 days ago: `subject`, message `body` set to `[erased]`, rows kept), and for `provider_events` null `payload` at the cutoff and delete only rows no `top_ups` row references. It never names ledger, approvals, `erasure_log`, `top_ups`, `meter_tokens`, `reconciliation_*`, `audit_*`. Each branch returns the row count. Django `RunSQL` wrapper; unmanaged models; `ListRetentionPolicies` view (`@operation("listRetentionPolicies")`) reading the table, `action` mapped to `delete` or `anonymise`.
- [ ] **4. Run** until green; `make contracts`.
- [ ] **5. Commit:** `feat(admin): retention table and guarded purge function`.

### Task 16.2: Data export with approval and the 60-second link

- [ ] **1. Failing tests.** `tests/unit/admin/test_export_domain.py`: `test_scope_rejects_ledger_and_audit`, `test_csv_cells_neutralised`, `test_bundle_has_json_and_csv`. `tests/integration/admin/test_export_flow.py`: `test_staff_export_needs_dp_admin_stepup_and_second_approver` (`TC-FR-20-1`: administrator without `dp_admin` 403; without step-up 401 `step_up_required`; with both, 202 and `Approval` pending, `data_exports.status == "requested"`; same user approving 403 `self_approval_forbidden`; a second administrator approves, status moves `approved` then `ready` after `BuildExportJob`; `object_key` set; `expires_at` within 7 days), `test_bundle_contains_only_subject_data` (`TC-FR-20-1`: the zip holds only customer A rows, no `audit` or `ledger` entry; a CSV cell `=cmd` is `'=cmd`), `test_customer_export_requires_second_proof` (`TC-FR-20-7`: missing or invalid `identity_proof` is 422 `identity_proof_required` and an audit row; valid proof creates a row with `requested_by` null), `test_customer_cannot_request_other_customer_export` (`TC-FR-20-3`: 403 and audited), `test_bulk_export_without_permission_403` (AC-FR-20-5, role without `export:bulk`; allowed export audited with row count). `tests/integration/admin/test_export_link.py::test_presigned_link_rules` (link for `requested` or `expired` 409; ready export returns a URL whose signed TTL is 60 (fake `ObjectStore` records `ttl_s == 60`); each issue audited `export.link_issued`; the 11th link in a day for one administrator is 429 `export_cap_reached`; a customer asking for another customer's export is 404; step-up required; `expires_at` passed 409) and `test_expire_exports_job_deletes_object_keeps_row` (object deleted, status `expired`, row kept).
- [ ] **2. Run:** `pytest tests/unit/admin tests/integration/admin/test_export_flow.py tests/integration/admin/test_export_link.py -q`. Expect 404 on the routes and import errors.
- [ ] **3. Implement.** `RequestExport` validates scope (`profile`, `tickets`, `top_ups`, `tokens`, `notifications`), checks the principal (staff path: `dp_admin` attribute, `export:bulk`, one `customer_id`; customer path: `CustomerIdentityProof.verify`, customer id from the principal never from the body), inserts `data_exports` (`requested`, `expires_at = now + 7 days`), then proposes approval `data.export` with `subject_customer_id`; a customer-initiated row has `requested_by` null and a `dp_admin` proposes the approval through `POST /approvals` (kind `data_export`, `subject_id` the export id; see Plan notes). `ReleaseExportExecutor` (action `data.export`) sets `approved`, enqueues `admin.build_export`. `BuildExportJob` opens a `REPEATABLE READ, READ ONLY` transaction, calls every participant's `export_subject`, assembles the zip (JSON per scope and CSV per scope, neutralised), puts it under `exports/<export_id>.zip` through `ObjectStore`, sets `ready` and `object_key`, stages `export.ready`. `IssueExportLink` checks status `ready` and not expired, counts today's `export.link_issued` audit rows for the actor against `EXPORT_LINK_DAILY_CAP`, returns `ObjectStore.presigned_get(key, ttl_s=60)` and audits. `ExpireExportsJob` (hourly) deletes objects past `expires_at` and sets `expired`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(admin): subject data export with approval and 60-second presigned link`.

### Task 16.3: Participants

- [ ] **1. Failing tests.** `tests/integration/admin/test_participants.py`: `test_every_module_registers_a_participant` (registry names equal the eleven module names above),  `test_ledger_participant_exports_nothing_and_erases_nothing` (ledger totals and entry count unchanged after `erase_subject`), `test_support_participant_purges_attachment_objects_and_summary_cache` (fake `ObjectStore` receives deletes for keys carried by `attachment.purge`; Redis key `summary:<ticket_id>` gone), `test_ai_participant_deletes_payload_objects` (`payload.purge` consumer), `test_customers_participant_clears_cache_keys`, `test_notifications_participant_cancels_queued_messages`, `test_participant_handlers_are_idempotent`. Tag `TC-NFR-07-3` on the object-store and cache tests. `test_reconciliation_participant_blocks_with_open_items` (open item for the customer's top-up gives blocker `open_dispute`).
- [ ] **2. Run:** `pytest tests/integration/admin/test_participants.py -q`; expect an empty registry.
- [ ] **3. Implement.** One `privacy.py` per module implementing `ErasureParticipant`: `export_subject` reads through the module's own repository or query object and returns plain mappings of only the subject's rows (no internal ids of other customers, no raw payloads, no AI prompts); `erase_subject` removes non-database residue only (object keys, cache keys) because `scrub_customer` already pseudonymised the rows; the `attachment.purge` consumer (support) and `payload.purge` consumer (ai_gateway) call `ObjectStore.delete` with `consumer_inbox` dedupe; `customer.erased` is consumed by each participant's `erase_subject` (at-least-once, idempotent). `blockers`: `reconciliation` returns `open_dispute` when an `open` or `pending_approval` item references any top-up of the customer; `meters` returns `unacknowledged_tokens`; `ledger` returns `wallet_non_zero`; the legal hold is read from the request. Extend `shared/contracts.py` as listed in Files.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(privacy): per-module erasure and export participants`.

### Task 16.4: Erasure workflow and `erase_customer`

- [ ] **1. Failing tests.** `tests/unit/admin/test_erasure_domain.py`: state machine `pending_approval -> verified -> approved -> completed`, `cooling_off_ends(verified_at) == verified_at + 7 days`, blocker aggregation order. `tests/integration/admin/test_erasure_api.py`: `test_customer_request_needs_second_proof` (`TC-FR-20-7`: OTP alone leaves `pending_approval`; no proof is refused and audited), `test_request_for_other_customer_denied_and_audited` (`TC-FR-20-3`), `test_verify_blocked_by_wallet_tokens_hold_and_dispute` (409 `erasure_blocked` with `blockers[]` codes `wallet_non_zero`, `unacknowledged_tokens`, `legal_hold` (status `blocked_by_retention`), `open_dispute`; `balance_or_dispute_open` accepted as the PRD alias in the audit detail), `test_verify_starts_cooling_off_and_notice` (7 days, outbox `erasure.verified`, `notice_sent_at` set), `test_approve_before_cooling_off_end_409`, `test_two_approvals_by_distinct_dp_admins` (requester excluded; same approver twice 409; non-`dp_admin` 403). `tests/integration/admin/test_erase_customer.py::test_erase_customer_guards_each_block` (`TC-FR-20-5`, one parametrised case each: wrong customer, wrong kind, status not approved, legal hold, cooling-off one second short, operator not `dp_admin`, one approval only, same approver twice, approver equals requester, wallet non-zero, token `pending`/`sent`/`unknown`; every case raises and changes zero rows) and `test_erasure_pseudonymises_and_ledger_untouched` (`TC-FR-20-2`: name, phone, email, ticket bodies and subject, `payer_msisdn`, orphan provider event, outbox payloads scrubbed; `ledger_entries` hash, `wallet_balances` and top-up amounts unchanged; ledger still sums to zero; `erasure_log` row exists; audit `customer.erase` in stream `privacy`; the log table refuses UPDATE and DELETE). `tests/integration/admin/test_erasure_execution.py::test_execution_blockers_and_idempotence` (second `admin.execute_erasure` for a completed request is a no-op; a blocker that appears after approval stops execution and returns the request to `verified` with an alert event; `customer.erased` staged once).
- [ ] **2. Run:** `pytest tests/unit/admin/test_erasure_domain.py tests/integration/admin/test_erasure_api.py tests/integration/admin/test_erase_customer.py tests/integration/admin/test_erasure_execution.py -q`; expect 404 and import errors.
- [ ] **3. Implement.** `RequestErasure` (customer path: id from principal, `CustomerIdentityProof.verify`, sets `second_proof_at`; staff path: `dp_admin`, step-up). `VerifyErasure` aggregates `blockers` from every participant plus wallet and token checks, records `legal_hold_reason` if set, sets `verified` and `cooling_off_until = greatest(received_at + 7 days, now + 7 days)`, stages `erasure.verified` for the notice to the old contact, sets `notice_sent_at`. `ProposeErasureApproval` (`approveErasureRequest`): refuses before `cooling_off_until`, proposes action `erasure.approve` with payload `{"request_id": "<uuid>"}` (the function reads that key), and records the proposer; a request carries two approvals, linked to `approval_1_id` then `approval_2_id` by `ErasureApproveExecutor`, which leaves each approval in status `approved` (the definer function consumes them), sets the request `approved` when both exist with distinct approvers (neither the requester), and stages `erasure.approved` with `performed_by` the last approver. `ExecuteErasureJob` re-checks blockers, then `call_function("erase_customer", customer_id, request_id, performed_by)`; maps `erasure_blocked` to `ErasureBlocked` and `erasure_invalid` to `ErasureInvalid`; after success stages `customer.erased`. `infrastructure/erasure.py` is the only module that mentions the function.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(admin): erasure workflow with cooling-off, two approvers and erase_customer`.

### Task 16.5: Retention sweeps and partition drops

- [ ] **1. Failing tests.** `tests/integration/admin/test_retention.py` additions: `test_sweep_never_touches_protected_tables` (row counts and hashes of `ledger_*`, `approvals`, `erasure_log`, `top_ups`, `meter_tokens`, `reconciliation_*`, `audit_log` identical after `RetentionSweepJob`), `test_sweep_is_batched_and_idempotent` (25,000 old `webhook_quarantine` rows, batch 5,000, rerun returns zero), `test_provider_event_referenced_by_top_up_keeps_row_payload_nulled`, `test_ticket_pseudonymised_three_years_after_resolution` (subject and bodies `[erased]`, row kept), `test_sweep_counts_audited` (`retention.sweep` entry holds counts per class, no personal data; `TC-NFR-07-3` not needed). `tests/integration/admin/test_partition_drop.py`: `test_expired_partition_exported_verified_then_dropped` (partition with range end older than 90 days; `ArchiveStore` fake returns row count and sha256; after the job the table is gone and a receipt is audited), `test_export_mismatch_keeps_partition` (checksum differs: partition stays detached, alert event, nothing dropped), `test_recent_and_default_partition_never_dropped`.
- [ ] **2. Run:** `pytest tests/integration/admin/test_retention.py tests/integration/admin/test_partition_drop.py -q`; expect missing job.
- [ ] **3. Implement.** `RetentionSweepJob`: reads `hub.retention_policies`, calls each participant's `retention_sweep(policy, uow)`; participants (`telemetry`, `support`, `ai_gateway`, `payments`, `notifications`, `customers` for exports and erasure requests) invoke `RetentionRepository.purge(class, batch=5000)` in a loop with a 50 ms sleep until it returns zero, never inside one transaction; one `retention.sweep` audit record holds the counts. The telemetry participant also runs the partition path: `detach_expired_meter_partition()` (definer function, `lock_timeout = '2s'`, retry three times, only partitions whose range end is older than the `telemetry_raw` days, never the default partition), `ArchiveStore.export_table`, verify row count and checksum against the function's `count(*)`, then `drop_detached_meter_partition(name, rows)`; the function re-checks the row count and name pattern `meter_readings_YYYY_MM`. Classes owned by other jobs (outbox, inbox, idempotency keys, sessions, OTP, logs, backups, audit log, erasure log, ledger) are not swept here. Cron `admin.retention_sweep` at 02:00 UTC.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(admin): batched retention sweeps and telemetry partition drops`.

### Task 16.6: Erasure log archive, replay after restore, audit and end-to-end

- [ ] **1. Failing tests.** `tests/integration/admin/test_replay_after_restore.py::test_restore_then_replay_erases_again` (`TC-FR-20-6`: `pg_dump` a scratch copy, erase a canary customer in the live database, restore the dump into a second scratch database, see identifiers and a ticket body present, import the `erasure_log` copy, run `tools/restore_replay.sh`: identifiers, body and orphan provider event scrubbed, ledger untouched; replaying twice is harmless), `test_erasure_log_exported_nightly` (a second run copies nothing; the copy holds only customer id, request id, date). `tests/integration/admin/test_privacy_audit.py::test_every_privacy_action_is_audited` (`TC-FR-20-3`, `TC-FR-20-7`: each listed action writes one `privacy` record without personal data; denied attempts are audited as `export.denied`, `erasure.denied`; `hub.verify_audit_chain('privacy')` passes). `tests/e2e/test_privacy_flow.py::test_operator_runs_erasure_in_ui_flow` (`TC-FR-20-4`, API-level: admin M and admin P cross-approve, the request reaches `completed` with a `completed_at` and a visible audit entry; ledger totals before and after equal) and `test_export_then_link_then_expiry` (60-second link expires with the fake clock).
- [ ] **2. Run:** `pytest tests/integration/admin/test_replay_after_restore.py tests/integration/admin/test_privacy_audit.py tests/e2e/test_privacy_flow.py -q`; expect missing script and job.
- [ ] **3. Implement.** `ExportErasureLogJob` copies `erasure_log` rows newer than the last exported id to the write-once bucket through `ArchiveStore` (`erasure-log/<date>.jsonl`), idempotent by id. `tools/restore_replay.sh <scratch_db_url> <erasure_log_jsonl>` loads the rows as `migrator` (ignoring existing), runs `SELECT hub.replay_erasure_log();`, prints the count and writes a `restore.replay` audit record. Add missing audit calls as the last statement of each transaction.
- [ ] **4. Run** the whole `tests/integration/admin` directory and `tests/e2e/test_privacy_flow.py`, then `make verify SLICE=16`.
- [ ] **5. Commit:** `feat(admin): erasure log archive, replay after restore and privacy audit`.

## Verification

`make verify SLICE=16` runs, from the repository root:

1. `cd backend && pytest tests/unit/admin tests/architecture/test_privacy_models.py -q`: all pass.
2. `cd backend && pytest tests/integration/admin -q`: all pass.
3. `cd backend && pytest tests/e2e/test_privacy_flow.py -q`: passes on the compose stack with the in-memory or MinIO `ObjectStore`.
4. `make lint`: clean; import-linter shows `admin` importing no business module (participants only through the container registry); the lint rule finds `erase_customer` and `replay_erasure_log` named only in `admin/infrastructure/erasure.py`.
5. `make contracts`: alignment, schema, django-alignment (new models and migration), traceability checks pass; `psql -f db/invariant_tests.sql` on a fresh database: no unexpected results.
6. `pytest -m "tc and (FR_20 or NFR_07)" backend/tests -q`: markers `TC-FR-20-1` to `TC-FR-20-7` and `TC-NFR-07-3` collected and passing.

## Demo script

1. `make up`, load fixtures: customer C with a ticket, a top-up, a notification and an acknowledged token; wallet zero.
2. As `dp_admin` A (step-up) request an export of C; B approves; the bundle reaches `ready`. Request the link and fetch it within 60 seconds (works) and after (fails). Check the audit entries.
3. Request erasure for C with the customer's second proof. Observe `pending_approval`, then as A verify: observe `cooling_off_ends_at` 7 days ahead and the notice event. Credit C's wallet 5.00 USD through slice 03 and verify again: observe 409 `erasure_blocked` with `wallet_non_zero`; reverse the credit.
4. Advance the clock 7 days; A and B cross-approve (A proposes, B approves; B proposes, A approves). Observe `completed`.
5. Query C: name and phone are `[erased]` or null; ticket body `[erased]`; `GET /ledger/accounts` totals unchanged and the ledger sums to zero; the object-store attachment key is gone.
6. Restore a backup taken before step 4 into a scratch database, observe C's name, run `tools/restore_replay.sh`, observe it gone.
7. Run the retention sweep on old seeded rows: counts appear in the audit log, the ledger is untouched.

## Done criteria

Covers FR-20 and, for erasure of personal data from database, cache and object storage, NFR-07. Implements TC-FR-20-1, TC-FR-20-2, TC-FR-20-3, TC-FR-20-4, TC-FR-20-5, TC-FR-20-6, TC-FR-20-7 and TC-NFR-07-3. `progress.md` holds the output of `make verify SLICE=16`, the demo result, assumptions and change requests.

## Plan notes for the tech lead

- `data_exports.scope` in the schema allows `profile, tickets, top_ups, tokens, notifications`; OpenAPI `DataExport.scope` allows `profile, meters, top_ups, ledger, tickets, notifications`; the PRD lists profile, meters, top-ups, tickets and notifications. Followed the schema (D-01) and the threat model (no raw ledger); `meters` and `ledger` values would be rejected with 422 until the OpenAPI enum is aligned.
- Export statuses differ (schema `requested, approved, preparing, ready, expired, failed`; OpenAPI adds `pending_approval, queued, processing, rejected`); the serializer maps them. `format` and `sha256` have no columns; bundles hold both formats.
- The approvals table requires `maker_user_id` (a staff user), so a customer cannot be a maker. The plan keeps customer export and erasure requests as rows with `requested_by` null and has a `dp_admin` propose the approval. Decide whether a customer-originated approval needs a system maker instead.
- `erase_customer` demands two approvals with distinct approvers, both `approved` and not consumed; `docs/03#2.14` consumes nothing after execution. The plan needs `ApprovalService.approve` to leave `erasure.approve` approvals in `approved` (slice 01 must not mark them consumed) and defines a cross-approval default of two `dp_admin` makers and approvers, since no document says who the two approvals are. [open] ownership of that choice.
- The SQL function does not check open disputes (OpenAPI `open_dispute`, PRD `balance_or_dispute_open`); the plan checks them in `VerifyErasure` and `ExecuteErasureJob` through a `blockers` extension of the docs/03 Protocol.
- The schema gives `hub_worker` no DELETE on most retained tables and no DDL for partition drops (only `ensure_meter_reading_partitions` exists), and `tickets` has no closed timestamp (`resolved_at` is used). The plan adds a seeded `hub.retention_policies` table and three migrator-owned definer functions in an additive migration; `db/schema.sql` needs the same change folded in.
- `provider_events` rows referenced by `top_ups` cannot be deleted at 13 months (FK); the plan nulls their payload and keeps the row.
- Schema `cooling_off_until` defaults to receipt plus 7 days; OpenAPI says 7 days after verification. Followed OpenAPI.
