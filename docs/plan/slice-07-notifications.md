# Slice 07: Notifications

- **Goal:** the `notifications` module: notification requests recorded with the business change, French and English templates with parameter schemas, per-agent and per-customer caps, stub SMS and email adapters, retry with dead-letter, event mapping for receipts and token failure, dedupe, and the outbox rule that a customer-facing AI-derived message needs an approval reference.
- **Depends on:** slice 05 (consumers, pollers, `consumer_inbox`), slice 04 (`payment.*` events), slice 06 (`token.*` events), slice 02 (`CustomerDirectory`), slice 01 (`Principal`, `AuditLog`), slice 00 (`UnitOfWork`, outbox writer).
- **Requirements served:** FR-19 (FR-16 for the EN/FR templates).
- **Size:** M, 6 days for a cheaper agent.
- **Companion files:** none (staff and portal screens for notifications belong to later frontend slices).
- **Spikes or open items:** **[open]** legal wording of the templates (default: the texts in Task 07.1). **[open]** per-agent daily cap, default 100 staff-created notifications per agent per UTC day (`NOTIFICATION_AGENT_DAILY_CAP`), because no document gives a number. **[open]** quiet hours: stored and returned by the preferences endpoints, not enforced in v1. One migration is instructed (Task 07.1): the frozen schema lacks `dedupe_key`, attempts and locale on `notifications` and any preference storage (Plan notes 1 and 4).

## Read first

- `docs/00-shared-context.md#3. Architecture decisions made up front` (invariant 2) and `#7.6 AI` (D-35)
- `docs/03-code-architecture.md#2.11 notifications (FR-19)`, `#4. Domain event catalogue` (notification and token rows), `#5. Ports and adapters` (`Notifier`)
- `docs/01-prd.md#FR-19 Notifications through a reliable outbox` (AC-FR-19-1 to 19-4)
- `docs/04-data-model.md` tables `hub.notification_templates`, `hub.notifications`, `hub.outbox` and its `ck_outbox_ai_approval` constraint
- `docs/07-security-threat-model.md` row T-54 (notification channel abuse)
- `docs/10-test-strategy.md#2.3 Outbox and relay` (O7) and `#2.10` (Notifications row)
- `docs/plan/slice-05-outbox-relay-worker.md` Interfaces; `docs/plan/slice-06-token-issuance.md` Interfaces (event payloads)
- Operations in `api/openapi.yaml`: `listNotifications`, `getNotification`, `createNotification`, `retryNotification`, `listNotificationTemplates`, `getNotificationPreferences`, `replaceNotificationPreferences`

## Global constraints

- Templates have a parameter schema; free text is impossible. A request with an unknown, missing, oversized or markup-bearing parameter is 422 `validation_error`.
- Languages are `fr` and `en`; channels are `sms` and `email`; French is written first and is the default when a customer's language is missing.
- Contact details are fetched at send time through `CustomerDirectory.contact`; they are never copied into events, parameters or rows (only a masked hint is stored). Erased or contactless customers get status `cancelled`.
- Dedupe key is event id, channel and customer (`{event_id}:{template_code}:{channel}:{customer_id}`), unique in PostgreSQL; a duplicate request is a no-op returning the existing id.
- The notification row and its `notification.requested` outbox row are written in the same transaction as the business change; a rollback leaves neither.
- A customer-facing, AI-derived request without `approval_ref` is rejected (AC-FR-19-4, invariant 2); the database constraint `ck_outbox_ai_approval` is the last line of defence.
- Send timeout 5 s; retries 1 minute doubling, at most 8 attempts (1, 2, 4, 8, 16, 32, 64, 128 minutes, about 4 hours); then status `failed` (shown as dead-lettered) and `notification.failed`.
- Staff create notifications only from templates flagged `staff_sendable`; caps: `NOTIFICATION_AGENT_DAILY_CAP=100` per agent per day, `NOTIFICATION_CUSTOMER_STAFF_DAILY_CAP=5` per customer per day from staff, `NOTIFICATION_TRIGGER_HOURLY_CAP=3` per customer and template per hour from system events (excess is dropped and counted).
- Never log message bodies, phone numbers or emails; log notification id, template code, channel, content hash and masked recipient only.
- Notification handlers do not decide business rules beyond the event map; the map lives in `event_map.py` only.
- Handlers raise `Retryable` for provider errors and `Permanent` for invalid state; the stub adapters never contact a real provider.

