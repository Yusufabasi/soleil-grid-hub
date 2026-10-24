# Slice 02: Registry (customers, sites, plans, meters)

- **Goal:** staff create, search, update and close customers; operations creates sites and meters and assigns meters; finance versions plan tariffs through maker-checker; all of it with optimistic concurrency (`version`, `If-Match`), site scope, a pseudonymous customer view for operations, and a CSV import for bulk loading.
- **Depends on:** slice 00 and slice 01 (`Principal`, `PermissionsYamlPermission`, `AuditLog`, `ApprovalService`, `ApprovalExecutorRegistry`, `require_site`, `CustomerLookup` Protocol).
- **Requirements served:** FR-01, FR-16 (registry strings, customer `locale`, localised errors), FR-15 (site scope and object-level parts), D-08 (tariff versioning), D-11, D-14.
- **Size:** L, 9 days.
- **Companion files:** `docs/plan/frontend/fe-02-registry.md` (customer, site, plan and meter screens; another author, not linked because it may not exist yet).
- **Spikes or open items:** (1) **[open] tariff change cap:** the schema trigger caps a change at 25 percent per revision; default is that value, not configurable in Python. (2) **[open] CSV import channel:** no HTTP route exists (Plan notes 1); default is the management command `import_registry`. (3) **[open] phone masking format:** default keep country code and last two digits, for example `+243*****42`.

## Read first

- `docs/00-shared-context.md#7.3 Money rules` (D-08, D-11)
- `docs/00-shared-context.md#7.4 Authorization and authentication` (D-13, D-14, D-15)
- `docs/00-shared-context.md#7.5 Data, infrastructure and operations` (D-22 row-level security, D-23 `version` column)
- `docs/03-code-architecture.md#2.3 customers (FR-01, part of FR-20)`
- `docs/03-code-architecture.md#2.6 meters (FR-01 device part, FR-05, part of FR-07)`
- `docs/03-code-architecture.md#7.7 API layer conventions (Django REST framework)`
- `docs/04-data-model.md#4.3` (row-level security honesty note; find with Grep `4.3`) and the section on the Django mapping (Grep `Django mapping`)
- `db/schema.sql` sections 3 (registry), 3b (`plan_tariffs`, `current_tariff`), `meter_assignments`, `record_meter_assignment`, section 12 (RLS)
- `docs/10-test-strategy.md#8 Traceability matrix` rows FR-01 and FR-16

## Global constraints

- One customer per phone number: unique `phone_e164` (D-11); duplicate gives 409 `duplicate_resource`.
- Versioned resources (customers, meters) require `If-Match`: stale gives 412 `precondition_failed`, missing gives 428 `precondition_required`; `ETag` is weak, derived from `version`. The database trigger `bump_version` owns the increment.
- Tariffs are versioned by `effective_from`, never edited, need maker-checker (`tariff.change`), approver holds `finance_lead`, change at most 25 percent per revision (D-08).
- Duplicate meter serial gives 409 `serial_conflict`; a decommissioned meter is refused for top-up and token issuance (FR-01).
- Operations engineers see only `CustomerPseudonymous` fields; support agents see masked phone; administrator has no customer access (D-14).
- Staff outside a customer's or meter's site get 404, not 403 (existence must not leak); site scope comes from `user_sites` and `users.all_sites`, never from client input.
- Money is integer minor units plus currency; amounts in `Plan` are `Money` objects; no floats.
- Ids from `IdGenerator`; time from `Clock`; transactions through `UnitOfWork`; models `managed = False`; no import of another module's models.
- Every create, update, close and assignment writes an audit event on stream `registry`; payloads hold ids and field names only, never phone or name values.
- CSV cells starting with `=`, `+`, `-`, `@` are neutralised on import and export (D-30 formula neutralisation).

## Review focus

