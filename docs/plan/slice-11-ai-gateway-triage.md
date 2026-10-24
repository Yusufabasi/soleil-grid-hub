# Slice 11: AI gateway and triage (redaction, Claude client, audit, budgets, triage, summaries)

- **Goal:** one `ai_gateway` module that is the only egress to Claude: redaction, structured output, per-feature effort, stop-reason routing, pinned fallbacks, timeouts and breaker, an insert-only `ai_runs` audit on its own database alias, dollar budgets with separate pools, a fake client for every test. On top of it, the triage consumer (with the hazard floor in code and an auto-applied suggestion with override record) and ticket and conversation summaries.
- **Depends on:** slice 0 (settings, `shared`, `ObjectStore`, `Clock`, `IdGenerator`), slice 5 (consumers, `consumer_inbox`, retry and DLQ), slice 10 (tickets, messages, `SupportService.thread_for_ai`, outbox `ticket.created`), slice 1 (`Authorizer`, `audit_append`), slice 7 (`NotificationService`, optional for pages).
- **Requirements served:** FR-09, FR-11, NFR-07, NFR-08, NFR-13 (and NFR-09 metrics for AI, FR-16 for summary language).
- **Size:** L, 9 days.
- **Companion files:** none (the triage badge and summary panel live in [fe-10-inbox-kb.md](frontend/fe-10-inbox-kb.md) and the draft screen in slice 12; this slice ships no screen).
- **Spikes or open items:** (1) **Golden-set evaluation gates (docs/06 section 7.2):** triage category at least 90% overall and 85% per category, `needs_human_now` recall at least 98% with zero misses on fire and injury cases, language at least 98%, PII-flag recall at least 97%; summary fact recall at least 90% and unsupported-fact rate at most 2%. The harness is built here (Task 11.8) and run against the live model by the nightly job; **[open]** the live run needs the provider key and the approved-model list, so the release gate is evaluated by the tech lead, not by `make verify`. (2) **[open]** approved fallback models: default `["claude-opus-5"]`, off in production until the agreement list is confirmed (docs/06 section 8.4). (3) **[open]** prices: defaults are the docs/06 section 6 prices as settings, re-confirmed before go-live. (4) **[open]** on-call paging channel for staff (the `notifications` table is customer-only): default is a `OnCallPager` port with a logging adapter. (5) The ask-the-data latency spike of D-24 is in slice 13; this slice only guarantees the 3 s deadline plumbing.

## Read first

- `docs/06-ai-integration.md#1. Principles and hard invariants`
- `docs/06-ai-integration.md#2. The ai_gateway module` (2.1 to 2.10, all)
- `docs/06-ai-integration.md#3.1 FR-09 Triage` and `#3.3 FR-11 Summaries`
- `docs/06-ai-integration.md#4. Prompt architecture` (4.1 to 4.4)
- `docs/06-ai-integration.md#5.2 Per-feature choices`
- `docs/06-ai-integration.md#6.4 Budgets (NFR-13) and exhaustion behaviour`
- `docs/06-ai-integration.md#7.2 Metrics and pass thresholds` and `#7.4 CI regression gating`
- `docs/06-ai-integration.md#8.2 What is sent, what is retained` and `#8.3`
- `docs/06-ai-integration.md#10. API-drift pitfalls the implementation must avoid`
- `docs/03-code-architecture.md#2.9 ai_gateway`, `#5 Ports and adapters` (LLMClient), `#6.4 Ticket AI pipeline`
- `docs/10-test-strategy.md#4.1 Tier 1: deterministic tests with a fake LLM client`
- `docs/adr/0012-ai-gateway-single-egress.md`, `docs/adr/0016-single-default-model-measured-routing.md`, `docs/adr/0018-per-deployable-database-roles.md`
- `db/schema.sql` lines 1427 to 1480 (`ai_runs`) and 2460 to 2463 (`ai_gateway` grants); `db/invariant_tests.sql` lines 726 to 736
- `docs/00-shared-context.md` section 7: D-31 to D-34, D-37, D-41, D-50
- Reference files (facts only through docs/06): `/tmp/claude-1000/bundled-skills/2.1.308/5f4f2a360d39ee3b9d7552651d5fa35c/claude-api/python/claude-api/README.md` (sections on effort, refusal and fallbacks, timeouts) and `.../shared/model-migration.md` (Opus 5.5 and refusal fallback sections).
- OperationIds: `getTicketTriage`, `requestTicketTriage`, `overrideTicketTriage`, `requestSummary`, `listSummaries`, `getSummary`, `getAiUsage`, `listAiRuns`, `getAiRun`.

## Global constraints