## Review focus

1. **Free text or markup smuggled through parameters.** Extra keys, 41-character values, `<script>`, URLs, braces and control characters must all be refused before anything is stored. Pinned by `test_params_reject_extra_oversized_markup_and_url` (Task 07.1) and `test_create_rejects_non_staff_sendable_template` (Task 07.5).
2. **AI-derived message to a customer without approval.** Pinned by `test_ai_derived_without_approval_ref_is_rejected_and_counted` (Task 07.2).
3. **Two messages for one event.** A redelivered event, a crash after the stub accepted the message but before the row said `sent`, and a retry must not double-send. Pinned by `test_duplicate_event_sends_once` (Task 07.4) and `test_crash_after_send_before_mark_does_not_double_send` (Task 07.3).
4. **Stale or erased contact data.** A changed number takes effect at once and an erased customer is never contacted. Pinned by `test_contact_fetched_at_send_time_and_not_stored` and `test_erased_customer_is_cancelled` (Task 07.3).
5. **Floods.** One trigger or one agent must not be able to send unbounded messages. Pinned by `test_agent_daily_cap_429` and `test_trigger_hourly_cap_drops_excess` (Task 07.2).

## Files

Paths relative to `backend/src/hub/` unless stated.

- `notifications/domain/templates.py` (`ParamSchema`, `validate_params`, `render`, locale formatters), `domain/retry.py` (`retry_delay`), `domain/errors.py`.
- `notifications/contracts.py` (`NotificationService`, `NotificationRequest`, `NotificationView`, template code constants), `events.py` (`NotificationRequested`, `NotificationSent`, `NotificationFailed`).
- `notifications/application/`: `request_notification.py`, `send_notification.py`, `event_map.py`, `event_handlers.py`, `staff_notifications.py`, `preferences.py`, `ports.py` (`Notifier`, `OutboundMessage`, `DeliveryReceipt`), `wiring.py`.
- `notifications/infrastructure/`: `models.py` (`Notification`, `NotificationTemplate`, `CustomerNotificationPrefs`, unmanaged), `repositories.py`, `sms_stub.py`, `email_stub.py`, `in_memory.py`.
- `notifications/api/serializers.py` (`NotificationSerializer`, `NotificationCreateSerializer`, `NotificationTemplateSerializer`, `NotificationPreferencesSerializer`), `views.py`, `urls.py`.
- `shared/uow.py` and `shared/events.py` (modify): `OutboxWriter.add(event, *, customer_facing=False, ai_derived=False, approval_ref=None, available_at=None)`.
- `db/migrations/NNNN_notifications_support.sql` and `shared/migrations/00NN_notifications_support.py`: columns, constraints, preference table, template rows.
- Settings: `NOTIFICATION_MAX_ATTEMPTS=8`, `NOTIFICATION_RETRY_BASE_S=60`, `NOTIFICATION_SEND_TIMEOUT_S=5`, `NOTIFICATION_AGENT_DAILY_CAP`, `NOTIFICATION_CUSTOMER_STAFF_DAILY_CAP`, `NOTIFICATION_TRIGGER_HOURLY_CAP`.
- Tests under `backend/tests/`: `unit/notifications/test_templates.py`, `test_retry.py`, `test_event_map.py`; `integration/notifications/test_migration.py`, `test_request.py`, `test_send.py`, `test_event_handlers.py`, `test_notifications_api.py`, `test_preferences_api.py`; `e2e/test_receipts_with_stub.py`.

## Interfaces

**Consumes:** `CustomerDirectory.get(customer_id).language` and `CustomerDirectory.contact(customer_id, purpose="notify") -> ContactView` (slice 02; assumed fields `phone_e164`, `email`, `language`, `erased: bool`); `EventHandler`, `JobRegistry.register_consumer` and `register_poller` (slice 05); `UnitOfWork.outbox.add` (slice 00, extended here); `Authorizer.require`, `AuditLog.record`, `Principal` (slice 01); events `payment.confirmed`, `payment.failed` (slice 04), `token.issued`, `token.failed` (slice 06).

**Produces:**

