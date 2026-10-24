# Slice 10: Support desk (tickets, inbound email, attachments, incidents, knowledge base)

- **Goal:** an agent can handle a ticket manually end to end: tickets arrive from the portal, the API and the inbound-email stub; messages, attachments (scanned, cookie-less download), assignment, incidents with linking of up to 100 tickets, and KB articles with versions published through maker-checker. Nothing in this slice calls a model.
- **Depends on:** slice 0 (skeleton, `shared` app, `DjangoUnitOfWork`, `OutboxWriter`, `ObjectStore` port, DRF helpers), slice 1 (`ApprovalService`, `ApprovalExecutorRegistry`, `Authorizer`, API-key auth with scopes, `audit_append`), slice 2 (`CustomerDirectory` contract), slice 5 (relay, consumers, `register_poller`), slice 7 (`NotificationService.request` for the acknowledgement SMS). Contract names are those of `docs/03-code-architecture.md#2.8 support`, `#2.14`.
- **Requirements served:** FR-08, FR-17, FR-16 (KB locale, acknowledgement language), NFR-06 (attachments, inbound trust).
- **Size:** L, 8 days.
- **Companion files:** [fe-10-inbox-kb.md](frontend/fe-10-inbox-kb.md) (agent inbox, ticket view, portal ticket form, incident banner, KB editor).
- **Spikes or open items:** (1) **[open]** unlinked inbound email needs a site (the schema makes `tickets.site_id` NOT NULL): default is the setting `SUPPORT_UNLINKED_SITE_ID`, which fixtures set to the seed site "unlinked". (2) **[open]** antivirus engine: default is a `Scanner` port with a ClamAV adapter behind `SUPPORT_SCANNER=clamav|fake`; production choice is operations'. (3) **[open]** attachments domain name: default setting `ATTACHMENTS_BASE_URL` (a different registrable host from the app, so no app cookie is ever sent). The latency spike of D-24 and the golden-set gates belong to slices 11 to 13 and are not needed here.

## Read first

- `docs/03-code-architecture.md#2.8 support (FR-08, FR-09 orchestration, FR-10, FR-11, FR-17)`
- `docs/03-code-architecture.md#2.14 Approvals (maker-checker) (D-15, D-16)` (executor pairing table)
- `docs/03-code-architecture.md#7.7 API layer conventions (Django REST framework)`
- `docs/04-data-model.md#3.2 Decisions worth knowing before reading the code` and the DDL of `tickets`, `incidents`, `ticket_messages`, `attachments`, `kb_*` in `db/schema.sql` (lines 1351 to 1625)
- `docs/01-prd.md` AC-FR-08-1 to AC-FR-08-5 and AC-FR-17-1 to AC-FR-17-4
- `docs/07-security-threat-model.md` T-19 (uploads), T-45 (inbound email), T-12 (KB poisoning)
- `docs/10-test-strategy.md#2.10 Controls added in revision 1` rows "Inbound email" and "KB publication"
- `docs/00-shared-context.md` section 7: D-15, D-30, D-36, D-50
- `docs/plan/00-executor-guide.md` sections 3 and 4
- OperationIds: `createTicket`, `listTickets`, `getTicket`, `updateTicket`, `listTicketMessages`, `createTicketMessage`, `ingestInboundEmail`, `uploadAttachment`, `listTicketAttachments`, `getAttachmentLink`, `listIncidents`, `createIncident`, `getIncident`, `updateIncident`, `linkIncidentTickets`, `listKbArticles`, `createKbArticle`, `getKbArticle`, `updateKbArticle`, `listKbVersions`, `getKbVersion`, `publishKbArticle`, `archiveKbArticle`.

## Global constraints