- Model id read from configuration only (`ANTHROPIC_MODEL`, default `claude-opus-5-5`); no model literal in code; effort sent explicitly on every call: triage `low`, summary `low` (draft `medium` and ask-data `low` are configured here, used later).
- `thinking` is omitted; no `temperature`, `top_p`, `top_k`; no `tool_choice`; no tools; no assistant prefill; the last message is never an assistant message; structured output only through `output_config={"effort": ..., "format": {"type": "json_schema", "schema": ...}}`.
- Every schema object has `additionalProperties: false`; no `minimum`, `maximum`, `minLength`, `maxLength`, no recursion; bounds are enforced in validators.
- `stop_reason == "refusal"` is checked before any content is read; never retried on the same model; content discarded; status `refused`. `max_tokens` is never parsed; one retry at 2x up to the ceiling (triage 8,000, summary 8,000), then `truncated`. First text block is read by `type == "text"`, never `content[0]`.
- Fallbacks: array form, header exactly `server-side-fallback-2026-06-01`, 1 to 3 distinct entries, each carrying only `model`; synchronous calls only; empty on Batches and when `AI_RUN_MODE=eval`; the scalar `"default"` form and header `server-side-fallback-2026-07-01` are never used.
- `max_tokens`: triage 4,000; reply_draft 12,000; summary 4,000; ask_data 3,000. Timeouts via `anthropic.Timeout`: interactive `Timeout(15, connect=3)`, ask-data `Timeout(3.0, connect=1.0)`, background `Timeout(90, connect=5)`; `max_retries` 0 interactive, 2 background. No `httpx` import (the 1.x SDK sits on `httpx2`).
- Circuit breaker: opens on 5 consecutive counted failures or at least 50% failures over the last 20 calls (minimum 10); open 60 s; half-open one probe; doubles to a 5 minute cap; 429 does not count.
- The `ai_runs` row is written on the `ai_audit` database alias, connecting as role `ai_gateway`, which can only INSERT (plus the budget read columns); never as `hub_api` or `hub_worker` (D-50, invariant 1).
- Budgets are integer micro-dollars: per ticket $0.50; per customer per day $1.00 and 5 AI-eligible tickets; per API key per day $3.00; pools per day triage $15, reply_draft $25, summary $8, ask_data $6; global soft $35 and hard $60; month $1,100; regeneration 3 per ticket. Triage is stopped only by its own pool and the per-ticket, customer and key caps.
- Prompts are stored redacted only, in object storage keyed by hash (90 days); no prompt, response text, question or identifier is a log field or span attribute; `TicketForAI` has no field for name, phone, e-mail, account or meter serial.
- Only `ai_gateway.infrastructure.anthropic_client` imports `anthropic`; `pydantic` is imported only inside `ai_gateway`; callers receive frozen dataclasses (docs/03 section 3.3).
- No model-controlled write: the gateway returns values; `support` writes suggestions after validation.
- Triage vocabulary exactly: category `billing_payment, no_power, meter_fault, tariff_question, account_change, complaint, safety_hazard, other`; urgency `low, normal, high, critical`; language `fr, en, sw, ln, other`; PII flags `phone, email, national_id, payment_reference, address, health`. `ai_runs.status` values `started, ok, refused, truncated, invalid, timeout, error, budget_blocked, guard_blocked`.

## Review focus

1. **Hazard text with a calm model answer.** A French ticket "le câble est dénudé et il y a de la fumée" for which the fake model answers `urgency: low, category: other` must end `urgency >= high`, `needs_human_now` true, and a later model run must never lower it. Pinned by `test_hazard_floor_overrides_low_model_answer` (Task 11.6).
2. **Budget race.** 20 concurrent triage calls against a ticket or pool with room for 10 must produce exactly the allowed number of calls and no negative balance; blocked calls produce `budget_blocked` rows, not exceptions. Pinned by `test_reserve_is_atomic_under_concurrency` (Task 11.4).
3. **Phone numbers in odd shapes.** `+243 81 234 56 78`, `0812345678`, `00243-81-2345678`, with zero-width characters inside the digits, must not reach the request. Pinned by `test_no_phone_pattern_survives_redaction` (hypothesis, Task 11.2).
4. **Refusal and truncation fixtures.** A refusal with empty content and `stop_details: null`, a truncation mid-JSON, and an overload must each end in the specified status with no partial value and no loop. Pinned by `test_refusal_fixtures_end_in_specified_status` (Task 11.3).
5. **Audit survives the caller's rollback and cannot be edited.** The caller's transaction rolls back after the call: the `ai_runs` row exists; connecting as `ai_gateway` and running UPDATE or DELETE fails. Pinned by `test_audit_row_survives_caller_rollback` and `test_ai_gateway_role_is_insert_only` (Task 11.1 and 11.5).

## Files