```python
# notifications/contracts.py
@dataclass(frozen=True, slots=True)
class NotificationRequest:
    customer_id: UUID; template_code: str; params: Mapping[str, Any]
    dedupe_key: str; channel: Literal["sms", "email"] | None = None   # None: every channel the template and the customer allow
    created_by: UUID | None = None; ai_derived: bool = False; approval_ref: UUID | None = None
    not_before: datetime | None = None                                # recall hold (D-35); also sets outbox available_at
class NotificationService(Protocol):                                  # docs/03 section 2.11
    def request(self, req: NotificationRequest, *, uow: UnitOfWork) -> list[UUID]: ...   # idempotent on dedupe_key per channel; raises ApprovalRequired, ValidationFailed
# notifications/application/ports.py
@dataclass(frozen=True, slots=True)
class OutboundMessage: notification_id: UUID; channel: str; to: str; subject: str | None; body: str; idempotency_key: str
@dataclass(frozen=True, slots=True)
class DeliveryReceipt: provider_ref: str
class Notifier(Protocol):
    def send(self, msg: OutboundMessage) -> DeliveryReceipt: ...      # raises Retryable (timeout, 5xx, 429) or Permanent
```

Template codes (constants in `contracts.py`): `topup_received`, `topup_failed`, `token_issued`, `token_refunded`, `token_delayed`, `token_blocked`, `ticket_reply_ready`, `ticket_followup`. Events produced: `notification.requested` (`notification_id, customer_id, channel, template, locale, params, dedupe_key, available_at`), `notification.sent` (`notification_id, channel, provider_message_id`), `notification.failed` (`notification_id, reason_code, attempts`). Consumers: `notifications.on_domain_event` registered on `events.payments` and `events.tokens` in group `notifications` (event names `payment.confirmed`, `payment.failed`, `token.issued`, `token.failed`); `notifications.on_notification_requested` on `events.notifications`. Poller `notifications.send_due` (every 5 s, skip locked). Extension point for slices 08 and 12: add entries to `EVENT_MAP` (`wallet.low_balance`, `meter.offline`, `draft.approved`); no other change.

## Tasks

### Task 07.1: Migration, templates, parameter validation and rendering