- Attachments: at most 3 files per message, each at most 5 MB (5,242,880 bytes), types `image/jpeg`, `image/png`, `application/pdf` decided by magic bytes, never by extension or the client header (D-30, T-19).
- Message and ticket body at most 5,000 characters (D-30; schema CHECK `ck_ticket_messages_length`).
- Downloads: 60-second link, only when `scan_status = 'clean'`, served from a cookie-less domain with `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`; attachment content is never sent to a model.
- An incident links at most 100 tickets (schema trigger `trg_tickets_incident_limit`; the API accepts at most 100 ids per call).
- `POST /tickets/inbound-email` needs API-key scope `tickets:inbound` and no other credential; a key holding only `tickets:write` gets 403 on it, and a `tickets:inbound` key gets 403 on `createTicket`.
- Inbound email: failed DMARC (or SPF with envelope mismatch) is quarantined; a reply is appended only when `from_address` equals the email of the ticket's customer; a repeated `message_id` returns `{"status":"duplicate"}` with no `ticket_id` and no content.
- KB publication needs a second approver; `published_by` and `approved_by` are recorded and differ from each other and from the version author (schema CHECK `ck_kb_versions_published`, D-36); published versions are immutable (trigger `trg_kb_versions_guard`); the forbidden-promise lint applies even to amounts present in an article (422 `draft_lint_failed`).
- Only the OpenAPI operations above exist; every handler has `@operation("<operationId>")`; roles come from `api/permissions.yaml`. No Django admin, `ATOMIC_REQUESTS` off, models `managed = False`.
- No other module's models are imported; `support` calls `identity`, `customers`, `notifications` only through `contracts.py`.
- The administrator sees no ticket content (D-14); ticket reads are site-scoped (`user_sites`) and portal reads are customer-scoped by RLS.
- Triage vocabulary and ticket status values are the schema's (D-01): status `open, in_progress, pending_customer, resolved, closed`.

## Review focus

1. **Duplicate and forged inbound email must not become a data oracle.** A replay of a `message_id` by a different key, a reply from a non-matching sender to someone else's ticket number, and a known versus unknown sender must give indistinguishable response bodies and status codes. Pinned by `test_inbound_email_responses_do_not_leak` (Task 10.4), owned by Task 10.4.
2. **Wrong file wearing the right extension.** An HTML file renamed `.png`, a PDF with a fourth sibling, a 5 MB + 1 byte file: the ticket text must still be saved with 422 `attachment_rejected`. Pinned by `test_upload_rejections_keep_ticket_text` (Task 10.3).
3. **Concurrent linking to an incident past 100.** Two agents linking 60 tickets each must end with exactly 100 linked and the rest reported, not a 500. Pinned by `test_link_incident_concurrent_cap` (Task 10.5).
4. **Self-publication and edit-after-proposal of a KB version.** The author proposing and approving, or editing the body after the approval was proposed, must not publish the edited text (hash binding). Pinned by `test_publish_requires_second_person_and_binds_hash` (Task 10.6).
5. **Download link for an unscanned or infected file.** `getAttachmentLink` on `pending`, `infected` or `failed` returns 409 `state_conflict`, never a URL. Pinned by `test_link_refused_until_clean` (Task 10.3).

## Files

Migration (the schema is frozen; this is the one allowed kind of addition, guide section 3). Use the next unused four-digit number at execution time and record it in `docs/plan/progress.md`:

- `db/migrations/NNNN_support_desk.sql`: (a) `tickets.ticket_number text` with a unique index and function `hub.next_ticket_number()` returning `SG-YYYY-nnnnnn` from a sequence (the schema has no ticket number; AC-FR-08-1); `tickets.customer_id` stays nullable for unlinked mail. (b) table `hub.inbound_emails (id uuid pk, api_key_id uuid not null, message_id_hash char(64) not null, disposition text check in ('created','appended','quarantined'), ticket_id uuid null, auth_summary jsonb, received_at timestamptz, unique (api_key_id, message_id_hash))`. (c) `kb_articles`: add `tags text[] not null default '{}'`, `translation_group_id uuid`, `archived_at timestamptz`; `kb_article_versions`: add `change_note text`, `publish_approval_id uuid references hub.approvals(id)`. (d) grants: `hub_api` INSERT, UPDATE on `inbound_emails`; `hub_worker` UPDATE (`scan_status`, `object_key`) on `attachments` (already granted) and SELECT on `inbound_emails`. (e) seed rows in `notification_templates` for `ticket_ack` (sms, en and fr) with param schema `{ticket_number}`.
- `db/migrations/NNNN_support_desk.down.md` is not created (forward only).