1. **Two operators assign the same meter to different customers at once**: exactly one wins, the other gets 409 (or 412 on a stale version); never two rows claiming one meter. Pinned by `test_concurrent_assign_exactly_one_wins`; Task 02.6.
2. **Phone typed in different shapes** (`+243 81 234 5678`, `0812345678`, trailing spaces) must normalise or be rejected before the uniqueness check, so the same person is not created twice. Pinned by `test_phone_normalisation_and_duplicate_409`; Task 02.3.
3. **A CSV with one bad row in 1,000** must import nothing and report the row number and reason; a re-upload of the fixed file must not duplicate rows imported earlier. Pinned by `test_import_all_or_nothing_and_idempotent_rerun`; Task 02.8.
4. **Support agent of site A requests a customer of site B by id**: 404 and an audit entry, also through the list filter `site_id=B`. Pinned by `test_other_site_customer_404_and_list_excludes`; Task 02.5.
5. **Tariff proposed with `effective_from` in the past, or by an approver who is not finance lead, or a 26 percent change**: refused with distinct codes; the approved tariff must not be visible as current before its date. Pinned by `test_tariff_effective_from_in_past_422` and `test_tariff_not_current_before_effective_from`; Task 02.4.

## Files

Under `backend/` unless stated; tests under `backend/tests/`.