Migration `db/migrations/NNNN_ai_triage.sql` (next unused number; record in `docs/plan/progress.md`):
- grants: `GRANT UPDATE (category, urgency, language, pii_flags, ai_category, ai_urgency, ai_language, ai_pii_flags, triage_ai_run_id, ai_status, needs_human_now, review_reason, ai_confidence, updated_at) ON hub.tickets TO hub_worker` (the schema gives the worker no write on `tickets`, and triage runs in the worker); `GRANT UPDATE (category, urgency, ai_status, review_reason, updated_at)` is not given to `hub_api` beyond what it has.
- columns on `tickets`: `ai_status text NOT NULL DEFAULT 'pending'` with CHECK in `('pending','applied','failed','skipped','refused','budget_exceeded')`, `needs_human_now boolean NOT NULL DEFAULT false`, `review_reason text` CHECK in `('low_confidence','injection_suspected','redaction_leak','hazard_floor')`, `ai_confidence numeric(4,3)`.
- table `hub.ai_pages (id uuid pk, ticket_id uuid not null, customer_id uuid, api_key_id uuid, status text check in ('sent','awaiting_confirmation','confirmed'), created_at timestamptz, confirmed_by uuid)` with grants to `hub_worker` and `hub_api`.

Backend (`backend/src/hub/ai_gateway/` unless stated):
- `contracts.py`: `AiRequest`, `AiResult`, `AiService`, `Redactor`, `RedactedText`, `UsageView`, frozen output dataclasses `TriageOutput`, `SummaryOutput`, and the Pydantic output models `TriageModel`, `SummaryModel`, `DraftModel`, `SqlProposalModel` (the latter two used in slices 12 and 13).
- `domain/redaction.py`, `domain/routing.py` (stop-reason router), `domain/budget.py` (scopes, reservation math), `domain/pricing.py` (cost from usage), `domain/errors.py`.
- `application/service.py` (`AiServiceImpl._execute`, the only pipeline), `application/config.py` (`AIConfig` and start-up validation), `application/prompts.py` (renderer and prompt id), `application/breaker.py`.
- `application/ports.py`: `LLMClient`, `AuditWriter`, `BudgetStore`, `PayloadStore`.
- `infrastructure/anthropic_client.py`, `fake_client.py` (`FakeLLMClient`), `models.py` (`AiRun`, unmanaged), `audit_writer.py` (alias `ai_audit`), `budget_redis.py` (Lua), `breaker_redis.py`, `payload_store.py` (object store, encrypted placeholder map).
- `prompts/triage/v1/{system.md,fewshots.jsonl,schema.json,meta.yaml}` and `prompts/summary/v1/...` (the four files per docs/06 section 4.4); `prompts/reply_draft/` and `prompts/ask_data/` are created in slices 12 and 13.
- `api/serializers.py`, `api/views.py`, `api/urls.py` for `getAiUsage`, `listAiRuns`, `getAiRun`.
- `backend/src/hub/support/domain/triage_rules.py` (hazard floor, monotonic urgency), `application/triage_pipeline.py`, `application/summaries.py`, `application/handlers.py` (`on_ticket_created`), `api/triage_views.py`, `api/summary_views.py`, `infrastructure/pager_log.py`.
- Settings: `backend/src/hub/settings/api.py` and `worker.py` (modify): `DATABASES["ai_audit"]` from `HUB_AI_AUDIT_DB_URL` (role `ai_gateway`), `ANTHROPIC_MODEL`, `AI_EFFORT`, `AI_MAX_TOKENS`, `AI_FALLBACK_MODELS`, `AI_RUN_MODE`, `AI_PROVIDER_ALLOWLIST`, budget settings, `AI_PRICES`, `AI_PLACEHOLDER_KEY`, `AI_ADAPTER=real|fake`.
- `backend/src/hub/bootstrap.py` (modify): wire `ai_gateway` and the pager; production refuses `AI_ADAPTER=fake`.
- `backend/src/hub/shared/management/commands/ai_probe.py` (start-up probe command), `run_ai_eval.py` (evaluation harness entry).
- Tests: `backend/tests/unit/ai_gateway/`, `backend/tests/integration/ai_gateway/`, `backend/tests/security/ai/`, `backend/tests/ai/eval/` (golden loader, thresholds) and `backend/tests/ai/fixtures/refusal_fixtures/*.json`.

## Interfaces

**Consumes:** `shared.ports.ObjectStore`, `Clock`, `IdGenerator`; `shared.uow.OutboxWriter`; `support.contracts.SupportService.thread_for_ai`, `TicketForAI`, `ThreadForAI`; `identity.contracts.Principal`; port `NotificationService` (optional); job framework `register_consumer(stream="events.support", group="support")`.

**Produces** (docs/03 section 2.9 is the public surface; docs/06 section 2.2 typed wrappers are internal helpers):