- [ ] **1. Failing tests.** `integration/notifications/test_migration.py`: `test_new_columns_and_unique_dedupe_key` (`dedupe_key` unique where not null; `locale` check `en|fr`; `attempts` default 0), `test_template_rows_exist_for_every_code_language_channel` (every `(code, channel, language)` of the catalogue below present with `status='active'`), `test_preferences_table_rls_self_only`, `test_models_match_schema`, `test_invariants_still_pass`. `unit/notifications/test_templates.py`: `test_valid_params_render_fr_and_en` (`topup_received`, `amount_minor=500`, `currency=USD`: fr text contains `5,00 USD`, en `5.00 USD`), `test_params_reject_extra_oversized_markup_and_url` (extra key, 41-character `short_text`, `<b>`, `http://x`, `{amount}`, a newline), `test_missing_required_param_rejected`, `test_wrong_type_rejected` (`amount_minor="5"`), `test_kwh_formatting` (`energy_wh=2000`: `2 kWh` en, `2 kWh` fr, 2500 gives `2.5` and `2,5`), `test_sms_body_length_limit_480`, `test_unknown_locale_falls_back_to_fr`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/unit/notifications/test_templates.py -q`. Expected: ImportError.
- [ ] **3. Implement.** Migration `db/migrations/NNNN_notifications_support.sql` (expand only): `ALTER TABLE hub.notifications ADD COLUMN dedupe_key text, ADD COLUMN locale text CHECK (locale IN ('en','fr')), ADD COLUMN attempts integer NOT NULL DEFAULT 0, ADD COLUMN next_attempt_at timestamptz, ADD COLUMN masked_recipient text, ADD COLUMN source_event_id uuid;` and `CREATE UNIQUE INDEX uq_notifications_dedupe ON hub.notifications (dedupe_key) WHERE dedupe_key IS NOT NULL;`; `CREATE TABLE hub.customer_notification_prefs (customer_id uuid PRIMARY KEY REFERENCES hub.customers(id), sms_enabled boolean NOT NULL DEFAULT true, email_enabled boolean NOT NULL DEFAULT true, low_balance_threshold_minor bigint, quiet_hours jsonb, updated_at timestamptz NOT NULL DEFAULT now())` with RLS enabled, a policy for `hub_api` (`customer_id = hub.current_customer_id()`) and one for `hub_worker` (`USING (true)`), `GRANT SELECT, INSERT, UPDATE` to `hub_api`, `GRANT SELECT` to `hub_worker`; the template rows (`INSERT ... ON CONFLICT (code, channel, language) DO NOTHING`) from the catalogue below. Parameter types in `param_schema`: `{"required": {...}, "optional": {...}, "staff_sendable": bool}` with types `int` (0 to 10^12), `currency` (`USD|CDF`), `short_text` (at most 40 characters, letters, digits, space, `.`, `-`, `_`, `#`). `validate_params(schema, params)` returns the typed params or raises `ValidationFailed` with `errors[]`; `render(template, locale, params)` substitutes `{name}` placeholders only, formats money (`amount_minor` plus `currency` into `amount`) and energy (`energy_wh` into `energy_kwh`, trailing zeros dropped) by locale (fr: comma decimal and narrow no-break space thousands; en: dot and comma).

  Template catalogue (SMS body; the email variants of the three receipts add a subject line and the same text):

  | Code | Channels | Required params | fr | en |
  |---|---|---|---|---|
  | `topup_received` | sms, email | `amount_minor`, `currency` | `Soleil Grid : paiement de {amount} reçu. Votre crédit d'énergie arrive dans quelques instants.` | `Soleil Grid: payment of {amount} received. Your energy credit will arrive shortly.` |
  | `topup_failed` | sms | `amount_minor`, `currency` | `Soleil Grid : le paiement de {amount} n'a pas abouti. Vous pouvez réessayer.` | `Soleil Grid: the payment of {amount} did not go through. You can try again.` |
  | `token_issued` | sms, email | `amount_minor`, `currency`, `energy_wh` | `Soleil Grid : {energy_kwh} kWh ont été crédités sur votre compteur ({amount}).` | `Soleil Grid: {energy_kwh} kWh were credited to your meter ({amount}).` |
  | `token_refunded` | sms, email | `amount_minor`, `currency` | `Soleil Grid : le crédit n'a pas pu être livré à votre compteur. {amount} sont revenus dans votre portefeuille.` | `Soleil Grid: the credit could not be delivered to your meter. {amount} is back in your wallet.` |
  | `token_delayed` | sms | `amount_minor`, `currency` | `Soleil Grid : votre crédit de {amount} n'est pas encore confirmé par le compteur. Nous vérifions, vous n'avez rien à faire.` | `Soleil Grid: your credit of {amount} is not yet confirmed by the meter. We are checking, you need do nothing.` |
  | `token_blocked` | sms | `amount_minor`, `currency` | `Soleil Grid : votre paiement de {amount} est reçu mais votre compteur n'est pas actif. Le montant reste dans votre portefeuille.` | `Soleil Grid: your payment of {amount} is received but your meter is not active. The amount stays in your wallet.` |
  | `ticket_reply_ready` | sms, email | `ticket_ref` | `Soleil Grid : une réponse à votre demande {ticket_ref} est disponible dans votre espace client.` | `Soleil Grid: a reply to your request {ticket_ref} is available in your customer portal.` |
  | `ticket_followup` (`staff_sendable`) | sms | `ticket_ref` | `Soleil Grid : nous reviendrons vers vous au sujet de votre demande {ticket_ref}.` | `Soleil Grid: we will get back to you about your request {ticket_ref}.` |

  `ticket_reply_ready` is not staff-sendable; it is used only by the approved-draft path of slice 12.
- [ ] **4. Run** until green; run `uv run pytest tests/test_alignment.py tests/test_schema_in_postgres.py -q` from the repository root.
- [ ] **5. Commit:** `feat(notifications): schema support, bilingual templates and parameter validation`

### Task 07.2: Requests, dedupe, caps and the approval rule