Backend, all under `backend/src/hub/support/` unless stated:

- `contracts.py`: `SupportService`, `TicketView`, `KbPassage`, `KbQueries` (extended in slice 12), `IncidentView`, `AttachmentView`, errors.
- `events.py`: `TicketCreated`, `MessageReceived`, `TicketResolved`, `KbPublished`, `KbArchived`.
- `domain/ticket.py`: ticket state machine and number format; `domain/inbound.py`: `decide_inbound(auth, sender_match, ref) -> InboundDecision`; `domain/attachment_rules.py`: count, size, magic-byte sniff; `domain/kb.py`: version rules and the publish lint; `domain/errors.py`.
- `application/open_ticket.py`, `post_message.py`, `update_ticket.py`, `upload_attachment.py`, `ingest_inbound_email.py`, `incidents.py`, `kb_articles.py`, `scan_attachments.py` (poller), `wiring.py` (registers the poller, the `kb.publish` executor, the `ticket.created` acknowledgement handler).
- `application/ports.py`: `Scanner`, `ObjectStore` use (from `shared.ports`), `Clock`, `IdGenerator`.
- `infrastructure/models.py` (unmanaged: `Ticket`, `Incident`, `TicketMessage`, `Attachment`, `KbArticle`, `KbArticleVersion`, `InboundEmail`), `repositories.py`, `queries.py`, `scanner_clamav.py`, `scanner_fake.py`.
- `api/serializers.py`, `views.py`, `urls.py`, `deps.py`.
- `backend/src/hub/settings/base.py` (modify): `SUPPORT_UNLINKED_SITE_ID`, `SUPPORT_SCANNER`, `ATTACHMENTS_BASE_URL`, `ATTACHMENT_LINK_TTL_S=60`.
- `backend/src/hub/bootstrap.py` (modify): wire `support`.
- `backend/src/hub/shared/management/commands/load_fixtures.py` (modify): support fixtures (customers with emails, one integrator key with `tickets:inbound`, one with `tickets:write`, KB seeds).
- Tests under `backend/tests/unit/support/`, `backend/tests/integration/support/`, `backend/tests/security/support/`.

## Interfaces

**Consumes** (earlier slices): `identity.contracts.ApprovalService.propose/approve`, `Principal`, `Authorizer`; `shared.contracts.ApprovalExecutor`, `ApprovalExecutorRegistry.register`; `shared.ports.Clock`, `IdGenerator`, `ObjectStore.put/presigned_get/delete`; `shared.uow.UnitOfWork`, `OutboxWriter.add`; `customers.contracts.CustomerDirectory.find_by_email(email) -> CustomerRef | None` and `.site_of(customer_id)`; `notifications.contracts.NotificationService.request(req, uow=) -> UUID` (idempotent on `dedupe_key`); `shared.api` helpers (`operation`, `idempotent`, `CursorEnvelopePagination`).

**Produces** (later slices rely on these):

```python
class SupportService(Protocol):                       # extends docs/03 2.8
    def open_ticket(self, cmd: OpenTicket, *, idempotency_key: str | None) -> TicketView: ...
    def post_message(self, ticket_id: UUID, body: str, *, actor: Principal) -> MessageView: ...   # human only
    def thread_for_ai(self, ticket_id: UUID, *, max_messages: int | None = None) -> ThreadForAI: ...   # used by slice 11
    def apply_triage_suggestion(self, ticket_id: UUID, s: TriageSuggestion, *, ai_run_id: UUID, uow: UnitOfWork) -> None: ...  # stub here, implemented in slice 11
    def search_kb(self, query: str, locale: str, limit: int) -> list[KbPassage]: ...              # implemented in slice 12
    def link_incident(self, incident_id: UUID, ticket_ids: Sequence[UUID], *, actor: Principal) -> LinkResult: ...

@dataclass(frozen=True, slots=True)
class OpenTicket:
    channel: Literal["portal", "api", "email"]; subject: str; body: str; customer_id: UUID | None
    meter_id: UUID | None; locale: Literal["en", "fr"]; created_by: Principal | None; api_key_id: UUID | None

@dataclass(frozen=True, slots=True)
class LinkResult:
    linked: tuple[UUID, ...]; skipped_out_of_scope: tuple[UUID, ...]; skipped_over_cap: tuple[UUID, ...]

class Scanner(Protocol):
    def scan(self, data: bytes) -> Literal["clean", "infected", "failed"]: ...
```