- `src/hub/customers/apps.py`, `models.py` (shim): app registration.
- `src/hub/customers/infrastructure/models.py`: unmanaged `Customer`, `Site`, `Plan`, `PlanTariff`, `StaffCustomerLink` is read through identity contracts only.
- `src/hub/customers/infrastructure/repositories.py`: `CustomerRepository`, `SiteRepository`, `PlanRepository`.
- `src/hub/customers/infrastructure/queries.py`: `CustomerQueries` (search, cursor, masked and pseudonymous projections).
- `src/hub/customers/domain/phone.py`: `normalise_e164(raw, default_prefix) -> str`.
- `src/hub/customers/domain/errors.py`: `DuplicatePhone`, `CustomerHasBalance`, `InvalidTariff`, domain codes.
- `src/hub/customers/domain/masking.py`: `mask_phone`, `to_pseudonymous`.
- `src/hub/customers/application/customers.py`: `RegisterCustomer`, `UpdateCustomer`, `CloseCustomer`, `GetCustomer`, `ListCustomers`.
- `src/hub/customers/application/sites_plans.py`: `CreateSite`, `UpdateSite`, `CreatePlan`, `UpdatePlan`, `ListPlans`.
- `src/hub/customers/application/tariffs.py`: `ProposeTariff`, `TariffChangeExecutor`.
- `src/hub/customers/application/directory.py`: `CustomerDirectoryImpl` (also binds identity's `CustomerLookup`).
- `src/hub/customers/application/wiring.py`: registers executor and directory.
- `src/hub/customers/contracts.py`: public Protocols and DTOs.
- `src/hub/customers/events.py`: `CustomerRegistered`, `CustomerStatusChanged`.
- `src/hub/customers/api/serializers.py`, `views.py`, `urls.py`: DRF resources for `/customers`, `/sites`, `/plans`, `/plans/{id}/tariffs`.
- `src/hub/meters/apps.py`, `models.py` (shim); `infrastructure/models.py`: `Meter`, `MeterAssignment` (read only), `Gateway` (read only here), `MeterState` (read only here).
- `src/hub/meters/infrastructure/repositories.py`: `MeterRepository` (`lock_for_assign`).
- `src/hub/meters/application/registry.py`: `RegisterMeter`, `UpdateMeter`, `DecommissionMeter`, `ListMeters`, `AssignMeter`.
- `src/hub/meters/contracts.py`: `MeterRegistry` Protocol, `MeterRef`.
- `src/hub/meters/api/serializers.py`, `views.py`, `urls.py`: `/meters`, `/customers/{id}/meters`.
- `src/hub/shared/api/concurrency.py`: `require_if_match(request, current_version)`, `etag_for(version)`, `IfMatchMixin`.
- `src/hub/shared/management/commands/import_registry.py`: CSV import command.
- `src/hub/customers/application/csv_import.py`: `RegistryCsvImporter`.
- `db/seed/registry_demo.json` (modify): demo sites, plans, tariffs, customers.
- `tests/unit/customers/`, `tests/integration/customers/`, `tests/integration/meters/`, `tests/contract/test_registry_matrix.py`: test files named per task.
- `tests/fixtures/registry/customers_ok.csv`, `customers_one_bad_row.csv`, `meters_ok.csv`: import fixtures.

## Interfaces

**Consumes (slice 01):** `Principal`, `Authorizer.require_site(principal, site_id)`, `AuditLog.record(...)`, `ApprovalService.propose(...)`, `ApprovalExecutor` and `ApprovalExecutorRegistry.register`, `PermissionsYamlPermission`, `CustomerLookup` Protocol (`find_active_by_phone(e164) -> UUID | None`). **Consumes (slice 00):** `UnitOfWork`, `Clock`, `IdGenerator`, `@operation`, `@idempotent`, `CursorEnvelopePagination`.

**Produces:**

```python
# customers/contracts.py
@dataclass(frozen=True, slots=True)
class CustomerView: id: UUID; site_id: UUID; plan_id: UUID; full_name: str; phone_e164: str; email: str | None; locale: str; status: str; version: int
@dataclass(frozen=True, slots=True)
class PlanView: id: UUID; code: str; currency: str; min_topup_minor: int; max_topup_minor: int; status: str
@dataclass(frozen=True, slots=True)
class EnergyQuote: plan_tariff_id: UUID; tariff_minor_per_kwh: int; energy_wh: int

class CustomerDirectory(Protocol):
    def get(self, customer_id: UUID) -> CustomerView: ...
    def current_plan(self, customer_id: UUID) -> PlanView: ...
    def quote_energy(self, customer_id: UUID, amount: Money, at: datetime) -> EnergyQuote: ...   # floors to Wh (D-08)
    def meter_for_customer(self, customer_id: UUID) -> "MeterRef | None": ...
    def customer_for_meter(self, meter_id: UUID) -> UUID | None: ...
class WalletBalanceProbe(Protocol):          # bound by slice 03; default returns 0
    def balance_minor(self, customer_id: UUID) -> int: ...

# meters/contracts.py
@dataclass(frozen=True, slots=True)
class MeterRef: id: UUID; serial_no: str; site_id: UUID; customer_id: UUID | None; status: str; version: int
class MeterRegistry(Protocol):
    def get(self, meter_id: UUID) -> MeterRef: ...
    def assert_usable_for_credit(self, meter_id: UUID, customer_id: UUID) -> None: ...  # raises MeterNotUsable(code="meter_decommissioned"|"meter_not_owned")
```

Serializer names: `CustomerSerializer`, `CustomerPseudonymousSerializer`, `CustomerCreateSerializer`, `CustomerUpdateSerializer`, `SiteSerializer`, `PlanSerializer`, `TariffCreateSerializer`, `MeterSerializer`, `MeterCreateSerializer`, `MeterUpdateSerializer`. Approval executor action `tariff.change` (`required_permission="config.manage"`, `required_attribute="finance_lead"`). Permission names used: `customer.read`, `customer.read_pii`, `meter.read`, `config.manage`, plus `customer:close` (Plan notes 2). URL names equal the operationIds. Settings: `HUB_DEFAULT_PHONE_PREFIX="+243"`, `HUB_IMPORT_MAX_ROWS=5000`, `HUB_IMPORT_MAX_BYTES=2097152`. Audit stream `registry`.

## Tasks

### Task 02.1: Unmanaged registry models and repositories

- [ ] Write failing tests. `tests/architecture/test_registry_models.py`: `test_registry_models_match_database` (customers, sites, plans, plan_tariffs, meters, meter_assignments); `tests/integration/customers/test_repositories.py`: `test_customer_insert_and_version_bump` (insert, update through repository, assert `version` went 1 to 2 by the database trigger, not by Python), `test_phone_unique_violation_maps_to_duplicate`.
- [ ] Run `cd backend && uv run pytest tests/architecture/test_registry_models.py tests/integration/customers/test_repositories.py -q`; expect ImportError.
- [ ] Implement models (columns exactly as `db/schema.sql`, `managed = False`, `Meta.db_table` unqualified) and repositories returning frozen DTOs through mappers. `CustomerRepository.add/get/update_with_version(customer, expected_version)` issues `UPDATE ... WHERE id = %s AND version = %s` and raises `VersionConflict` when zero rows change. Translate unique violations of `uq_customers_phone`, `uq_meters_serial`, `uq_sites_code`, `uq_plans_code` into domain errors.
- [ ] Run until green.
- [ ] Commit: `feat(customers): unmanaged registry models and repositories`

### Task 02.2: Sites and plans

- [ ] Write failing tests. `tests/integration/customers/test_sites_plans.py`: `test_operations_creates_site_and_reads_back` (TC-FR-01-1 part), `test_support_cannot_create_site_403`, `test_site_code_unique_409_duplicate_resource`, `test_finance_creates_plan_with_money_limits` (min greater than max gives 422), `test_plan_currency_mismatch_422`, `test_administrator_gets_403_on_sites` (administrator is absent from the `getSite` roles), `test_list_sites_cursor_pagination`. Site scope: `test_site_list_limited_to_user_sites` (support agent with `user_sites` containing only A sees only A).
- [ ] Run `uv run pytest tests/integration/customers/test_sites_plans.py -q`; expect 404 on `/api/v1/sites`.
- [ ] Implement the use cases and DRF views for `listSites`, `createSite`, `getSite`, `updateSite`, `listPlans`, `createPlan`, `getPlan`, `updatePlan`. Fields without a schema column in `Site` (`latitude`, `longitude`, `capacity_kw`, `health`, `meter_count`) are returned as `null` or computed (`meter_count` by a count query; `health` as `operational` when no alert data exists yet); not stored (Plan notes 3). `PlanSerializer` maps `min_topup_minor` and `max_topup_minor` to `Money` using the plan currency. `updatePlan` may change name and status (`draft` to `active` to `retired`), never currency or limits of an active plan (422 `plan_locked`). Audit each write. Site visibility for non-`all_sites` users filters by `user_sites`.
- [ ] Run until green.
- [ ] Commit: `feat(customers): sites and plans endpoints`

### Task 02.3: Customers with optimistic concurrency

- [ ] Write failing tests. `tests/integration/customers/test_customers_crud.py`: `test_create_read_back_with_status_and_etag` (TC-FR-01-1), `test_phone_normalisation_and_duplicate_409` (post `+243 81 234 5678` then `+243812345678` gives 409 `duplicate_resource`; `0812345678` normalises with `HUB_DEFAULT_PHONE_PREFIX`; `abc` gives 422), `test_stale_if_match_412_and_missing_428` and `test_stale_version_update_returns_conflict` (TC-FR-01-4: the matrix says 409 for a stale version; assert the contract's 412 `precondition_failed` and also that a database-level version race inside the repository maps to 409 `version_conflict`), `test_update_bumps_etag`, `test_close_customer_requires_zero_balance` (probe returns 5: 409 `customer_has_balance`; 0: 204 and status `closed`), `test_update_status_only_active_or_suspended`, `test_locale_defaults_to_fr_and_accepts_en`. Unit: `tests/unit/customers/test_phone.py::test_normalise_e164_cases`.
- [ ] Run `uv run pytest tests/unit/customers/test_phone.py tests/integration/customers/test_customers_crud.py -q`; expect 404 on `/customers`.
- [ ] Implement `normalise_e164` (strip spaces, dots, dashes; leading `00` becomes `+`; leading single `0` becomes `HUB_DEFAULT_PHONE_PREFIX`; must match `^\+[1-9][0-9]{7,14}$`), `shared/api/concurrency.py` (`etag_for(version) -> 'W/"v{n}"'`, `require_if_match` raising 428 or 412), and the use cases `RegisterCustomer`, `UpdateCustomer`, `CloseCustomer` (loads the balance through `WalletBalanceProbe`, default implementation returns 0 until slice 03). The use case runs inside `with uow:`, writes the audit event last, emits `customer.registered` and `customer.status_changed` through the outbox. `createCustomer` response is 201 with `ETag` and `Location`. Wire `CustomerLookup` for identity's customer login.
- [ ] Run until green.
- [ ] Commit: `feat(customers): customer registry with If-Match concurrency`

### Task 02.4: Plan tariffs through maker-checker

- [ ] Write failing tests. `tests/integration/customers/test_tariffs.py`: `test_propose_returns_202_and_no_current_tariff_change`, `test_second_finance_lead_approval_creates_active_tariff` (TC-FR-01-1 part; asserts `plan_tariffs.status='active'`, `approval_id` set, `current_tariff` function returns it after `effective_from`), `test_tariff_not_current_before_effective_from` (fake clock), `test_tariff_effective_from_in_past_422`, `test_change_over_25_percent_422_tariff_change_too_large`, `test_approver_without_finance_lead_403`, `test_administrator_cannot_approve_tariff` (holds `config.manage`, lacks `finance_lead`), `test_maker_cannot_approve_own_tariff`, `test_previous_tariff_becomes_superseded_at_activation`, `test_list_tariffs_history_ordered_desc`, `test_two_pending_proposals_same_effective_from_second_fails_409`.
- [ ] Run `uv run pytest tests/integration/customers/test_tariffs.py -q`; expect 404 on `/tariffs`.
- [ ] Implement `ProposeTariff` (validates `effective_from > now`, `price_per_kwh` currency equals plan currency and is positive, builds a payload `{plan_id, tariff_minor_per_kwh, daily_fixed_fee_minor, effective_from, reason}`, calls `ApprovalService.propose(action="tariff.change", ...)` and returns 202 with the approval) and `TariffChangeExecutor.execute` (inserts the `plan_tariffs` row with `proposed_by`, `approval_id`, status `active`, marks the earlier active row `superseded` when its `effective_from` is earlier; database trigger enforces the 25 percent cap and the executor maps that `check_violation` to 422 `tariff_change_too_large`, which rolls back the approval so it stays pending). `daily_fixed_fee` has no column (Plan notes 3): accepted, validated, and stored only inside the approval payload. Register the executor in `wiring.py`. `currentTariff` on `Plan` uses `hub.current_tariff(plan, now)`.
- [ ] Run until green.
- [ ] Commit: `feat(customers): versioned tariffs with maker-checker executor`

### Task 02.5: Site scope, masking and the pseudonymous view

- [ ] Write failing tests. `tests/integration/customers/test_scope_and_masking.py`: `test_other_site_customer_404_and_list_excludes` (support agent on site A: `GET` customer of B gives 404; `GET /customers?site_id=B` returns an empty list; audit row `outcome='denied'` exists; TC-FR-15-3 site part), `test_all_sites_user_sees_all`, `test_support_sees_masked_phone` (`+243*****42` pattern; full phone only when the principal holds `customer.read_pii`, and each full read is audited), `test_operations_gets_pseudonymous_fields_only` (keys equal `CustomerPseudonymous` exactly; no `full_name`, `phone_e164`, `email`), `test_operations_cannot_filter_by_phone` (422 or ignored with no match leak), `test_finance_sees_full_fields`, `test_administrator_403_on_customers`, `test_customer_reads_only_self` (customer principal: own id 200, other id 404). `tests/integration/customers/test_rls_customers.py`: `test_rls_foreign_site_rows_invisible_and_immutable` and `test_rls_missing_claim_zero_rows` for `hub.customers` (customers part of TC-FR-15-6).
- [ ] Run `uv run pytest tests/integration/customers/test_scope_and_masking.py tests/integration/customers/test_rls_customers.py -q`; expect failures on masking and scope.
- [ ] Implement `CustomerQueries` projections chosen by principal role (`CustomerSerializer` masked for support, full for finance, `CustomerPseudonymousSerializer` for operations; serializer class picked in the view from `request.principal`, never from a query parameter). Pseudonym is `customers.pseudonym` when set, otherwise `'c-' + hex(HMAC(HUB_PSEUDONYM_KEY, id))[:12]` computed in `domain/masking.py` (no write). Site scope is applied in the repository through `site_id = ANY(%s)` for non-`all_sites` principals, with RLS as a second layer; use `Authorizer.require_site` for single objects and translate its failure to 404. List filters: `q` (name or phone prefix, finance and support only), `status`, `site_id`, `plan_id`, `phone_e164`, `created_after`, `created_before`, keyset pagination on `id`.
- [ ] Run until green.
- [ ] Commit: `feat(customers): site scope, phone masking and pseudonymous view`

### Task 02.6: Meters registry and assignment

- [ ] Write failing tests. `tests/integration/meters/test_meters_registry.py`: `test_create_meter_and_read_back_status_provisioned` (TC-FR-01-1), `test_duplicate_serial_409_serial_conflict` (TC-FR-01-2 part), `test_concurrent_assign_exactly_one_wins` (two threads, each with its own connection, assign meter M to customers C1 and C2 using the same `If-Match`; assert one 200 and one 412 or 409, and `meter_assignments` has exactly one new row), `test_concurrent_duplicate_serial_exactly_one_wins` (TC-FR-01-2: two parallel creates, one 201 and one 409), `test_assign_requires_customer_on_same_site_422_site_mismatch`, `test_reassign_keeps_history_in_meter_assignments` (A then B: both pairs present), `test_unassign_with_null_customer`, `test_decommission_blocked_while_credit_outstanding_409` (probe port `MeterCreditProbe` returns 1 Wh), `test_decommissioned_meter_refused_for_credit` (TC-FR-01-3 registry side via `assert_usable_for_credit` raising `meter_decommissioned`), `test_status_transitions_only_allowed` (`decommissioned` is final: update gives 409 `invalid_state_transition`), `test_stale_if_match_412`, `test_ops_outside_site_404`, `test_customer_lists_only_own_meters`, `test_integrator_scope_meters_read`.
- [ ] Run `uv run pytest tests/integration/meters/test_meters_registry.py -q`; expect 404 on `/meters`.
- [ ] Implement `RegisterMeter`, `UpdateMeter`, `DecommissionMeter`, `AssignMeter`, `ListMeters`, `MeterRepository.lock_for_assign(meter_id)` (`SELECT ... FOR UPDATE` inside the unit of work, then compare `version` with `If-Match`; the second transaction waits, sees a newer version and fails 412). Assignment sets `meters.customer_id`; the schema trigger writes `meter_assignments`. Validate in one transaction that the customer exists, is `active`, and belongs to the meter's site; a customer may hold one active meter in v1 (`ix_meters_customer` read before write, 409 `duplicate_resource` otherwise; assumption recorded in Plan notes 5). Map API `serial_number`, `firmware_version` to `serial_no`, `firmware`; `hardware_model` has no column (Plan notes 3), stored in the audit detail only and returned from a constant `"unknown"`. Emit `meter.assigned` through the outbox. `MeterCreditProbe` default returns 0 until slice 06.
- [ ] Run until green.
- [ ] Commit: `feat(meters): meter registry and race-safe assignment`

### Task 02.7: Localised registry errors and bilingual strings

- [ ] Write failing tests. `tests/integration/customers/test_locale.py`: `test_problem_detail_in_french_for_french_principal` (duplicate phone as a `locale=fr` support agent: `detail` contains the French message from the catalogue and `code` unchanged), `test_accept_language_fallback_to_en_for_unknown_locale` (TC-FR-16-3 backend), `test_every_registry_error_code_has_en_and_fr_message` (iterate the code list `DuplicatePhone`, `serial_conflict`, `customer_has_balance`, `precondition_failed`, `precondition_required`, `tariff_change_too_large`, `meter_decommissioned`, `plan_locked`, `invalid_state_transition`; assert both catalogues have a non-empty, different string).
- [ ] Run `uv run pytest tests/integration/customers/test_locale.py -q`; expect missing catalogue keys.
- [ ] Implement message catalogues `src/hub/customers/locale/en.json` and `fr.json` (and the same for meters under `src/hub/meters/locale/`) loaded by the shared problem handler through the `message_key` of `DomainError`; follow the handler's existing lookup from slice 00 (add keys, do not change the handler's interface).
- [ ] Run until green.
- [ ] Commit: `feat(customers): English and French error messages for registry codes`

### Task 02.8: CSV import command

- [ ] Write failing tests. `tests/integration/customers/test_csv_import.py`: `test_import_all_or_nothing_and_idempotent_rerun` (fixture `customers_one_bad_row.csv`: exit code 2, zero rows inserted, report lists `row 3: duplicate_phone`; then fixed file imports all; importing it again inserts 0 and reports `skipped_existing=N` keyed by `external_ref`), `test_formula_cells_neutralised` (cell `=cmd()` stored as `'=cmd()` and flagged in the report), `test_row_and_size_caps` (`HUB_IMPORT_MAX_ROWS` plus one row fails before any insert), `test_unknown_site_code_row_error`, `test_meters_import_serial_conflict_reported`, `test_dry_run_changes_nothing`, `test_import_writes_one_audit_event_with_counts_only` (no names or phones in `details`), `test_import_refuses_non_utf8_and_bom_handled`. The command runs as role `hub_api` through `--settings hub.settings.api`.
- [ ] Run `uv run pytest tests/integration/customers/test_csv_import.py -q`; expect `CommandError: Unknown command: 'import_registry'`.
- [ ] Implement `manage.py import_registry --kind customers|meters --file PATH [--dry-run] --actor-email EMAIL`. The importer reads the whole file into memory (cap `HUB_IMPORT_MAX_BYTES`), validates every row with the same serializers as the API (`CustomerCreateSerializer` plus `external_ref`, `MeterCreateSerializer`), resolves `site_code` and `plan_code` to ids, collects all row errors, and only when there are none inserts all rows in one `with uow:`; the actor must be an existing active user holding the matching create permission (the command impersonates no one else). Customers CSV columns: `external_ref,full_name,phone,email,locale,site_code,plan_code`; meters: `serial_number,site_code,hardware_model,firmware_version,customer_external_ref`. Output a JSON report on stdout: `{inserted, skipped_existing, errors:[{row, code}]}` with codes only.
- [ ] Run until green.
- [ ] Commit: `feat(customers): all-or-nothing CSV import for customers and meters`

### Task 02.9: Registry authorization matrix, directory and demo seed

- [ ] Write failing tests. `tests/contract/test_registry_matrix.py`: `test_registry_operations_role_matrix` (every operationId tagged Customers or Meters, by every principal, per `permissions.yaml`; denied cells 403 or 401, allowed cells never 403), `test_x_site_scoped_operations_never_leak_existence` (404 not 403 for foreign-site ids). `tests/integration/customers/test_directory.py`: `test_quote_energy_floors_to_wh_and_uses_tariff_in_force` (tariff 1,000 minor per kWh, amount 1 minor gives 1 Wh: floor of 1,000 Wh times 1 over 1,000; assert `energy_wh` integer and remainder dropped), `test_meter_for_customer_and_customer_for_meter`, `test_current_plan`. `tests/contract/test_openapi_conformance.py` is extended by running it for tags Customers and Meters.
- [ ] Run `uv run pytest tests/contract/test_registry_matrix.py tests/integration/customers/test_directory.py -q`; expect missing `CustomerDirectoryImpl`.
- [ ] Implement `CustomerDirectoryImpl` and `MeterRegistry` adapters, register them in `wiring.py`, extend `db/seed/registry_demo.json` (two sites, two plans with an active tariff each, six customers, six meters, one with a conflict link) and `load_fixtures` to load it. Quote algorithm: `energy_wh = floor(amount_minor * 1000 / tariff_minor_per_kwh)` using integer arithmetic.
- [ ] Run until green, then the full slice suite.
- [ ] Commit: `feat(customers): directory contract, registry matrix test and demo seed`

## Verification

`make verify SLICE=02` runs, from the repository root:

1. `make lint` (pass: exit 0).
2. `make contracts` (pass: exit 0; alignment, schema and traceability tests green).
3. `cd backend && uv run pytest tests/unit/customers tests/integration/customers tests/integration/meters tests/architecture -q` (pass: 0 failed, 0 skipped).
4. `cd backend && uv run pytest tests/contract/test_registry_matrix.py -q` (pass: 0 failed).
5. `cd backend && uv run pytest tests/contract/test_openapi_conformance.py -q -k "Customers or Meters"` (pass: schemathesis checks green).
6. `cd backend && uv run python manage.py import_registry --kind customers --file tests/fixtures/registry/customers_ok.csv --dry-run --actor-email support.demo@soleilgrid.example --settings hub.settings.test` (pass: exit 0, JSON report with `errors: []`).
7. `cd backend && uv run pytest tests -k "slice01 or identity" -q` (pass: slice 01 still green).

## Demo script

1. `make up && make seed`; sign in as the fixture operations engineer (slice 01 flow). Observe `GET /api/v1/sites` returns the two demo sites.
2. `POST /sites` as operations; then as the support agent repeat: 403.
3. As finance, `POST /plans/{id}/tariffs` with `effective_from` tomorrow and a 10 percent rise: 202 with an approval; `GET /plans/{id}` still shows the old `current_tariff`.
4. Approve as the second finance lead after step-up: 200; `GET /plans/{id}/tariffs` lists the new active row; try +30 percent: 422 `tariff_change_too_large`.
5. As support, `POST /customers` with phone `0812345678` (observe the stored `+243812345678`), then again with `+243 81 234 5678`: 409 `duplicate_resource`.
6. `PATCH` that customer with the returned `ETag` as `If-Match`: 200 and a new `ETag`; repeat with the old one: 412; with none: 428.
7. As operations, `GET /customers`: only `id, pseudonym, site_id, plan_id, status, created_at`. As support: masked phone `+243*****xx`.
8. `POST /meters`, then `PATCH` assign to the customer; run two simultaneous assignments with curl in parallel and observe one success and one 412 or 409; `POST /meters` with the same serial: 409 `serial_conflict`.
9. Run the CSV import with `customers_one_bad_row.csv`: observe exit 2 and nothing inserted; run with `customers_ok.csv`: observe the inserted count, and a second run reporting `skipped_existing`.
10. Switch the support agent locale to `fr` (`PATCH /me`) and repeat step 5: observe a French `detail`.

## Done criteria

Requirements covered: FR-01 (registry: sites, plans, customers, meters, assignments, status), registry parts of FR-16 (customer locale, localised errors; screens come with the frontend plan), site-scope and object-level parts of FR-15, D-08 tariff versioning.

Test cases implemented and passing: TC-FR-01-1, TC-FR-01-2, TC-FR-01-4, TC-FR-01-3 (registry guard `assert_usable_for_credit` only; the UI e2e completes in slices 04 and 06), TC-FR-15-3 (site-scope part), TC-FR-15-6 (customers table part), TC-FR-16-3 (backend half, error language fallback).

## Plan notes for the tech lead

1. **CSV import has no HTTP operation.** The assignment and `docs/03#2.3` expect a CSV import; `api/openapi.yaml` has no import path, and the executor guide forbids unlisted endpoints. I implemented a management command (`import_registry`). If an HTTP upload is wanted, a change request must add the operation first.
2. **`customer:close` permission.** `closeCustomer` carries `x-permission: customer:close` in OpenAPI, but `hub.permissions` has no such code (the schema has `customer.read`, `customer.read_pii`). I follow `permissions.yaml` (role `support_agent` only) and do not check a named permission for close; the seed needs the code if the check is wanted. Also `customer.read_pii` is in no role's permission seed, so no role sees full phone numbers through support; finance sees full fields by role rule in `x-pseudonymised-for`.
3. **OpenAPI fields without columns.** `Site.latitude`, `longitude`, `capacity_kw`, `health`, `meter_count`; `Meter.hardware_model`; `TariffCreate.daily_fixed_fee`; `Customer.customer_number` (the schema has `external_ref`, `pseudonym`). `db/schema.sql` wins (D-01) and is frozen, so these fields are returned null, computed or constant; a schema migration is needed if they must persist. `customer_number` is returned as `external_ref`, else the first 8 hex characters of the id.
4. **Stale version status.** `docs/10` TC-FR-01-4 says a stale version returns 409; OpenAPI and `api/openapi.yaml#IfMatch` say 412 (and 428 when missing). I followed OpenAPI for the HTTP contract and use 409 `version_conflict` only for a repository-level race.
5. **One active meter per customer.** The schema allows several meters per customer (index is not unique); `docs/03` `meter_for_customer` returns a single meter. I enforce one active meter per customer in the use case as an assumption; relaxing it later needs no migration.
6. **Pseudonym.** `CustomerPseudonymous.pseudonym` is described as an HMAC of the customer id; the column `customers.pseudonym` is only filled at erasure. I compute the HMAC at read time with `HUB_PSEUDONYM_KEY` and use the column when set.