- [ ] **1. Failing tests.** `integration/notifications/test_request.py`: `test_request_writes_row_and_outbox_in_one_transaction` (TC-FR-19-1: inside `with uow:` the business insert then `NotificationService.request`; an exception after it leaves zero rows in `notifications`, `outbox` and the business table), `test_request_commits_both_rows_together`, `test_duplicate_dedupe_key_is_noop_returning_same_id`, `test_two_channels_create_two_rows_with_distinct_keys`, `test_channel_chosen_from_template_and_preferences` (email disabled in preferences: sms only), `test_ai_derived_without_approval_ref_is_rejected_and_counted` (raises `ApprovalRequired`, no row, metric `notification_ai_without_approval_total` 1, audit entry `notification.rejected_unapproved`), `test_ai_derived_with_approval_ref_sets_outbox_flags` (`customer_facing`, `ai_derived`, `approval_ref` set; `ck_outbox_ai_approval` holds), `test_not_before_sets_scheduled_at_and_available_at`, `test_unknown_template_or_invalid_params_422_and_nothing_stored`, `test_agent_daily_cap_429` (101st staff-created notification of one agent in a UTC day: `rate_limited`), `test_customer_staff_daily_cap_429`, `test_trigger_hourly_cap_drops_excess` (system trigger: fourth `topup_received` for one customer within an hour is not stored, counter `notification_dropped_total{reason="trigger_cap"}`), `test_contact_not_in_row_or_event` (no phone or email substring in `params`, `masked_recipient` is null before sending, outbox payload).
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/notifications/test_request.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `RequestNotification.__call__(req, uow) -> list[UUID]`: validate the template and params, choose locale from `CustomerDirectory.get(customer_id).language` (fallback `fr`), compute channels (template channels intersected with `req.channel`, then preference flags; a channel with no template row is skipped), apply the trigger cap for system requests (count in `notifications` by customer and template over one hour), insert one row per channel with `dedupe_key` per channel (`{req.dedupe_key}:{channel}`, `ON CONFLICT (dedupe_key) DO NOTHING RETURNING id`; zero rows returns the existing id), `scheduled_at = req.not_before or now`, add `NotificationRequested` to the outbox with `uow.outbox.add(event, customer_facing=True, ai_derived=req.ai_derived, approval_ref=req.approval_ref, available_at=req.not_before)`. If `ai_derived` and no `approval_ref`: raise `ApprovalRequired`, increment the metric, record the audit entry, store nothing. Extend `OutboxWriter.add` with the keyword flags (raw insert includes the flag columns). The staff caps are enforced in `staff_notifications.py` (Task 07.5) with the same repository counts.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(notifications): idempotent requests, caps and approval rule for AI-derived messages`

### Task 07.3: Stub adapters, sender, retry and dead-letter

- [ ] **1. Failing tests.** `unit/notifications/test_retry.py`: `test_delays_1_2_4_8_16_32_64_128_minutes`, `test_total_window_about_four_hours` (sum 255 minutes). `integration/notifications/test_send.py` (`InMemoryNotifier`, `FrozenClock`): `test_successful_send_marks_sent_and_emits_event` (`sent_at`, `provider_ref`, `content_sha256` set, `masked_recipient` like `+243*****111`, `notification.sent` outbox row), `test_sms_stub_failures_retry_with_backoff_then_single_delivery` (TC-FR-19-7: script 500, then 429, then ok: attempts 3, next attempts at 1 and 2 minutes (429 honours `Retry-After` when larger), exactly one recorded delivery), `test_permanent_failure_after_8_attempts_sets_failed_and_emits_failed_event`, `test_timeout_5s_is_retryable`, `test_crash_after_send_before_mark_does_not_double_send` (the stub dedupes by `idempotency_key = notification id`; the retried send returns the same `provider_ref`), `test_contact_fetched_at_send_time_and_not_stored` (change the phone between request and send: the new number is used; nothing stored except the mask), `test_erased_customer_is_cancelled` (`cancelled`, `last_error='no_contact'`, no send), `test_customer_without_email_skips_email_channel_as_cancelled`, `test_claim_is_skip_locked_and_leases_next_attempt`, `test_retry_endpoint_resets_failed_to_queued` (service level, attempts 0). `test_stubs.py`: `test_sms_stub_scripted_modes_from_redis` (`hub:stub:sms:script` list of `ok|500|429|timeout` consumed one per send), `test_stub_log_line_has_no_body_or_full_number`, `test_stub_keeps_last_100_messages_in_redis_for_inspection`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/unit/notifications/test_retry.py tests/integration/notifications/test_send.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `SendNotification.__call__(notification_id)`: short transaction claims the row (`status='queued' AND scheduled_at <= now()`, `skip_locked`), increments `attempts`, sets `scheduled_at = now + retry_delay(attempts)` as the lease and commits; outside any transaction fetch the contact, render the template, call the `Notifier` with `idempotency_key=str(notification_id)`; a second transaction records `sent` (with `provider_ref`, `content_sha256`, `masked_recipient`) and adds `NotificationSent`, or on `Retryable` stores `last_error` and keeps `queued`, or at `attempts >= NOTIFICATION_MAX_ATTEMPTS` sets `failed` and adds `NotificationFailed`; `Permanent` fails at once; missing contact cancels. `retry_delay(n) = base * 2**(n-1)` seconds with base 60. `SmsStubNotifier` and `EmailStubNotifier` log a structured line (id, template, channel, masked recipient, content hash), keep the last 100 messages in Redis list `hub:stub:<channel>:messages`, and read scripted failure modes from list `hub:stub:<channel>:script`; they dedupe on `idempotency_key` with a Redis hash and never open a network connection. Poller `notifications.send_due` calls the use case for due rows; consumer `notifications.on_notification_requested` triggers one immediate attempt for rows whose `scheduled_at <= now()`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(notifications): stub adapters, sender with backoff and dead-letter`