```python
@dataclass(frozen=True, slots=True)
class AiRequest:
    feature: Literal["triage", "reply_draft", "summary", "ask_data"]
    subject_ref: Reference                      # ticket id, customer id or question id
    prompt_version: str                          # e.g. "triage@v1"
    variables: Mapping[str, str]                 # already minimised; redacted by the gateway
    locale: Literal["en", "fr"]
    output_schema: type[BaseModel] | None        # only inside ai_gateway; callers pass a name via the registry
    actor: Principal | None
    sync: bool
    deadline: timedelta
    customer_id: UUID | None = None              # budget scope, never sent to the model
    api_key_id: UUID | None = None               # budget scope
    idempotency_key: str = ""                    # feature + subject + input_hash + prompt_id

@dataclass(frozen=True, slots=True)
class AiResult(Generic[T]):
    status: Literal["ok","refused","truncated","invalid","timeout","error","budget_blocked","guard_blocked"]
    parsed: T | None; text: str | None; run_id: UUID; served_model: str | None
    usage: UsageView; degraded_reason: str | None; blocked_scope: str | None

class AiService(Protocol):
    def run(self, req: AiRequest) -> AiResult: ...

class LLMClient(Protocol):
    def complete(self, req: LLMRequest) -> LLMResponse: ...   # raises only typed errors from ai_gateway.domain.errors

class BudgetStore(Protocol):
    def reserve(self, scopes: Sequence[BudgetScope], micro_usd: int, *, feature: str) -> Reservation | Blocked: ...
    def settle(self, r: Reservation, actual_micro_usd: int) -> None: ...
    def release(self, r: Reservation) -> None: ...
```

`TriageOutput` (frozen dataclass mirroring `triage.v1`): `category, urgency, language, pii_flags: tuple[str,...], needs_human_now: bool, confidence: float, injection_suspected: bool`. JSON schema `triage.v1` is exactly the schema in docs/06 section 3.1 (seven required properties, `additionalProperties: false`); `summary.v1` is exactly docs/06 section 3.3 (headline, status, key_points, open_questions, next_action, summary_language). Schemas live in `prompts/<feature>/v1/schema.json` and are the Pydantic source of truth.

Events: `ai.budget_exceeded {scope, feature, spent_micro_usd, limit_micro_usd}`; `ticket.triaged {ticket_id, category, urgency, language, pii_flags, confidence, ai_run_id}`; `ticket.triage_failed {ticket_id, reason_code, attempts}`; `ticket.triage_overridden {ticket_id, ai_run_id, overridden_by}` (consumed by a worker handler that sets `ai_runs.human_decision` and `reviewer_id`, because `hub_api` has no UPDATE on `ai_runs`); `ticket.page_requested {ticket_id, mode}`.

Serializers: `TriageResultSerializer`, `TriageOverrideCreateSerializer`, `SummaryRequestSerializer`, `SummarySerializer`, `AiUsageSerializer`, `AiRunSerializer`.

## Tasks

### Task 11.1: Migration, AiRun model, the `ai_audit` alias and the insert-only role

- [ ] 1. Failing tests: `backend/tests/integration/ai_gateway/test_ai_audit_role.py::test_ai_gateway_role_is_insert_only` (connect through alias `ai_audit`; INSERT of a valid row succeeds; `UPDATE hub.ai_runs SET output='{}'`, `DELETE`, `SELECT * FROM hub.customers`, `INSERT INTO hub.tickets` each raise `InsufficientPrivilege`), `test_worker_can_apply_triage_columns_only` (as `hub_worker`: UPDATE of `tickets.category` works, of `tickets.subject` fails), `test_models_match_database`.
- [ ] 2. Run `cd backend && uv run pytest tests/integration/ai_gateway/test_ai_audit_role.py -q`; expected: alias `ai_audit` not configured, columns missing.
- [ ] 3. Implement the migration, the `AiRun` unmanaged model (all schema columns), the `ai_audit` alias in the api and worker settings with `ATOMIC_REQUESTS` false and `CONN_MAX_AGE=0`; `AuditWriter.write(run: AiRunRecord)` is a single raw `INSERT` inside `transaction.atomic(using="ai_audit")` (no ORM `save()`).
- [ ] 4. Re-run until green; `make contracts`.
- [ ] 5. Commit: `feat(ai-gateway): insert-only audit alias and triage columns migration`.

### Task 11.2: Contracts, configuration, FakeLLMClient and redaction