Events (payloads carry ids, never body text): `ticket.created {ticket_id, customer_id|null, channel, locale, created_at}`; `message.received {message_id, ticket_id, channel, received_at}`; `ticket.resolved {ticket_id, resolved_at}`; `kb.published {article_id, version_no, language, published_by, approved_by}`; `kb.archived {article_id}`.

Serializers (`support/api/serializers.py`): `TicketCreateSerializer`, `TicketSerializer`, `TicketUpdateSerializer`, `MessageCreateSerializer`, `MessageSerializer`, `InboundEmailSerializer`, `InboundEmailResultSerializer`, `AttachmentSerializer`, `AttachmentLinkSerializer`, `IncidentSerializer`, `IncidentCreateSerializer`, `IncidentUpdateSerializer`, `IncidentLinkSerializer`, `KbArticle*Serializer`, `KbVersionSerializer`, `KbPublishSerializer`.

## Tasks

### Task 10.1: Migration, unmanaged models, alignment

- [ ] 1. Write failing tests in `backend/tests/integration/support/test_schema_support.py`: `test_ticket_number_format` (insert a ticket, assert `ticket_number ~ '^SG-[0-9]{4}-[0-9]{6}$'` and unique), `test_inbound_emails_unique_per_key` (second insert same `(api_key_id, message_id_hash)` raises `UniqueViolation`; different key succeeds), `test_models_match_database` (reuse `check_db_alignment` for the `support` app).
- [ ] 2. Run `cd backend && uv run pytest tests/integration/support/test_schema_support.py -q`. Expected: fails, `column ticket_number does not exist` and unknown model `InboundEmail`.
- [ ] 3. Implement the migration file and its `RunSQL` Django migration in `hub/shared/migrations/` (helper `apply_sql_file`), then `infrastructure/models.py` (all `managed = False`, unqualified `db_table`). Number function: `'SG-' || to_char(now() at time zone 'UTC','YYYY') || '-' || lpad(nextval('hub.ticket_seq')::text, 6, '0')`, set as column default so `tickets` inserts need no application code.
- [ ] 4. Re-run the same command until it passes; run `make contracts` (alignment, schema, traceability must stay green).
- [ ] 5. Commit: `feat(support): add ticket number, inbound email and KB columns migration`.

### Task 10.2: Tickets and messages (portal, API, staff)