### Task 07.4: Event mapping for receipts and token failure

- [ ] **1. Failing tests.** `unit/notifications/test_event_map.py`: `test_mapping_table` (parametrised: `payment.confirmed` gives `topup_received` with `amount_minor`, `currency`; `payment.failed` gives `topup_failed`; `token.issued` gives `token_issued` (amount from the top-up amount is not in the event, so the handler reads it through `PaymentService.get_top_up`); `token.failed` with `final=true, status=failed` and a token id gives `token_refunded`; `token.failed` with `status=unknown` gives `token_delayed`; `token.failed` with `token_id=null` gives `token_blocked`; `payment.reversed`, `ledger.posted` and unknown names give nothing), `test_dedupe_key_format` (`{event_id}:{template}`; the service appends channel and customer). `integration/notifications/test_event_handlers.py`: `test_payment_confirmed_creates_receipt_row`, `test_duplicate_event_sends_once` (same event twice through the framework: one notification per channel, one send), `test_same_template_new_event_ids_within_hour_respects_trigger_cap`, `test_token_failed_final_creates_refund_notice`, `test_unknown_token_creates_delay_notice`, `test_late_confirmation_still_gets_receipt`, `test_handler_failure_retries_then_dead_letters` (service raises `Retryable`: redelivery, then `dlq.events.payments` after 5), `test_ai_event_without_approval_is_not_mapped` (an event flagged ai-derived in the map input is refused by the service). `e2e/test_receipts_with_stub.py`: `test_top_up_to_receipts_in_french_and_english` (provider stub top-up for a French and an English customer; the SMS stub list holds `topup_received` then `token_issued` texts in the right language; each exactly once, even with the provider stub in `duplicate` mode).
- [ ] **2. Run.** `cd backend && uv run pytest tests/unit/notifications/test_event_map.py tests/integration/notifications/test_event_handlers.py -q`. Expected: ImportError.
- [ ] **3. Implement.** `event_map.py`: `EVENT_MAP: dict[str, Callable[[EventEnvelope, Lookups], list[Spec]]]` with `Spec(template_code, params)`; `Lookups` provides `top_up_amount(top_up_id)` through `PaymentService.get_top_up`. `event_handlers.py`: `OnDomainEvent` (handler name `notifications.on_domain_event`, `event_names` = the keys of the map): for each spec call `NotificationService.request(NotificationRequest(customer_id, template_code, params, dedupe_key=f"{event_id}:{template_code}"), uow=uow)`. The handler contains no business rule beyond the map. Wire both consumers in `wiring.py`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(notifications): event mapping for receipts and token failure notices`

### Task 07.5: Staff and portal API

- [ ] **1. Failing tests.** `integration/notifications/test_notifications_api.py`: `test_create_notification_202_and_row_queued` (support agent, `ticket_followup`, `Idempotency-Key`), `test_create_same_key_twice_one_row`, `test_create_rejects_non_staff_sendable_template` (422 `template_not_allowed` for `topup_received`), `test_create_rejects_free_text_param` (422), `test_create_requires_support_agent_role` (403 for others), `test_create_out_of_site_customer_404`, `test_agent_daily_cap_429`, `test_list_filters_status_and_customer_cursor_pagination`, `test_status_mapping` (stored `failed` is returned as `dead_lettered`, `queued` as `queued`, `sent` as `sent`), `test_get_notification_hides_params_values` (only parameter names and the masked recipient), `test_retry_requires_operations_engineer_and_resets_failed_to_queued`, `test_retry_on_sent_is_409`, `test_templates_list_shows_required_and_optional_params_and_locales`, `test_templates_list_has_no_body_text`. `test_preferences_api.py`: `test_get_defaults_when_no_row`, `test_replace_validates_quiet_hours_format` (`HH:MM`), `test_customer_sees_only_own_preferences`, `test_replace_is_idempotent`, `test_threshold_money_currency_matches_wallet_currency`.
- [ ] **2. Run.** `cd backend && uv run pytest tests/integration/notifications/test_notifications_api.py tests/integration/notifications/test_preferences_api.py -q`. Expected: 404 on the routes.
- [ ] **3. Implement.** `NotificationViewSet` (`listNotifications`, `getNotification`, `createNotification`, `retryNotification`) with `@operation(...)`; `createNotification` uses `@idempotent(scope="POST /api/v1/notifications", window=timedelta(hours=24))`, checks `staff_sendable`, caps and site scope (`Authorizer.require_site`), then `NotificationService.request(... created_by=principal.subject_id, dedupe_key=f"api:{principal.subject_id}:{idempotency_key}")`. `retryNotification` sets `failed` rows to `queued` with `attempts=0` and `scheduled_at=now` through the narrow `hub_api` UPDATE and writes an audit entry. `NotificationSerializer` maps status as in the test and never returns parameter values. `NotificationTemplateViewSet` (`listNotificationTemplates`) derives `required_params`, `optional_params` and `locales` from the rows. Preferences endpoints read and write `customer_notification_prefs` for `principal.customer_id`; the money field uses `MoneyField`.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(notifications): staff notification API, template catalogue and customer preferences`