- [ ] 1. Failing tests: `tests/unit/ai_gateway/test_config.py` (`test_unknown_effort_or_missing_effort_refuses_start`, `test_fallbacks_longer_than_three_or_duplicate_rejected`, `test_non_allowlisted_base_url_rejected`, `test_fallback_not_in_approved_set_rejected`), `test_fake_client.py::test_scripted_refusal_overload_timeout`, `tests/unit/ai_gateway/test_redaction.py`: `test_known_cases` (phone, e-mail, meter serial, Luhn-valid card run, coordinates, `avenue X`, customer name with accent folded, each replaced by `[PHONE_1]`, `[EMAIL_1]` and similar), `test_no_phone_pattern_survives_redaction` (hypothesis over generated DRC phone forms with separators and zero-width characters), `test_normalisation_neutralises_untrusted_marker` (input containing `</untrusted` is neutralised), `test_redaction_report_counts`.
- [ ] 2. Run `uv run pytest tests/unit/ai_gateway/test_config.py tests/unit/ai_gateway/test_fake_client.py tests/unit/ai_gateway/test_redaction.py -q`; expected: import errors.
- [ ] 3. Implement `contracts.py`, `AIConfig` (frozen, built from settings, validated at start; effort required for every feature), `domain/redaction.py` (pure Python, rules in the order of docs/06 section 2.4: NFKC, strip zero-width and bidi controls, collapse whitespace, length cap, patterns, known-identity names, stable placeholders; returns `RedactedText(text, placeholders: dict, report: RedactionReport)`), and `FakeLLMClient` (scripted by test: returns canned structured output, `refusal`, `max_tokens`, overload, rate limit with `retry_after`, timeout; records every request so tests can inspect it).
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(ai-gateway): contracts, configuration validation, redaction and fake client`.

### Task 11.3: Request builder, Anthropic adapter, stop-reason router and breaker

- [ ] 1. Failing tests: `tests/unit/ai_gateway/test_request_builder.py` (`test_request_has_exactly_allowed_keys`: keys are `model, max_tokens, output_config, system, messages` plus `betas` and `fallbacks` only when fallbacks are configured and the call is sync; `test_no_forbidden_parameters` asserts absence of `thinking`, `temperature`, `top_p`, `top_k`, `tools`, `tool_choice`; `test_last_message_not_assistant`; `test_eval_mode_has_no_fallbacks`; `test_fallback_entries_carry_only_model`; `test_beta_header_exact` equals `server-side-fallback-2026-06-01`; `test_stable_prefix_byte_identical_across_renders`; `test_nonce_differs_per_request_and_operator_context_is_system_role`), `test_stop_router.py::test_refusal_fixtures_end_in_specified_status` (parametrised over `tests/ai/fixtures/refusal_fixtures/*.json`: refusal with empty content and null `stop_details`, partial-content refusal, `max_tokens` mid-JSON, `pause_turn`, `end_turn` with a leading empty `thinking` block), `test_max_tokens_one_retry_at_double_then_truncated`, `test_first_text_block_not_content_zero`, `test_errors_map_to_status` (400 gives `invalid` and pages, 401/403/404 open the breaker, 429 honours `retry_after` and does not count, 5xx counts), `test_breaker_opens_after_5_and_half_opens` (clock-controlled, doubling to 5 minutes).
- [ ] 2. Run `uv run pytest tests/unit/ai_gateway/test_request_builder.py tests/unit/ai_gateway/test_stop_router.py -q`; expected: modules missing.
- [ ] 3. Implement `application/prompts.py` (renders system block 1 with `cache_control` on the last stable system block, wraps untrusted text in `<untrusted source="..." id="<nonce>">`, appends the operator context as a `role: "system"` message after the user message; with a one-time rebuild into the user block if a model rejects the system role), `build_request` exactly as in docs/06 section 2.5, `infrastructure/anthropic_client.py` (one `anthropic.Anthropic` per process; calls `client.beta.messages.create`; per-call `with_options(timeout=..., max_retries=...)`; maps SDK exceptions most specific first, no string matching; reads `usage` and `usage.iterations`; `served_model` from `response.model`; `fallback_ran` true when `usage.iterations` holds a `fallback_message` entry), `domain/routing.py` implementing the five rows of docs/06 section 2.7 in order, `application/breaker.py` with Redis state (per-process fallback if Redis is down: closed).
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(ai-gateway): request builder, anthropic adapter, stop routing and breaker`.

### Task 11.4: Budgets in dollars with separate pools

- [ ] 1. Failing tests: `tests/integration/ai_gateway/test_budgets.py`: `test_per_ticket_budget_blocks_call_and_none_is_made` (fake records zero calls; row `budget_blocked`, scope `ticket`), `test_per_day_pool_blocks_and_spend_consistent` (TC-NFR-13-2), `test_reserve_is_atomic_under_concurrency` (20 threads, pool for 10; exactly 10 pass), `test_customer_and_key_caps_block_but_triage_floor_runs` (customer and key exhausted: blocked; draft and ask_data pools exhausted: triage still runs, TC-NFR-13-4), `test_triage_not_stopped_by_global_hard_limit`, `test_global_hard_limit_blocks_other_pools`, `test_regeneration_cap_3_per_ticket`, `test_more_than_3_refusals_stops_ai_for_customer_24h`, `test_settle_replaces_reservation_with_actual_and_uses_iterations_when_fallback`, `test_redis_down_fails_closed_for_draft_triage_uses_fraction`, `test_rebuild_from_ai_runs_sums` (counters rebuilt from `ai_runs` using only the granted columns), `test_cost_from_usage_integer_math` (prices as micro-dollars per million tokens: input 4,000,000, output 20,000,000, cache write 5,000,000, cache read 200,000; no floats), `test_token_equivalent_display` (dollars divided by the $4 per MTok input price). Plus `tests/integration/ai_gateway/test_usage_api.py::test_ai_usage_matches_audit_within_1_percent` (TC-NFR-13-3 shape on the fake) and role tests (`operations_engineer` and `finance_officer` allowed, `support_agent` and `administrator` 403 on the three read operations).
- [ ] 2. Run `uv run pytest tests/integration/ai_gateway/test_budgets.py tests/integration/ai_gateway/test_usage_api.py -q`; expected: module missing.
- [ ] 3. Implement `domain/budget.py` (scope list order narrow to wide: per-call cap, ticket, customer, api key, feature pool, then global soft/hard and month for non-triage; reservation worst case = ceil(chars / 3) estimated input tokens at the input price plus `max_tokens` at the output price; keys include the UTC day) and `budget_redis.py` with one Lua script that checks every scope and increments all reservations atomically or reports the first blocking scope. Emit `ai.budget_exceeded` through the outbox once per scope per day (caller side) and alert counters. Limits are settings; changing them is out of scope (needs approval, later).
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(ai-gateway): dollar budgets with separate pools and triage floor`.

### Task 11.5: `AiServiceImpl` pipeline and audit

- [ ] 1. Failing tests in `tests/integration/ai_gateway/test_pipeline.py`: `test_pipeline_order` (budget, redact, build, breaker, send, route, validate, settle, audit; a spy records the order), `test_call_without_redaction_report_is_rejected` (I-3), `test_every_outcome_writes_exactly_one_ai_runs_row_per_attempt` (ok, refused, truncated with two rows, invalid, timeout, error, budget_blocked), `test_audit_row_survives_caller_rollback`, `test_audit_failure_fails_closed` (audit insert error: the call result is `error` and no value is returned, TC-NFR-08-1), `test_audit_row_fields` (`model`, `served_model`, `fallback_ran`, `prompt_template`=prompt id, `prompt_hash` 64 hex of the redacted prompt, `payload_ref`, token counts including cache columns, `cost_micro_usd`, `latency_ms`, `stop_reason`, `stop_category` within the CHECK set), `test_idempotent_replay_returns_existing_run_without_call`, `test_served_model_outside_list_raises_alert_counter`, `test_prompt_and_pii_absent_from_logs_and_spans`, `test_payload_object_keyed_by_hash_and_map_encrypted` (placeholder map object is ciphertext; decrypt with `AI_PLACEHOLDER_KEY` restores values), `test_system_prompt_canary_never_in_output` (fake obeys an injection that prints the canary: result `invalid`, TC-NFR-08-3), `test_egress_to_unapproved_host_blocked` (TC-NFR-08-2), `test_fallbacks_only_pinned_list_sync_only` (TC-NFR-08-4), `test_refusal_recorded_never_retried_never_customer_visible` (TC-NFR-08-5).
- [ ] 2. Run `uv run pytest tests/integration/ai_gateway/test_pipeline.py -q`; expected: `AiServiceImpl` missing.
- [ ] 3. Implement `application/service.py` as one private `_execute` used by `run`; status mapping `ok` only after schema validation (Pydantic) and the domain validators; `AiResult.parsed` is the frozen dataclass, never a Pydantic object. Audit insert and payload write happen after the outcome in `transaction.atomic(using="ai_audit")`. Because the role cannot UPDATE, **one terminal row is inserted per attempt** and no `started` row is written (see Plan notes); the budget reservation expires by TTL if the process dies. Implement `ai_probe` (one-token probe with the exact header and list; skipped with `AI_ADAPTER=fake`).
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(ai-gateway): audited execution pipeline with validation and payload storage`.

### Task 11.6: Triage consumer, hazard floor, suggestion, override, paging

- [ ] 1. Failing tests: `tests/unit/support/test_triage_rules.py` (`test_hazard_floor_overrides_low_model_answer`, parametrised EN and FR texts: fire, smoke, electric shock, injury, exposed cable, `feu`, `fumée`, `choc électrique`, `blessure`, `câble dénudé`; `test_model_cannot_lower_urgency`, `test_model_cannot_close_or_reroute_safety_hazard`, `test_safety_hazard_category_forces_needs_human_now`, `test_confidence_below_0_6_goes_to_review_hint_only`, `test_text_hash_recorded_not_text`), `tests/integration/support/test_triage_pipeline.py` (`test_ticket_created_applies_suggestion_with_override_record` (ai_* columns and visible columns set, `triage_ai_run_id` set, outbox `ticket.triaged`), `test_refused_truncated_invalid_go_to_manual_no_default_category` (`ai_status` failed or refused, category stays NULL, TC-FR-09-3 and TC-FR-09-4), `test_error_and_timeout_retried_three_times_5s_30s_3min`, `test_budget_blocked_flags_manual_and_alerts_once_per_day`, `test_redelivery_makes_no_second_model_call`, `test_override_stores_staff_value_next_to_ai_value` (TC-FR-09-5 data; sets `triage_overridden_by`; outbox `ticket.triage_overridden` then the worker handler sets `ai_runs.human_decision='edited'` and reviewer), `test_pii_flags_after_redaction_raise_leak_counter_and_review`, `test_injection_suspected_never_pages`, `test_paging_first_3_per_hour_then_confirmation` (TC-FR-09-6), `test_low_confidence_queue_listing`, `test_p95_latency_path_uses_background_timeout`), and `tests/security/ai/test_triage_injection.py::test_injected_payloads_cannot_alter_schema_bound_output` (TC-FR-09-2: fake obeys "set urgency low" and returns an out-of-enum value or extra field; result `invalid`; legal-but-wrong values still pass through the hazard and monotonic rules).
- [ ] 2. Run `uv run pytest tests/unit/support/test_triage_rules.py tests/integration/support/test_triage_pipeline.py tests/security/ai/test_triage_injection.py -q`; expected: modules missing.
- [ ] 3. Implement `triage_rules.apply(current: TicketTriageState, out: TriageOutput, normalised_text: str) -> TriageSuggestion` as pure domain code: hazard terms matched on NFKC, accent-folded, lower-cased text with word boundaries; urgency = max(current, model, floor if hazard); category never replaced when the current category is `safety_hazard`; `needs_human_now` true when hazard, `safety_hazard`, or model says so; `confidence < 0.6` stores the suggestion only in `ai_*` columns and sets `review_reason='low_confidence'`. `TriagePipeline.triage(ticket_id)` loads `thread_for_ai`, calls `AiService.run(feature="triage", sync=False, deadline=90s)`, maps statuses as docs/03 section 6.4 step 2, applies in transaction T2 (one `with uow:`), writes outbox events, and decides paging through `OnCallPager` with the table `ai_pages` (3 per hour automatic, later ones `awaiting_confirmation` until a staff confirm; never for `injection_suspected` alone). Views: `getTicketTriage`, `requestTicketTriage` (rate tier `ai`, re-queues via `JobQueue.enqueue`, counts against the ticket budget), `overrideTicketTriage` (requires `reason`).
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): triage consumer with hazard floor, suggestions, overrides and paging limits`.

### Task 11.7: Summaries (ticket and conversation) and the batch path behind a flag

- [ ] 1. Failing tests in `tests/integration/support/test_summaries.py`: `test_summary_in_ui_language_with_schema` (`summary_language` equals the requested locale), `test_stale_when_new_messages_arrive_and_no_auto_regeneration`, `test_summary_is_internal_only` (no code path attaches it to a message; `createTicketMessage` rejects its text origin), `test_timeout_or_overload_degrades_to_no_summary_ticket_unchanged` (TC-FR-11-2), `test_erased_customer_data_omitted` (TC-FR-11-3: thread of an erased customer is built from pseudonymised rows; the redacted request contains no erased name or phone), `test_conversation_summary_last_10_tickets_redacted`, `test_summary_pool_exhausted_returns_budget_blocked_with_reset_time`, `test_batch_refusal_item_audited_not_resent` (batch path off by default: flag `ai.summary_batch`; with the fake batch client a refusal item gets an audited row and no summary), plus role tests for `requestSummary`, `listSummaries`, `getSummary` (support and operations only).
- [ ] 2. Run `uv run pytest tests/integration/support/test_summaries.py -q`; expected: 404 routes.
- [ ] 3. Implement `summaries.py`: `source_hash = sha256(message ids and edit versions)`, stored in the run output; stale when the current hash differs; summary id equals the `ai_runs.id`. Sync path: `sync=True`, `Timeout(15, connect=3)`. Batch interface `submit_summary_batch` and `collect_summary_batch` implemented against a `BatchClient` port with a fake only (no fallbacks, per-item audit with the same pipeline, `ai.summary_batch` default false; the real adapter is `[open]`, default not built).
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): ticket and conversation summaries with staleness and degradation`.

### Task 11.8: Evaluation harness, gates, metrics and canary scripts

- [ ] 1. Failing tests: `tests/ai/eval/test_harness.py` (`test_eval_mode_refuses_to_start_with_fallbacks_on` (TC-NFR-08-4 part), `test_golden_loader_validates_labels_against_vocabulary`, `test_thresholds_computed_from_fixture_results` (category accuracy overall and per category, urgency within one level, language, PII recall and precision, `needs_human_now` recall with a hard zero-miss rule on fire and injury cases, Wilson 95% interval), `test_report_has_prompt_id_model_effort_served_model_usage_cost_per_case`, `test_french_and_mixed_subsets_reported_separately` (TC-FR-09-5)), `tests/ai/eval/test_golden_fixture_smoke.py::test_small_synthetic_set_runs_on_fake` (the 30-case synthetic fixture in `backend/tests/ai/fixtures/triage_gold_mini.jsonl`, EN and FR, with hazard, injection and planted-PII cases, run on the fake client), `tests/unit/ai_gateway/test_metrics.py::test_metrics_registered` (`soleil_ai_requests_total`, `soleil_ai_latency_seconds`, `soleil_ai_tokens_total`, `soleil_ai_cost_usd_total`, `soleil_ai_cache_hit_ratio`, `soleil_ai_refusals_total`, `soleil_ai_budget_blocked_total`, `soleil_ai_fallback_total`, `soleil_ai_redaction_leak_total`, labels as docs/06 section 9.2).
- [ ] 2. Run `uv run pytest tests/ai/eval tests/unit/ai_gateway/test_metrics.py -q`; expected: harness missing.
- [ ] 3. Implement `run_ai_eval` (loads `golden/*.jsonl`, sets `AI_RUN_MODE=eval`, runs each case three times, reports pass@1, pass@3 and pass^3 for safety cases, writes a JSON report and refuses to run when the estimated cost exceeds `--max-usd`), the metrics exporters, the nightly canary script `tools/scripts/ai_canary.py` (skips without a key: double call with the cache check `cache_read_input_tokens > 0` and at least 90% of the stable prefix; one fixture per stop reason) and the prompt-hash CI check (`test_prompt_files_unchanged_without_version_bump`).
- [ ] 4. Re-run until green; run `make lint`.
- [ ] 5. Commit: `feat(ai-gateway): evaluation harness, metrics and canary tooling`.

## Verification

`make verify SLICE=11` runs from the repository root:

1. `make lint`: clean (import-linter confirms `anthropic` and `pydantic` containment).
2. `make contracts`: green.
3. `cd backend && uv run pytest tests/unit/ai_gateway tests/unit/support/test_triage_rules.py -q`: all pass.
4. `cd backend && uv run pytest tests/integration/ai_gateway tests/integration/support/test_triage_pipeline.py tests/integration/support/test_summaries.py -q`: all pass.
5. `cd backend && uv run pytest tests/security/ai -q`: all pass.
6. `cd backend && uv run pytest tests/ai/eval -q`: harness and mini-set pass.
7. `cd backend && uv run pytest tests/contract/test_openapi_ai.py -q` (schemathesis for the nine operations): no violations.
8. `cd backend && uv run python manage.py ai_probe --settings=hub.settings.test`: prints `fake adapter: probe skipped` and exits 0.

## Demo script

1. `make up`, load fixtures with the fake adapter. Open a French ticket whose text says "câble dénudé, fumée". Observe within seconds: urgency `high` or above, `needs_human_now` true, `ai_runs` row `triage` with `served_model`, `fallback_ran=false`, `payload_ref`.
2. Script the fake to refuse: the next ticket shows `ai_status=refused`, category empty, manual queue, no retry in the logs.
3. Set the triage pool to a tiny value: next ticket `budget_exceeded`, drafts and ask pools unaffected.
4. As the `ai_gateway` role in psql run an UPDATE on `ai_runs`: permission denied.
5. Request a summary in English for a 7-message ticket; add a message; observe `stale`.

## Done criteria

Requirements covered: FR-09, FR-11, NFR-07, NFR-08, NFR-13. Test cases implemented and passing: TC-FR-09-1 (harness and mini-set here; the live-model gate is the nightly job), TC-FR-09-2, TC-FR-09-3, TC-FR-09-4, TC-FR-09-5, TC-FR-09-6, TC-FR-11-1 (harness; live gate nightly), TC-FR-11-2, TC-FR-11-3, TC-NFR-07-1, TC-NFR-07-2, TC-NFR-08-1, TC-NFR-08-2, TC-NFR-08-3, TC-NFR-08-4, TC-NFR-08-5, TC-NFR-13-1, TC-NFR-13-2, TC-NFR-13-3, TC-NFR-13-4.

## Plan notes for the tech lead

1. **Audit row lifecycle.** docs/06 section 1 and 2.10 say a `started` row is written before the call and updated to a terminal state. `db/schema.sql` and the invariant test give the `ai_gateway` role INSERT only (an UPDATE must fail). Followed the schema: one INSERT per attempt after the outcome; `started` stays a legal value for a future sweeper but is unused.
2. **Missing `ai_runs` columns.** docs/06 lists `effort`, `redaction_report`, `idempotency_key`, `input_hash`, `iterations`, `thinking_tokens`; the schema has `prompt_template`, `prompt_hash`, `output jsonb`. Followed the schema: `prompt_template` carries the prompt id, extras go in `output` under a `meta` key, the encrypted placeholder map is a separate object next to `payload_ref`.
3. **Worker cannot update tickets.** `hub_worker` has no write on `tickets`, yet docs/03 6.4 applies triage in the worker. Followed: column-level UPDATE grant by migration. Likewise `hub_api` cannot update `ai_runs`, so `human_decision` is set by a worker handler.
4. **Public AI interface.** docs/03 2.9 (`AiService.run(AiRequest)`, synchronous) conflicts with docs/06 2.2 (async typed functions). Followed docs/03 (higher precedence); typed wrappers are internal.
5. **Audit alias role.** docs/03 6.4 says the `ai_audit` alias uses the deployable's role; D-50 and the schema say `ai_gateway`. Followed D-50.
6. **Staff paging.** `notifications.customer_id` is NOT NULL, so staff on-call pages cannot use that table; a port with a logging adapter is planned and the real channel is `[open]`.