- [ ] 1. Failing tests `backend/tests/integration/support/test_tickets_api.py`: `test_customer_creates_ticket_with_derived_customer` (portal token for customer A posts `customer_id` of B in the body; stored `customer_id` is A, status `open`, `urgency` `normal`, `language` equal to the request locale (so triage can only raise urgency, slice 11), `ticket_number` present, one outbox row `ticket.created` without body text), `test_integrator_ticket_limited_to_key` (key X lists tickets: sees only those with its `api_key_id`; fetching another key's ticket is 404), `test_body_over_5000_rejected` (5,001 chars gives 422 `validation_error`, nothing stored), `test_staff_site_scope` (agent without the ticket's site: 404 on `getTicket`), `test_update_requires_version` (PATCH with stale `version` gives 409 `version_conflict`), `test_ai_draft_cannot_be_created_by_post_message` (body of `createTicketMessage` always stored as `author_type='agent'` or `'customer'`; a client-supplied `author_type` is ignored), `test_administrator_denied` (403).
- [ ] 2. Run `uv run pytest tests/integration/support/test_tickets_api.py -q`; expected: 404 on every route.
- [ ] 3. Implement `OpenTicket`, `PostMessage`, `UpdateTicket` use cases and the views. Approach: `open_ticket` opens `with uow:` once, inserts the ticket and first message (`direction='inbound'`, `status='received'`), writes outbox `ticket.created`, and for portal tickets calls `NotificationService.request` with template `ticket_ack` and dedupe key `ticket_ack:<ticket_id>` (AC-FR-08-1; failure to request is a log, never a rollback). Site for a customer ticket comes from `CustomerDirectory.site_of`; for an unlinked ticket from `SUPPORT_UNLINKED_SITE_ID`. Resolving a ticket sets `resolved_at` and outbox `ticket.resolved`. Agent messages: `direction='outbound'`, `author_type='agent'`, stored with `status='sent'` in the same transaction as a `NotificationService.request` call with template `ticket_reply` (param `message_id`; the sender renders the stored body). Internal notes use `direction='internal'` and request nothing [open: template text by product; default one line "You have a reply on ticket {ticket_number}"].
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): open tickets and post messages from portal, API and staff`.

### Task 10.3: Attachments, scanning and download links

- [ ] 1. Failing tests in `backend/tests/integration/support/test_attachments.py`: `test_upload_accepts_png_pdf_jpg_by_magic` (real minimal files, 202, `scan_status=='pending'`, object key random, not derived from filename), `test_upload_rejections_keep_ticket_text` (parametrised: HTML renamed `.png`, 5,242,881 bytes, fourth file on the same message, `.exe`; each returns 422 `attachment_rejected` and the earlier ticket text is still readable), `test_exactly_5mb_accepted`, `test_filename_traversal_neutralised` (`../../x.png` stored with sanitised display name), `test_scan_job_marks_clean_and_infected` (FakeScanner: EICAR bytes give `infected`), `test_link_refused_until_clean` (pending, infected and failed give 409 `state_conflict`; clean gives a URL whose host equals the `ATTACHMENTS_BASE_URL` host and whose expiry is at most 60 s), `test_customer_cannot_get_other_customers_link` (404).
- [ ] 2. Run `uv run pytest tests/integration/support/test_attachments.py -q`; expected: 404 routes.
- [ ] 3. Implement `domain/attachment_rules.py` (`sniff(data) -> ContentType | None` using signatures `FFD8FF`, `89504E470D0A1A0A`, `%PDF-`), `upload_attachment.py` (count rule via the schema trigger, mapped from `check_violation` to 422; images are stored as received in v1 but the scanner adapter contract states metadata stripping for images is done by `scanner_clamav` post-step [open: re-encode, default off]), the `scan_attachments` poller (`register_poller`, `SKIP LOCKED` claim of `scan_status='pending'`, reads the object, sets the verdict; `failed` after three attempts), and `getAttachmentLink` calling `ObjectStore.presigned_get(key, 60)` then rewriting the host to `ATTACHMENTS_BASE_URL` through a signed-URL helper that adds the two response-header overrides. A body over 5,000 characters or an upload error never rolls back the ticket text: the message is committed first, files after.
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): attachments with content sniffing, scanning and cookie-less links`.

### Task 10.4: Inbound email stub