### Task 07.6: Conformance, wiring and demo coverage

- [ ] **1. Failing tests.** `contract/test_openapi_conformance.py::test_schemathesis_notifications`. `architecture/test_notifications_boundaries.py`: `test_no_other_module_imports_notifications_internals`, `test_notifier_only_used_by_send_use_case`. `integration/notifications/test_request.py::test_outbox_row_is_consumed_end_to_end` (relay and worker running: `notification.requested` reaches the consumer and the stub list).
- [ ] **2. Run.** `cd backend && uv run pytest tests/contract/test_openapi_conformance.py -q -k notifications`. Expected: failures where wiring or serializers are missing.
- [ ] **3. Implement.** Finish `wiring.py` (consumers, poller, adapters selected by `settings.adapters`), mount the routes in `urls/api.py`, and add the compose settings for the stub Redis keys. No new behaviour.
- [ ] **4. Run** until green.
- [ ] **5. Commit:** `feat(notifications): wiring and conformance tests`

## Verification

`make verify SLICE=07` runs from the repository root; each command must exit 0.

1. `cd backend && uv run ruff check . && uv run mypy --strict src/hub/notifications src/hub/shared && uv run lint-imports` (zero findings).
2. `cd backend && uv run pytest tests/unit/notifications -q` (all passed).
3. `cd backend && uv run pytest tests/integration/notifications -q` (all passed).
4. `cd backend && uv run pytest tests/contract/test_openapi_conformance.py -q -k "notifications"` (no 5xx, schema conformance).
5. `make up && cd backend && uv run pytest tests/e2e/test_receipts_with_stub.py -q` (all passed).
6. `python -m pytest tests/test_alignment.py tests/test_schema_in_postgres.py -q && python tools/check_traceability.py` from the repository root (green).
7. `cd backend && uv run pytest tests/architecture -q` (boundary and Django-configuration checks pass).

## Demo script