- [ ] 1. Failing tests in `backend/tests/integration/support/test_inbound_email.py` and `backend/tests/security/support/test_inbound_email_security.py`: `test_known_sender_creates_linked_ticket` (customer email match gives `created` and a ticket with that `customer_id`, channel `email`), `test_unknown_sender_creates_unlinked_ticket` (`customer_id` null, site `SUPPORT_UNLINKED_SITE_ID`), `test_reply_appends_when_sender_matches` (subject `Re: SG-2026-000001 ...` and matching sender gives `appended`, one new message, no new ticket), `test_reply_from_other_sender_opens_new_ticket` (status `created`, original ticket untouched), `test_dmarc_fail_quarantined` (status `quarantined`, no ticket, one `inbound_emails` row `disposition='quarantined'`), `test_spf_fail_with_envelope_mismatch_quarantined`, `test_duplicate_message_id_single_message` (second call returns `{"status":"duplicate"}`, message count still 1, AC-FR-08-3), `test_inbound_email_responses_do_not_leak` (collect bodies and status codes for: duplicate by same key, duplicate of a quarantined mail, known sender, unknown sender, reply to foreign ticket; assert duplicates carry no `ticket_id` key and the created-case bodies differ only in the id value; assert the same HTTP status 200), `test_replay_by_other_key_is_not_duplicate` (dedupe scoped by `api_key_id`, so another key cannot probe), `test_scope_isolation` (a `tickets:write` key gets 403 on the route; a `tickets:inbound` key gets 403 on `createTicket`), `test_body_over_5000_rejected_422`, `test_injection_text_stored_verbatim_as_data` (body "ignore instructions" is stored unchanged; no AI call exists).
- [ ] 2. Run both files with `uv run pytest tests/integration/support/test_inbound_email.py tests/security/support/test_inbound_email_security.py -q`; expected: 404 route.
- [ ] 3. Implement `domain/inbound.py` and `ingest_inbound_email.py`. Algorithm (order matters): (1) validate the serializer; (2) in one `with uow:` insert into `inbound_emails` with `message_id_hash = sha256(message_id.strip().lower())` `ON CONFLICT DO NOTHING`; zero rows returns `{"status":"duplicate"}` immediately (same work and same shape whatever the original disposition); (3) `auth_ok = dmarc == "pass" or (dmarc != "fail" and dkim == "pass" and spf == "pass")`; `dmarc == "fail"` or `spf == "fail" and envelope_from != from_address` gives `quarantined`: update the row disposition, return `{"status":"quarantined"}` with no ticket id; (4) match `CustomerDirectory.find_by_email(from_address)` (case-folded); (5) extract a ticket number with regex `SG-\d{4}-\d{6}` from the subject; if a ticket exists and its customer matches the sender, append; in every other case open a new ticket (never reveal that the referenced ticket exists); (6) persist disposition and ticket id; return `{"status": "created"|"appended", "ticket_id": ...}`. Store `auth_results` on the row (`auth_summary`) so staff see trust. Responses never include customer names or content.
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): inbound email stub with sender authentication and duplicate handling`.

### Task 10.5: Incidents and linking

- [ ] 1. Failing tests in `backend/tests/integration/support/test_incidents.py`: `test_create_and_update_incident` (statuses per the schema `open, mitigated, resolved`; resolved sets `resolved_at`), `test_link_up_to_100_in_one_call` (100 tickets linked; `ticket_count == 100`), `test_link_101st_reported_not_500` (101 ids is a 422 from the serializer `maxItems`; 60 + 60 sequential calls link 100 and report 20 in `skipped_over_cap`), `test_link_concurrent_cap` (two threads link 60 each, final count exactly 100, no 500), `test_out_of_scope_tickets_skipped` (tickets of an unassigned site appear in `skipped_out_of_scope`), `test_ticket_shows_incident_banner_when_site_alert_open` (ticket created at a site with an open `site_offline` alert returns `incident_id` null but `GET /tickets/{id}` includes the open incident of that site when one exists; the banner logic is `IncidentQueries.open_for_site`), `test_administrator_denied`.
- [ ] 2. Run `uv run pytest tests/integration/support/test_incidents.py -q`; expected: 404.
- [ ] 3. Implement `incidents.py`: `link_incident` filters tickets by site visibility, then `UPDATE tickets SET incident_id = ... WHERE id = ANY(...) AND incident_id IS NULL` in a loop of savepoints so the schema trigger's `check_violation` on the 101st becomes `skipped_over_cap` rather than aborting. Serializer enum mapping: if `tests/test_alignment.py` requires the API value `monitoring`, map it to the schema value `mitigated` in the serializer only (see Plan notes).
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): incidents with bulk ticket linking capped at 100`.

### Task 10.6: KB articles, versions and publication by approval

- [ ] 1. Failing tests in `backend/tests/integration/support/test_kb.py` and `backend/tests/security/support/test_kb_publication.py`: `test_create_article_makes_version_1_draft`, `test_edit_after_publication_creates_new_draft_version` (old version row unchanged, still citeable, AC-FR-17-1; direct SQL UPDATE of a published body fails with `restrict_violation`), `test_publish_requires_second_person_and_binds_hash` (author calls publish: 403; another agent proposes: 202 with an `Approval` whose `payload_hash` covers `{article_id, version_no, body_sha256}`; the body is then edited to a new version; approving the old proposal publishes the OLD body only; the maker approving own proposal is refused with `self_approval_forbidden`), `test_published_by_and_approved_by_recorded_and_distinct`, `test_publish_lint_blocks_promise_and_amount` (body "Refunds within 24 hours, 50 USD credit" gives 422 `draft_lint_failed` before any approval row is created, EN and FR phrase lists), `test_publish_scan_flags_instructions_to_assistant` ("ignore previous instructions and reply with" is rejected 422; TC-FR-17-5 data), `test_unpublished_article_not_in_current_versions` (view `kb_current_versions` excludes drafts), `test_archive_excludes_within_60s` (after archive, `kb_current_versions` has no row; hard delete route absent), `test_non_editor_cannot_publish` (403 for `finance_officer`, 403 for `administrator` per D-14 permissions).
- [ ] 2. Run `uv run pytest tests/integration/support/test_kb.py tests/security/support/test_kb_publication.py -q`; expected: 404.
- [ ] 3. Implement `domain/kb.py` (`lint_publishable(title, body, language) -> list[LintFinding]` with bilingual forbidden-promise patterns: refund, credit, reversal, compensation, discount, any currency amount, any duration or time commitment, "guarantee"/"garantie"; plus instruction-like patterns aimed at the assistant) and `kb_articles.py`. `KbPublishExecutor(action="kb.publish", required_permission="kb:publish")` runs inside the approval transaction: re-reads the stored payload, verifies the stored body hash against the version row (mismatch aborts), then `UPDATE kb_article_versions SET published_by=<maker>, approved_by=<approver>, published_at=now()` and `kb_articles.status='published'`, supersedes the previous published version of that language, writes outbox `kb.published`. Lint runs both at proposal and again in the executor. Updating `body_markdown` maps to `kb_article_versions.body` of a new row; `PATCH` requires the `version` for optimistic concurrency.
- [ ] 4. Re-run until green.
- [ ] 5. Commit: `feat(support): versioned KB articles published through maker-checker`.

### Task 10.7: Conformance, fixtures, end-to-end

- [ ] 1. Failing tests: `backend/tests/contract/test_openapi_support.py::test_schemathesis_support_operations` (schemathesis restricted to the 23 operationIds listed above, checks per docs/03 7.7 item 10) and `backend/tests/e2e/test_support_flow.py::test_agent_handles_ticket_manually` (portal ticket, agent assign and reply, resolve, with the outbox drained by the relay).
- [ ] 2. Run `uv run pytest tests/contract/test_openapi_support.py tests/e2e/test_support_flow.py -q`; expected: fixtures missing and schema mismatches listed.
- [ ] 3. Fix schema mismatches in serializers (never in `api/openapi.yaml`); extend `load_fixtures` as in Files. Add `tests/architecture` assertions: no `anthropic` import in `hub.support`; `SupportService` has no method that accepts `author_type`.
- [ ] 4. Re-run until green; run `make lint` and `make contracts`.
- [ ] 5. Commit: `test(support): conformance and manual ticket flow for slice 10`.

## Verification

`make verify SLICE=10` runs, each from the repository root:

1. `make lint`: clean.
2. `make contracts`: alignment, schema-in-postgres, traceability green.
3. `cd backend && uv run pytest tests/unit/support tests/integration/support tests/security/support -q`: all pass, zero skipped.
4. `cd backend && uv run pytest tests/contract/test_openapi_support.py -q`: no schema or status-code violations.
5. `cd backend && uv run pytest tests/architecture -q`: import-linter and model alignment pass.
6. `cd backend && uv run pytest tests/e2e/test_support_flow.py -q`: pass.