1. `make up`; seed one French-speaking and one English-speaking customer with meters (fixtures command).
2. Complete a provider-stub top-up for each. Inspect the SMS stub (`docker compose exec redis redis-cli LRANGE hub:stub:sms:messages 0 -1`): each customer has a `topup_received` then a `token_issued` message in their own language with locale-formatted amounts; the French text uses a comma decimal.
3. Set the stub script to `500`, `429`, `ok` (`redis-cli RPUSH hub:stub:sms:script 500 429 ok`) and complete a third top-up: the receipt appears after the retries; `GET /api/v1/notifications` shows `attempts` 3 and status `sent`.
4. Set simulator mode `token_reject{reason:"invalid_meter"}` and complete a top-up: the customer receives `token_refunded`.
5. Put the stub in permanent failure (eight `500` entries): after eight attempts the notification shows `dead_lettered` and a `notification.failed` event exists; as an operations engineer `POST /api/v1/notifications/{id}/retry` returns 202 and the next attempt succeeds.
6. As a support agent `POST /api/v1/notifications` with template `ticket_followup`: 202. With template `topup_received`: 422 `template_not_allowed`. With a parameter containing `<b>`: 422.
7. Run the provider stub in `duplicate` mode for a top-up: still one receipt per channel.

## Done criteria

Requirements: FR-19 (AC-FR-19-1 to 19-4; AC-FR-19-4 is tested by `test_ai_derived_without_approval_ref_is_rejected_and_counted`, which has no TC id in the matrix), FR-16 for notification text.

Test cases implemented and passing: TC-FR-19-1 (Task 07.2, notification level), TC-FR-19-7 (Task 07.3), TC-FR-19-8 (Tasks 07.1, 07.2, 07.5). TC-FR-19-2 to TC-FR-19-6 belong to slice 05 and run against the same outbox.

## Plan notes for the tech lead

1. **`notifications` lacks columns the design relies on.** docs/03 section 2.11 and AC-FR-19-3 need a unique dedupe key, and AC-FR-19-2 needs attempt counts and retry times; the frozen table has `scheduled_at` but no key, attempts or locale, and the API `Notification` schema returns `locale` and `attempts`. Task 07.1 adds them by an expand-only migration.
2. **Status vocabularies differ.** The OpenAPI `Notification.status` lists `queued, sending, sent, delivered, failed, dead_lettered`; the schema allows `queued, sent, failed, cancelled` (D-01: the schema wins). The API maps terminal `failed` to `dead_lettered` and never returns `sending` or `delivered` (the stubs give no delivery receipts); `tests/test_alignment.py` may need its enum exception for this response field recorded.
3. **The outbox writer cannot set the customer-facing and AI flags.** Slice 00's `OutboxWriter.add` inserts five columns. The approval rule needs `customer_facing`, `ai_derived`, `approval_ref` and `available_at`, so Task 07.2 extends the writer (slice 00 files).
4. **No storage for notification preferences** exists although `getNotificationPreferences` and `replaceNotificationPreferences` carry FR-19; the migration adds `customer_notification_prefs`. Quiet hours are stored but not enforced. docs/03 also lists a `suppression_list` that the schema does not have; it is omitted.
5. **Sync versus async notifier.** docs/03 section 5 declares `async send` for `Notifier`, while sections 1.2 and 9 make every service synchronous except the stream pool. The worker is synchronous, so `send` is synchronous.
6. **Receipt count.** One SMS on `payment.confirmed` and one on `token.issued` gives two messages per top-up; AC-FR-19-3 and the PRD story say one receipt per event that concerns the customer. I followed the event reading. If the product owner wants one, drop `topup_received` from the map.
7. **AC-FR-19-4 says an alert is raised.** `alerts.kind` has no fitting kind, so I raise a metric, an audit entry and a log line (alert rule belongs to slice 17).
8. **Retry tables.** The PRD (1 minute doubling, 8 attempts) governs the sender; docs/03 section 4 (5 deliveries) governs stream consumers (slice 05).
9. **Per-agent cap** has no number anywhere (docs/05 says only "a per-agent daily cap" and the tier table lists none); defaults are settings marked open above.
10. **Mapped events for later slices.** `wallet.low_balance`, `meter.offline` and `draft.approved` are in docs/03's consumer list but belong to slices 08 and 12; the `EVENT_MAP` extension point is provided.
11. **Template wording** is mine; legal review is open (D-35 notes a similar open item for portal terms).