## Demo script

1. `make up && manage.py load_fixtures`. Observe the stack healthy and the seed customers.
2. As customer A (portal token) `POST /api/v1/tickets` with a body in French. Observe 201, `ticket_number` `SG-2026-…`, an acknowledgement SMS in the stub outbox in French.
3. Upload a PNG (accepted, `pending` then `clean` within one scanner tick) and an HTML file renamed `.png` (422 `attachment_rejected`, text kept).
4. `GET /attachments/{id}/link`: a URL on the attachments host, expiring within 60 s; before the scan finishes, 409.
5. With the `tickets:inbound` key `POST /tickets/inbound-email` twice with the same `message_id`: first `created`, second `{"status":"duplicate"}` with no `ticket_id`. A DMARC-fail mail returns `quarantined`.
6. As an agent assign and resolve the ticket; create an incident and link 100 seeded tickets in one call; observe the banner field on a new ticket of that site.
7. Create a KB article, publish as the author (403), then propose as agent 2 and approve as agent 3 (service-level approve if the identity route excludes support agents, see Plan notes). Observe `published_by` and `approved_by` distinct and an old version still readable after an edit.

## Done criteria

Requirements covered: FR-08, FR-17, FR-16 (KB language and acknowledgement language). Test cases implemented and passing: TC-FR-08-1 (Task 10.2 and 10.4), TC-FR-08-2 (10.3 and 10.4), TC-FR-08-3 (10.7 e2e), TC-FR-08-4 (10.4), TC-FR-08-5 (10.2, 10.3, 10.4 size and body checks), TC-FR-17-1 (10.6), TC-FR-17-3 (10.6), TC-FR-17-4 (10.6), TC-FR-17-5 (10.6). TC-FR-17-2 (injected instructions cannot alter draft policy) is finished in slice 12 (the publish scan is delivered here).

## Plan notes for the tech lead

1. **Ticket number, inbound dedupe table and KB columns missing from `db/schema.sql`.** AC-FR-08-1 needs `SG-YYYY-nnnnnn`; the schema has no column; the OpenAPI `Ticket` schema requires `ticket_number`. Followed: numbered migration (allowed by the executor guide) rather than editing the frozen schema. Same for `inbound_emails`, KB tags and translation group.
2. **Unlinked tickets need a site.** `tickets.site_id` is NOT NULL while AC-FR-08-2 has an "unlinked" queue. Followed: a configured site (default fixture "unlinked") and `customer_id` NULL.
3. **Incident status.** Schema `open, mitigated, resolved`; OpenAPI `open, monitoring, resolved`; `site_id` NOT NULL in the schema but optional in `IncidentCreate`. Followed the schema (D-01) with a serializer mapping and a required site (taken from the caller when absent); the alignment test result decides whether the mapping is needed.
4. **Ticket status.** PRD says `new`; schema and OpenAPI say `open`. Followed the schema.
5. **Duplicate response.** T-45 says 202; the OpenAPI operation returns 200 with `{status: duplicate}`. Followed the OpenAPI.
6. **KB approval deadlock risk.** `ck_approvals_action` allows `kb.publish`, the executor needs permission `kb:publish` which only `support_agent` holds, but `api/permissions.yaml` lists `approveApproval` for finance, operations and administrator only, and `administrator` holds no `kb:*` permission although the OpenAPI KB operations list it. Followed: executor and service are implemented as specified; the demo approves at service level; a change request is advised (add `support_agent` to `approveApproval` for `kb.publish`, or remove `administrator` from the KB operations).
7. **Notifications for human replies.** `notification_templates.param_schema` forbids free text (A-18), so replies use a template with a `message_id` parameter that the sender renders from the stored message; the slice 7 author must provide or accept the `ticket_reply` template. Default text is `[open]`.
