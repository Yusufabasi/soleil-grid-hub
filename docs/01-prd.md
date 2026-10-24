# 01 - Product Requirements Document: Soleil Grid Hub

Owner: Product planner. Status: design phase, nothing built. This document follows `00-shared-context.md` and keeps its FR/NFR ids. Every figure is an **assumption** unless marked otherwise. Test cases derived from this PRD should use `TC-<FR id>-n` and trace back to the `AC-FR-nn-k` ids below.

---

## 1. Vision, problem, audience, success

### 1.1 Vision
One internal platform where a payment made on a customer's phone becomes energy on their meter in under a minute, exactly once. Every franc can be traced from the provider statement to the ledger to the meter. Staff (support, finance, operations) can work in French or English with AI help that is bounded, audited and always reviewed by a person before it reaches a customer or touches money.

### 1.2 Problem statement (assumed current state, 3,000 customers)
- Top-ups are confirmed by people cross-checking provider SMS against a spreadsheet. Credit reaches the meter in 15 to 45 minutes and sometimes twice.
- Provider statements are reconciled by hand in spreadsheets. Month-end close takes about 5 working days.
- Sites going offline are found out from customer calls, on average about 6 hours after the outage starts.
- Support runs on a shared WhatsApp number and an inbox. There is no ticket history, and the median first response is about 24 hours.
- Ad-hoc data questions ("revenue per site last month") go to one engineer and take about 2 working days.
- None of this scales to the 300,000-customer target (NFR-03).

### 1.3 Who it is for
Six personas (section 2). Five of them are people, and the sixth is a machine client. The primary internal users are Support, Finance and Operations. Customers use one thin portal for top-up, balance and tickets.

### 1.4 Success metrics (baselines are assumptions; measure them in the R0 discovery week)

| # | Metric | Baseline (assumed) | Target at end of R4 | Source |
|---|---|---|---|---|
| M1 | Payment confirmed to token accepted by meter, p95 | 15-45 min | 60 s (meter online) | payments + meters events |
| M2 | Payments applied to the ledger more than once | about 0.3% of top-ups | 0 (hard invariant) | reconciliation report |
| M3 | Month-end close (statement imported to period locked) | 5 working days | 1 working day; unmatched exceptions under 0.5% of transactions | reconciliation module |
| M4 | Site-offline mean time to detect | about 6 h | 20 min or less | alert timestamps vs telemetry gap |
| M5 | Support median first response (business hours) | about 24 h | 4 h or less; at least 60% of AI drafts approved with no or light edits (under 20% characters changed) | support module |
| M6 | Ad-hoc data question turnaround | about 2 days | 80% answered in ask-the-data without an engineer | FR-12 logs + monthly survey |

---

## 2. Personas

**P1 Customer (household or small shop owner, rural or peri-urban DRC).**
- Goals: top up in seconds, know the balance, get power back quickly.
- Pains: credit that never arrives, no proof of payment, support that cannot be reached.
- Context: low-end Android or a feature phone. The phone is often shared with family, so sessions must be short, nothing sensitive can persist on the device, and logins use a one-time code. Connectivity is 2G/3G with bundles bought by the MB. Prefers French, often Lingala or Swahili at home. Patchy literacy, so icons, short text and amounts in local format.
- Top tasks: top up, check balance and last 5 top-ups, open a ticket, read the SMS receipt.

**P2 Support agent (call-centre style, 8 to 20 agents at target scale).**
- Goals: clear the queue, give correct answers, avoid escalations.
- Pains: re-asking for context, no payment visibility, writing the same reply in two languages.
- Context: office broadband that sometimes drops, so the UI must work on a 1 Mbps link. French first, mixed-skill.
- Top tasks: triage queue, look up a customer's payments and token status, approve AI drafts, escalate.

**P3 Finance officer (2 to 4 people).**
- Goals: books that balance, fast close, an audit-ready trail.
- Pains: spreadsheet reconciliation, untraceable adjustments.
- Context: desktop, works mostly in French with figures in CDF and USD.
- Top tasks: import provider statements, work the exception queue, approve adjustments (four-eyes), lock the period, export reports, ask revenue questions.

**P4 Operations engineer (field and NOC, 5 to 15 people).**
- Goals: know which site or meter is down before customers call, dispatch the right technician.
- Pains: blind spots, alert floods.
- Context: often on mobile data in the field. Needs a dashboard usable on a tablet at 3G speeds.
- Top tasks: watch the dashboard, acknowledge and resolve alerts, inspect a meter's telemetry, check token delivery backlog.

**P5 Administrator (1 or 2 people, IT/ops lead).**
- Goals: least-privilege access, a safe AI configuration, compliance.
- Pains: shared logins, no audit trail.
- Scope (D-14): a platform administrator, not a business user. The administrator has no money capability, no access to ticket or customer content and no ask-the-data access, and cannot initiate top-ups. `finance_lead` and `dp_admin` (data-protection administrator) are user attributes, and `kb:edit`, `kb:publish` and `export:bulk` are permissions inside roles, not new roles.
- Top tasks: manage users and roles, issue and revoke API keys, set AI budgets, run the administrative steps of erasure and export requests (second approver required), review the audit log.

**P6 Integrator (machine client: mobile-money provider stub, meter gateway, future partners).**
- Goals: a predictable API, clear errors, idempotency.
- Pains: ambiguous retries, silent drops.
- Top tasks: send signed webhooks, post telemetry batches, receive token commands, respect rate limits.

---

## 3. End-to-end journeys

### J1 Customer top-up
1. The customer signs in to the portal with phone number + 6-digit one-time code (FR-15, FR-16).
2. The customer picks a meter and enters 5,000 CDF. The portal sends initiate with an idempotency key (FR-01, FR-02).
3. The hub validates limits, creates the top-up in `initiated`, calls the provider stub and moves it to `pending`. The customer gets a PIN prompt on the phone (FR-02).
4. The provider sends the signed success webhook, then retries it twice (FR-03).
5. The hub verifies the signature and timestamp, then in one transaction records the event once in `provider_events`, posts to the ledger and writes the outbox row, and only then acks (under 300 ms p95). The duplicates get the same ack with no new effect (FR-03).
6. A balanced posting debits `provider_clearing` and credits `customer_wallet`, the top-up becomes `confirmed`, and the event `payment.confirmed` is published through the outbox (FR-04, FR-03).
7. Token issuance converts wallet money into kWh at the plan tariff, posts wallet to `revenue_energy`, and sends the token to the meter gateway, retrying until the meter acknowledges (FR-05).
8. An SMS receipt in French goes out through the outbox (FR-19, FR-16).
9. The portal shows the top-up as confirmed with the kWh amount. The audit log has every step (FR-15).

### J2 Support ticket with AI triage and approved reply
1. The customer emails "Pas de courant depuis hier, j'ai payé 10 000 FC". The inbound-email stub creates a ticket linked by sender address (FR-08).
2. The text is redacted, then AI triage returns category `no_power`, urgency `high` (P2), language `fr`, and flags `payment_reference`, applied as a suggestion the agent can override (FR-09).
3. The agent opens the queue sorted by urgency and sees the customer's top-ups and token status. The token is `pending` with retries under way (attempts above 0) because the meter is offline (FR-01, FR-05).
4. The agent asks for a draft. The AI drafts a French reply citing KB article "Meter offline after top-up" v3 (FR-10, FR-17).
5. The agent edits one sentence and approves. The reply is sent by email through the outbox. Draft, edit diff and approver are audited (FR-10, FR-19, FR-15).
6. After 12 messages, a colleague taking over reads the AI summary (FR-11).

### J3 Month-end finance reconciliation
1. On day 1 of the month, the finance officer uploads the provider's monthly statement CSV (FR-14).
2. The hub matches 99.6% of rows by provider transaction id. The remaining rows go to the exception queue, typed (FR-14, FR-04).
3. For a "statement-only" row (money received, no webhook ever arrived), the officer links it to the customer's pending top-up. The hub posts it exactly once through the same path as a webhook (FR-14, FR-03, FR-04).
4. For a fee mismatch, the officer proposes an adjustment to `fees`/`suspense`. A different officer approves it through the approvals resource (maker-checker, bound to the exact payload) (FR-14, FR-15).
5. Once exceptions are zero or each one is explained, the officer locks the period. Later postings dated in that period are rejected (FR-04, FR-14).
6. The officer exports the trial balance and the reconciliation report in CSV, with French locale formatting (FR-20, FR-16).

### J4 Operations incident: a site goes offline
1. The telemetry batches from site "Kasangulu-02" stop. The gateway heartbeat (every 5 minutes) is missing for 15 minutes (FR-06, FR-07).
2. The hub raises a single `site_offline` alert (not 412 meter alerts) and the dashboard turns the site red within 15 s (FR-07, FR-13).
3. The ops engineer acknowledges the alert and records the cause "inverter trip" (FR-07, FR-15).
4. During the outage, 37 tokens are `pending` with retries under way and the dashboard shows the backlog (FR-05, FR-13).
5. Support sees a site banner on tickets from affected customers. The agent bulk-links 9 tickets to the incident (FR-08).
6. The power returns and the late batches arrive in one go. Readings are accepted, duplicates are dropped, and tokens are delivered (FR-06, FR-05).
7. The alert auto-resolves after 2 consecutive healthy heartbeats. Affected customers get an SMS saying power is restored (FR-07, FR-19).

### J5 Analyst asks the data (actor: finance officer; operations engineer equivalent)
1. The officer asks in French: "Revenu énergie par site en septembre, comparé à août" (FR-12, FR-16).
2. The AI generates SQL against the aggregate-first views her finance role allows. The hub validates it as a single read-only SELECT and runs it on the replica with a 5 s timeout (FR-12, FR-15).
3. The SQL, a results table (max 1,000 rows) and a one-paragraph French explanation of the SQL and its assumptions (query rows never reach the model) appear in under 8 s (FR-12).
4. She asks "and the customers' phone numbers?". The hub declines because no view contains phone numbers (FR-12, FR-20).
5. She exports the results to CSV. The question, SQL, row count and export are audited (FR-20, FR-15).

---

## 4. Functional requirements: stories and acceptance criteria

Conventions: amounts are in integer minor units, timestamps are UTC, error bodies use the API error format defined in `05-api-design.md`. "Audited" means an entry in the FR-15 audit log with actor, action, target, timestamp and correlation id.

### FR-01 Customer and meter registry
**Story:** As an operations engineer I want one registry of sites, customers, meters and plans with their status, so that payments, tokens and alerts always resolve to the right customer and meter.
- **AC-FR-01-1:** Given a site exists, when an admin or ops engineer creates a meter with a serial that is unique across the registry, then the meter is created in status `inactive` with a UUIDv7 id and the change is audited.
- **AC-FR-01-2:** Given a meter serial already exists, when a second meter is created with the same serial, then the API returns 409 `serial_conflict` and nothing is written.
- **AC-FR-01-3:** Given an active customer, when their meter is assigned a plan (tariff per kWh in minor units + currency USD or CDF), then the customer's wallet currency equals the plan currency. A plan change to a different currency while the wallet balance is non-zero is rejected with 422 `currency_change_blocked`.
- **AC-FR-01-4:** Given a meter in status `decommissioned`, when a top-up or token is requested for it, then the request is rejected with 422 `meter_not_active`.
- **AC-FR-01-5:** Given a support agent, when they search by phone number (E.164) or meter serial, then matching customers come back within 200 ms p95 at 300,000 customers.

### FR-02 Prepaid top-up
**Story:** As a customer I want to top up my meter from my mobile-money wallet, so that I get energy without travelling to an agent.
- **AC-FR-02-1:** Given a signed-in customer and an active meter, when they initiate a top-up of 5,000 CDF with an `Idempotency-Key`, then the API returns 202 with a top-up id, the top-up is stored in `initiated` with the quoted tariff and `energy_wh` (D-08), moves to `pending` once the provider accepts, and the provider stub is called once.
- **AC-FR-02-2:** Given the same `Idempotency-Key` and an identical body within 7 days (D-12), when initiate is repeated, then the original top-up is returned and the provider is not called again. With the same key and a different body, the API returns 422 `idempotency_key_reuse`.
- **AC-FR-02-3:** Given limits (assumption: min 500 CDF / 0.50 USD, max 500,000 CDF / 200 USD per top-up, 10 top-ups per meter per 24 h), when an amount or count is outside them, then the API returns 422 `amount_out_of_range` or 429 `topup_limit` and no provider call is made.
- **AC-FR-02-4:** Given the provider call times out after 10 s, when no response is received, then the top-up goes to `pending_unknown`, is never auto-failed, and is resolved by the webhook, by a status poll every 2 minutes for 30 minutes, or by FR-14.
- **AC-FR-02-5:** Given a top-up that became `expired` after 15 minutes with no confirmation, when a valid success webhook arrives later, then it is still credited exactly once (the customer's money was taken), the status becomes `confirmed` and the boolean `late` is set (D-05). Only an amount or currency mismatch, a decommissioned meter or an erased customer routes the money to `suspense` with an FR-14 exception.
- **AC-FR-02-6:** Given a customer cancels a `pending` top-up in the portal, when the status becomes `cancelled`, then no further provider call is made on the customer's behalf, and if the provider's success confirmation still arrives it is credited exactly once and flagged `late` (D-05). The money is never dropped.
- **AC-FR-02-7:** Given a confirmed top-up, when the provider sends `payment.reversed`, then reversing ledger entries are posted and the top-up becomes `reversed`. If the wallet no longer holds the money because energy was already issued, the shortfall is posted to `chargeback_receivable` and an FR-14 exception is opened (D-06).
- **AC-FR-02-8:** Given the canonical top-up statuses `initiated`, `pending`, `pending_unknown`, `confirmed`, `failed`, `expired`, `cancelled`, `reversed` plus the boolean `late`, when any component reads or writes a top-up status, then only these values are accepted. The UI maps them to labels (for example `pending_unknown` is shown as "needs review") (D-01).

### FR-03 Webhook ingestion
**Story:** As a finance officer I want provider webhooks to be verified and applied exactly once, so that retries and forgeries never create or lose money.
- **AC-FR-03-1:** Given a webhook with a valid HMAC-SHA256 signature over the string `timestamp.event_id.body` and a timestamp within ±5 minutes, when it is received, then processing is synchronous: in one transaction the event is recorded in `provider_events`, posted to the ledger and written to the outbox, and only then acked 200 within 300 ms p95. The ledger effect is visible within 5 s p95.
- **AC-FR-03-2:** Given an event whose provider event id has already been processed, when it is redelivered (any number of times, including concurrently), then the response is 200 with the original result and no second posting, notification or token exists.
- **AC-FR-03-3:** Given an invalid signature, when the webhook is received, then the response is 401, no business state changes, the event is not stored in `provider_events` (only a quarantine log keeps the raw request for 30 days), and more than 20 failures in 5 minutes raises a security alert.
- **AC-FR-03-4:** Given a valid signature but a timestamp older than 5 minutes (replay), when it is received, then the response is 400 `stale_timestamp` and nothing is applied.
- **AC-FR-03-5:** Given a webhook amount, currency or payer phone number that differs from the pending top-up, when it is processed, then the money is posted to `suspense` (not to the wallet) and an FR-14 exception (`amount_mismatch` or `currency_mismatch`) is opened.
- **AC-FR-03-6:** Given a success webhook for an amount of 100 USD or more (or the CDF equivalent) **[open: threshold, default 100 USD]**, when it is processed, then the amount is confirmed by a pull from the provider's status API before the wallet is credited. If the pull disagrees or fails, the money goes to `suspense` and the top-up is `pending_unknown`.
- **AC-FR-03-7:** Given uniqueness on `(provider, event_id)` and `(provider, provider_txn_id)` enforced in PostgreSQL only (Redis is a hint), when two deliveries race, then exactly one posting exists.

### FR-04 Double-entry ledger and wallet balances
**Story:** As a finance officer I want every money movement recorded as a balanced posting, so that wallet balances and revenue can be proven at any time.
- **AC-FR-04-1:** Given any posting, when it is committed, then the sum of its lines per currency equals 0. A posting that does not balance is rejected and nothing from it is written.
- **AC-FR-04-2:** Given a confirmed payment, when it is posted, then the database refuses a second posting with the same payment reference (unique constraint), whatever path it came from: webhook, poll or reconciliation.
- **AC-FR-04-3:** Given a customer wallet, when its balance is read, then it equals the negative of the sum of its ledger entries (credit-negative convention: debits positive, credits negative; the API shows lines with an explicit `direction` and a positive amount). A nightly check compares the cached balance with the sum for all wallets and raises an alert on any difference above 0.
- **AC-FR-04-4:** Given a posted transaction, when anyone tries to update or delete it, then the action is refused. Corrections are made only by reversing postings that reference the original.
- **AC-FR-04-5:** Given a locked accounting period, when a posting with an effective date in that period is attempted, then it is rejected with 409 `period_locked`.

### FR-05 Energy-credit token issuance
**Story:** As a customer I want the credit I paid for to reach my meter automatically, even if my meter is briefly offline, so that I never pay without getting power.
- **AC-FR-05-1:** Given a confirmed top-up whose wallet holds at least the top-up amount, when token issuance runs, then inside one transaction the hub re-checks that the meter still belongs to the top-up's customer and is active, and creates a token (statuses `pending`, `sent`, `acknowledged`, `failed`, `cancelled`, `unknown`) with a unique token id, a monotonic per-meter `counter`, an `expires_at` and a reference to its ledger posting (never null). The energy is the `energy_wh` stored at initiation from the quoted tariff, rounded down to the watt-hour; the remainder stays in the wallet. The command is sent to the site gateway within 5 s p95.
- **AC-FR-05-2:** Given the meter does not acknowledge, when retries run, then they follow exponential backoff (30 s doubling, capped at 30 min) for up to 72 h, reusing the same token id so the meter applies it at most once ("retrying" is `pending` with attempts above 0). The posting is reversed only on a definitive rejection or after reconciliation confirms the meter never applied the token. Otherwise the token becomes `unknown` and is raised as an exception, never silently failed.
- **AC-FR-05-3:** Given a token is `failed` or confirmed as never applied, when an ops engineer cancels it, then a reversing posting returns the amount to the customer wallet through maker-checker approval and the action is audited. No money is lost.
- **AC-FR-05-4:** Given the meter acknowledges a token id it already applied, when the duplicate ack arrives, then the state stays `acknowledged` and no further posting happens.
- **AC-FR-05-5:** Given the daily token reconciliation job, when it compares tokens marked `acknowledged` with the credit registers in meter telemetry, then any mismatch older than 24 h becomes an exception visible to ops.
- **AC-FR-05-6:** Given the v1 integration with site systems, when any component tries to write to a meter or gateway other than by token issuance, then no such command exists: token issuance, bound to meter id, counter and expiry, is the single approved write command to site systems. There is no remote disconnect and no remote configuration in v1.

### FR-06 Meter telemetry ingestion
**Story:** As an operations engineer I want meter readings ingested reliably even when they arrive late, duplicated or in bursts, so that alerts and dashboards reflect reality.
- **AC-FR-06-1:** Given an authenticated meter gateway (FR-18 scope `telemetry:write`), when it posts a batch of up to 1,000 readings, then the API returns 202 within 300 ms p95 with counts `accepted`, `duplicate` and `rejected`.
- **AC-FR-06-2:** Given a reading with the same (meter id, reading timestamp) as a stored one, when it arrives again, then it is counted as `duplicate` and not stored twice.
- **AC-FR-06-3:** Given a reading up to 7 days old, when it arrives, then it is accepted into the correct time partition. Readings older than 7 days or more than 5 minutes in the future are rejected one by one with reasons `too_old` or `future_timestamp`, and the rest of the batch is still accepted.
- **AC-FR-06-4:** Given a sustained 1,700 readings per second (5x peak), when the ingestion load test runs for 30 minutes, then no accepted reading is lost and ingestion lag stays under 60 s p95.
- **AC-FR-06-5:** Given a batch larger than 1,000 readings or malformed JSON, when it is posted, then the response is 413 or 400 and nothing from the batch is stored.

### FR-07 Alerts
**Story:** As an operations engineer I want actionable alerts for offline meters, low balances and anomalous consumption, so that I act before customers call.
- **AC-FR-07-1:** Given a meter whose `last_seen` is more than 90 minutes old (readings arrive in hourly batches), when the detector runs (every 60 s), then one `meter_offline` alert is opened. Given the site gateway heartbeat (sent every 5 minutes) is missing for 15 minutes, then a single `site_offline` alert is opened instead and the meter alerts of that site are suppressed. Alert kinds are `meter_offline`, `site_offline`, `low_balance` and `anomalous_consumption`.
- **AC-FR-07-2:** Given a wallet balance below the plan's low-balance threshold (default: 2 days of the meter's 14-day average consumption), when the balance changes, then a `low_balance` alert is opened and the customer gets at most 1 SMS per 24 h.
- **AC-FR-07-3:** Given hourly consumption above 3 times the meter's 28-day median for that hour, or a register value that decreases, when it is detected, then an `anomalous_consumption` alert is raised with the subtype `spike` or `register_decrease`.
- **AC-FR-07-4:** Given an alert is already open for (target, type), when the condition fires again, then no duplicate alert is created; its `last_seen` is updated instead. The alert auto-resolves after 2 consecutive healthy checks.
- **AC-FR-07-5:** Given a support agent, when they try to acknowledge or resolve an alert, then the response is 403 (only ops engineers and admins may do this), and the denial is audited.

### FR-08 Support tickets
**Story:** As a support agent I want tickets from the portal, the API and email in one queue linked to the customer, so that I have full context in one place.
- **AC-FR-08-1:** Given a customer submits a portal ticket (subject, body up to 5,000 characters, up to 3 attachments of at most 5 MB each, types jpg/png/pdf), when it is saved, then it gets a ticket number `SG-YYYY-nnnnnn`, status `new`, and an acknowledgement SMS in the customer's language.
- **AC-FR-08-2:** Given an inbound email whose sender matches a customer email, or whose subject contains an existing ticket number, when the email stub delivers it, then it is linked to that customer or appended to that ticket. Unmatched senders create a ticket in the `unlinked` queue.
- **AC-FR-08-3:** Given the same email Message-ID delivered twice, when it is processed, then only one ticket message exists.
- **AC-FR-08-4:** Given an attachment with a disallowed type or over 5 MB (or a fourth file), when it is uploaded, then the upload is rejected with 422 and the ticket text is still saved. Accepted attachments are scanned and served from a cookie-less domain.
- **AC-FR-08-5:** Given an open `site_offline` alert, when a ticket arrives from a customer at that site, then the ticket shows the incident banner, and agents can link up to 100 tickets to the incident in one action.

### FR-09 AI triage
**Story:** As a support agent I want each new ticket pre-classified by category, urgency, language and personal-data flags, so that urgent issues reach the front of the queue.
- **AC-FR-09-1:** Given a new ticket, when triage runs, then within 30 s p95 it stores a structured result. Category is one of `billing_payment, no_power, meter_fault, tariff_question, account_change, complaint, safety_hazard, other`. Urgency is `low`, `normal`, `high` or `critical`, mapped to P4, P3, P2 and P1. Language is `fr`, `en`, `sw`, `ln` or `other`. PII flags are a subset of `phone, email, national_id, payment_reference, address, health`. Model id, prompt version and token counts are recorded.
- **AC-FR-09-2:** Given the ticket text contains phone numbers, emails or national ids, when the prompt is built, then those values are replaced by typed placeholders before any call to the model, and the audited prompt contains no raw values.
- **AC-FR-09-3:** Given the model returns stop reason `refusal`, an invalid structured output, or does not respond within 20 s after 1 retry, when triage completes, then the ticket is marked `triage_failed` with the reason and placed in the manual triage queue. No default category is invented.
- **AC-FR-09-4:** Given the per-day triage token budget (NFR-13) is used up, when a new ticket arrives, then triage is skipped with reason `budget_exceeded`, the ticket goes to manual triage, and admins are alerted once per day.
- **AC-FR-09-5:** Given an agent changes the AI category or urgency, when they save, then the override is stored next to the AI value for accuracy reporting, with a target of at least 85% category agreement on a 200-ticket labelled set.
- **AC-FR-09-6:** Given triage results are applied automatically as suggestions, when a result is stored, then an override record is kept for every staff change, low-confidence results go to a review queue, and a release gate requires recall of at least 98% on `needs_human_now` on the labelled set (accepted risk, D-32).
- **AC-FR-09-7:** Given a ticket flagged `needs_human_now`, when on-call paging is considered, then paging is rate-limited, and above a configured threshold a staff member must confirm each page before it is sent.

### FR-10 AI reply drafting with citations and approval
**Story:** As a support agent I want a draft reply in the customer's language grounded in our knowledge base, so that I answer faster and consistently, while staying responsible for what is sent.
- **AC-FR-10-1:** Given a triaged ticket, when the agent requests a draft, then within 15 s p95 a draft in the ticket language (drafts only in `fr` or `en`; EN if `other`; for `sw` or `ln` tickets the draft is in `fr` or `en` with a translator warning) is shown with 1 to 3 citations, each one a published KB article id + version from the retrieved set.
- **AC-FR-10-2:** Given a draft, when nobody has approved it, then there is no code path that sends it. A test asserts that the outbox rejects any customer message without an `approved_by` staff id.
- **AC-FR-10-3:** Given a draft citing an article id or version not in the retrieved set, or having no citation, when it is returned, then it is labelled `unverified` and approving it requires the agent to tick "I checked this answer".
- **AC-FR-10-4:** Given a draft that promises a refund, credit or tariff change (detected by the structured output flag `money_affecting`), when the agent approves it, then sending is blocked until a finance officer also co-approves. The draft text never triggers a ledger action by itself.
- **AC-FR-10-5:** Given an approved reply, when it is sent, then the original draft, the final text, the edit distance, the approver and the timestamp are audited, and the customer sees no AI attribution text beyond the company signature. Customers are told in the portal terms that staff may use AI to prepare replies **[open: wording by legal]** (Q4).
- **AC-FR-10-6:** Given an approver edits the draft text at approval, when they confirm, then the validators (citation check, forbidden-promise lint, `money_affecting` detection, language check) re-run on the edited text before the approval takes effect.
- **AC-FR-10-7:** Given a reply is approved, when it enters the outbox, then it is held for a 30-second recall window during which the approver can recall it (status `recalled`) and nothing is sent. After the window it is sent.

### FR-11 AI summaries
**Story:** As a support agent taking over a ticket I want a short summary of the conversation, so that I do not re-read 20 messages or re-ask the customer.
- **AC-FR-11-1:** Given a ticket with at least 6 messages, when an agent clicks "Summarise", then a summary of 120 words or fewer in the agent's UI language is shown within 10 s p95, with sections: issue, actions taken, open question.
- **AC-FR-11-2:** Given a summary exists, when new messages arrive, then it is marked `stale` with the count of new messages, and no new summary is generated without a request.
- **AC-FR-11-3:** Given a summary, when it is displayed, then it is labelled "AI-generated, internal" and it can never be attached to a customer message.
- **AC-FR-11-4:** Given the model refuses or times out (10 s, 1 retry), when the request fails, then the agent sees a localised error and the ticket is unchanged.

### FR-12 Ask the data
**Story:** As a finance officer or operations engineer I want to ask questions in plain French or English and get an answer with the SQL shown, so that I get numbers without waiting for an engineer and can check how they were computed.
- **AC-FR-12-1:** Given a finance, operations or support user (the administrator has no ask-the-data access), when they ask a question, then the generated SQL, a results table (at most 1,000 rows shown) and a short explanation in the question's language are returned within 8 s p95. The explanation describes the SQL and its assumptions only; query rows never reach the model. The time budget is 3 s for the model, 5 s for the query and 8 s outer.
- **AC-FR-12-2:** Given generated SQL, when it is validated before execution, then anything other than a single SELECT over the role's allowed views is rejected. That includes DML, DDL, multiple statements, functions on the denylist and system catalogs. Execution uses a read-only database role on the replica with a 5 s statement timeout.
- **AC-FR-12-3:** Given a support agent asks for ledger revenue (views their role does not allow), when the question is processed, then the answer is "not available for your role", no SQL is executed, the names of views they cannot see are not revealed, and the denial is audited.
- **AC-FR-12-4:** Given any role, when a question asks for direct identifiers (names, phone numbers, emails), then they cannot be returned because no ask-the-data view contains them. Views are aggregate-first: support and operations get aggregate views and finance gets pseudonymous money rows keyed by `customer_ref`; row-level customer attributes are not exposed.
- **AC-FR-12-6:** Given analytics is not site-scoped in v1, when a role-allowed question spans all sites, then the answer is returned from aggregated or pseudonymous data. This is an accepted residual risk (D-24); the main API still enforces site scope.
- **AC-FR-12-5:** Given the model refuses, the SQL times out, or the per-user daily limit (assumption: 50 questions) is reached, when the request ends, then a localised reason is shown and the attempt is audited with question, SQL (if any) and outcome.

### FR-13 Operations dashboard
**Story:** As an operations engineer I want a near-real-time view of site status, revenue today and open alerts, so that I can see where to act at a glance.
- **AC-FR-13-1:** Given 200 concurrent staff users, when the dashboard loads, then the first render is served from cache in under 500 ms p95 and the staff app's first load is 250 KB or less gzipped (3G constraint; the dashboard is part of the staff app, D-44).
- **AC-FR-13-2:** Given a site changes status or an alert opens, when the event is published, then connected dashboards show the change through SSE within 15 s.
- **AC-FR-13-3:** Given "revenue today", when it is shown, then it is the sum of confirmed top-ups since 00:00 in each site's local time zone (Africa/Kinshasa UTC+1 or Africa/Lubumbashi UTC+2), per currency, never converted between currencies, and it matches the ledger within 60 s.
- **AC-FR-13-4:** Given the SSE connection drops or the data is more than 60 s old, when the user is viewing, then a "data as of hh:mm" stale banner appears and the client reconnects with backoff up to 30 s.
- **AC-FR-13-5:** Given a customer or an integrator key, when the dashboard endpoints are called, then the response is 403.

### FR-14 Reconciliation and exception queue
**Story:** As a finance officer I want provider statements matched against the ledger automatically, with an exception queue, so that I can close the month in one day with every difference explained.
- **AC-FR-14-1:** Given a provider statement CSV in the stub format, when it is uploaded, then rows are matched by provider transaction id. For 1.2 million rows the run finishes in 10 minutes or less, and a summary shows matched, unmatched and mismatched counts per currency.
- **AC-FR-14-2:** Given a file whose SHA-256 has already been imported, when it is uploaded again, then the import is refused with 409 `statement_already_imported`.
- **AC-FR-14-3:** Given rows that do not match, when the run completes, then exceptions are created with kinds `statement_only`, `ledger_only`, `amount_mismatch`, `duplicate_in_statement` or `currency_mismatch`, with status `open`, `pending_approval`, `resolved` or `written_off` (a note is mandatory on resolution). Fuzzy candidates (same amount and phone within ±10 min) are suggested but never auto-applied.
- **AC-FR-14-4:** Given an exception resolution that posts money (for example crediting a `statement_only` payment), or any manual adjustment or refund, when the officer submits it with a mandatory note, then it goes through the approvals resource: status `pending_approval` until an approver who differs from the maker by user and session approves the exact payload (a hash is bound to the approval). The threshold is zero in v1. Self-approval returns 403, and the posting uses the FR-04 at-most-once payment reference.
- **AC-FR-14-5:** Given every exception for a period is `resolved` or `written_off`, when the officer locks the period, then the lock is audited and FR-04 rejects later postings for that period. With open exceptions, the lock is refused with 409 and the count of open exceptions.
- **AC-FR-14-6:** Given a `match_to_top_up` resolution, when it is submitted, then it also requires provider evidence obtained by the system (a status pull or a statement fetched by a job), not an uploaded file alone.

### FR-15 RBAC and immutable audit log
**Story:** As an administrator I want each role to see and do only what it needs, and every sensitive action recorded unchangeably, so that we meet least privilege and can investigate incidents.
- **AC-FR-15-1:** Given the role-permission matrix (Customer, Support agent, Finance officer, Operations engineer, Administrator, Integrator), when any endpoint is called, then authorisation is enforced on the server. An automated test calls every endpoint as every role and compares the results with the matrix.
- **AC-FR-15-2:** Given a staff user without MFA enrolled, when they sign in, then they must enrol (company identity provider with enforced MFA preferred; TOTP for break-glass local accounts) before reaching any page. After 5 failed codes in 15 minutes the account locks for 15 minutes. WebAuthn is required for finance officers and administrators before go-live.
- **AC-FR-15-3:** Given an audit entry, when any role (including Administrator and the application database user) tries to update or delete it, then the database refuses. Each entry carries the hash of the previous one, and a daily job checks the hash chain and alerts on a break.
- **AC-FR-15-4:** Given writes, permission denials, logins, AI calls, data exports and key changes, when they happen, then each produces an audit entry within 1 s. 100% coverage is checked by a test of the event types.
- **AC-FR-15-5:** Given a customer signs in by one-time code on a shared phone, when the session is idle for 5 minutes (absolute lifetime 12 hours) or the browser tab closes, then the session ends and no balance or personal data stays in local storage.
- **AC-FR-15-6:** Given a maker proposes an action that requires approval (money-posting reconciliation resolutions, manual adjustments and refunds, tariff changes, role grants, user creation, MFA reset, API-key creation, erasure approval, data export, KB publication), when it is submitted, then it is stored in the `approvals` resource with the maker, a hash of the exact payload and, once decided, the approver, who must differ from the maker by user and session. The threshold is zero in v1, a changed payload invalidates the approval, and staff cannot approve money for customers linked to them (conflict check).
- **AC-FR-15-7:** Given a staff user with a valid session, when they approve, grant a role, create an API key, export data or approve erasure, then step-up authentication within the previous 5 minutes is required, otherwise the response is 401 with a step-up challenge.
- **AC-FR-15-8:** Given a staff user who lost their second factor, when they recover access, then they use a one-time backup code, or an administrator resets MFA and a second administrator must approve the reset. Both are audited.
- **AC-FR-15-9:** Given session rules, when sessions are issued, then the access token lives 10 minutes and is revocable, staff sessions end after 30 minutes idle or 8 hours absolute, and customer sessions after 5 minutes idle or 12 hours absolute.
- **AC-FR-15-10:** Given the administrator role, when an administrator calls any endpoint for money, ticket or customer content, or ask-the-data, then the response is 403 and the denial is audited. `finance_lead` and `dp_admin` are user attributes, and `kb:edit`, `kb:publish` and `export:bulk` are permissions, not roles.

### FR-16 English and French UI and AI output
**Story:** As a French-speaking agent or customer I want the whole product and its AI output in my language, so that I understand everything without translation.
- **AC-FR-16-1:** Given a user's locale (default `fr` for customers and new staff), when any screen, SMS or email template renders, then all strings come from the selected locale. The CI build fails if any key is missing in `fr` or `en`.
- **AC-FR-16-2:** Given an amount of 1250000 minor units of CDF, when it is displayed, then `fr` shows "12 500,00 FC" and `en` shows "CDF 12,500.00". Dates are DD/MM/YYYY in `fr`, and the site's time zone is shown on dashboards.
- **AC-FR-16-3:** Given AI output (draft, summary, ask-the-data explanation), when it is generated, then it is in the language required by the relevant FR. A language check on the output flags a mismatch for the agent instead of silently sending it.
- **AC-FR-16-4:** Given a customer-facing message written in `fr`, when it exceeds 160 GSM-7 characters as an SMS, then the template test fails at build time (assumption: single-segment SMS).

### FR-17 Knowledge-base management
**Story:** As a support lead (agent with KB-editor permission) I want versioned KB articles in FR and EN, so that AI drafts cite current, approved answers.
- **AC-FR-17-1:** Given an article, when it is edited after publication, then a new version is created in `draft`. Published versions are immutable, and citations keep pointing to the version they cited.
- **AC-FR-17-2:** Given the workflow draft → in_review → published → archived, when an author tries to publish their own draft, then the response is 403. A second approver (through the approvals resource) must approve, and the version records `published_by` and `approved_by`. The forbidden-promise lint applies even to amounts present in retrieved passages.
- **AC-FR-17-3:** Given a published article, when it is archived, then within 60 s it is excluded from AI retrieval. Hard deletion is not available.
- **AC-FR-17-4:** Given an article that exists only in FR, when a draft for an EN ticket is requested, then retrieval may use it but the draft is marked "source in French" for the agent.

### FR-18 Integrator API keys, scopes and rate limits
**Story:** As an administrator I want scoped, revocable keys with rate limits for each integrator, so that a leaked or misbehaving client cannot harm the platform.
- **AC-FR-18-1:** Given an admin creates a key (format `sgh_<env>_<keyid>_<secret>`) with scopes (for example `telemetry:write`, `tokens:ack`, `tickets:write`) and a maximum life of 180 days, when it is created, then the secret is shown once, only a peppered HMAC is stored, and creation needs step-up and a second approver and is audited.
- **AC-FR-18-5:** Given the two key kinds `gateway` and `integrator` with disjoint scopes, when a key is created, then a `gateway` key (telemetry, token acknowledgement, heartbeat) is bound to a set of sites and a mandatory source address range, there is one key per physical gateway, and a call from outside the range or for another site is rejected with 403.
- **AC-FR-18-2:** Given a key without the required scope, when it calls an endpoint, then the response is 403 `insufficient_scope`.
- **AC-FR-18-3:** Given a per-key limit (default per the tier table in `05-api-design.md`, which is authoritative), when it is exceeded, then the response is 429 with a `Retry-After` header and no processing happens.
- **AC-FR-18-4:** Given a key is revoked, when it is used more than 60 s after revocation, then the response is 401. When a key is rotated, the old and new keys both work for up to 24 h.

### FR-19 Notifications through a reliable outbox
**Story:** As a customer I want one SMS or email receipt for each event that concerns me, so that I trust my payment went through.
- **AC-FR-19-1:** Given a business change that requires a notification, when it commits, then the outbox row is written in the same transaction. If the transaction rolls back, nothing is sent.
- **AC-FR-19-2:** Given the SMS/email stub returns an error or times out (5 s), when delivery is retried, then retries use backoff (1 min doubling, max 8 attempts over about 4 h) and then the message moves to a dead-letter list visible to admins.
- **AC-FR-19-3:** Given an event delivered twice by the queue (at-least-once), when the consumer processes it, then the dedup key (event id + channel + recipient) ensures the customer gets one message.
- **AC-FR-19-4:** Given a message whose content came from an AI draft, when it is enqueued without an `approved_by` staff id, then the enqueue is rejected and an alert is raised (invariant 2).

### FR-20 Data export, retention and erasure
**Story:** As an administrator I want to fulfil customer data requests and enforce retention, so that we respect GDPR principles and DRC law while keeping the financial records we are legally required to keep.
- **AC-FR-20-1:** Given a verified customer data-access request, when an admin runs export, then a JSON + CSV bundle of that customer's profile, meters, top-ups, tickets and notifications is produced within 72 h after a second proof of identity and an approval. It is downloaded through a presigned link that expires after 60 seconds (a new link can be requested) and is audited.
- **AC-FR-20-2:** Given an approved erasure request that has passed a 7-day cooling-off period with a notice to the old contact, when it is executed by a data-protection administrator (`dp_admin`) with a second approver, then name, phone, email and ticket free text are pseudonymised in place (no crypto-shredding in v1). Ledger lines and amounts are kept with the pseudonymous id (financial retention, assumption: 10 years under OHADA accounting rules). Telemetry is unlinked from the person. The erasure log is replayed after any restore from backup.
- **AC-FR-20-3:** Given a customer with a non-zero wallet balance, unacknowledged tokens or an open FR-14 exception, when erasure is requested, then it is blocked with reason `balance_or_dispute_open` until resolved (see Q2).
- **AC-FR-20-4:** Given the retention table owned by `04-data-model.md` (raw telemetry 90 days in PostgreSQL then archived; tickets 3 years after closure; AI audit metadata 13 months; redacted prompt payloads 90 days; provider events 13 months; quarantined webhooks 30 days; ledger 10 years; audit log 7 years) **[open: legal sign-off]**, when the nightly job runs, then expired data is purged and the job's counts are audited.
- **AC-FR-20-5:** Given a staff bulk export (CSV of more than 1,000 rows from any screen or FR-12), when it is requested by a role without `export:bulk`, then the response is 403. Allowed exports are audited with row count.

---

## 5. Non-functional requirements

| ID | Target (shared context, sharpened where noted) | Verification |
|---|---|---|
| NFR-01 | Availability of the payment-confirmation path: valid provider webhooks are accepted and posted to the ledger (provider outages excluded). 99.9% monthly (43.2 min error budget); confirmation-to-ledger freshness is a separate SLO | Proven in production only (probe every 30 s on a 30-day window, real success ratio); before release the mechanisms are tested, not the number |
| NFR-02 | p95: webhook ack under 300 ms; read API under 200 ms; cached dashboard under 500 ms; ask-the-data under 8 s. Sharpened: telemetry batch ack under 300 ms, AI triage under 30 s, AI draft under 15 s | k6 load tests at the NFR-03 peak in CI nightly on a staging-sized environment; production histograms |
| NFR-03 | 300,000 meters/customers; 83 batches/s on average (333 readings/s) and about 417 batches/s at 5x peak (1,700 readings/s); a batch is one meter's hourly batch unless gateways aggregate (then rows per second is the only limit); 10 top-ups/s payday ×3 webhook retries = 30 webhooks/s; 200 concurrent staff | 30-min sustained peak test + 2-h soak at average; seeded 300k-customer dataset |
| NFR-04 | RPO ≤ 5 min (zero for committed ledger transactions with the synchronous standby); RTO ≤ 30 min for instance and zone failure, 2 hours for logical corruption or region loss | Quarterly restore drill from PITR backups to a clean environment, timed; drill report; the erasure log is replayed after restore |
| NFR-05 | Every posting sums to zero; a payment affects the ledger at most once | DB constraints; property-based tests; chaos test sending each webhook 1-50 times concurrently; nightly balance check (AC-FR-04-3) |
| NFR-06 | OWASP ASVS L2; staff MFA; least privilege; secrets manager; TLS 1.2+ in transit; AES-256 at rest | ASVS L2 checklist review; SAST/DAST and dependency scan in CI; role-matrix test (AC-FR-15-1); pre-handover penetration test |
| NFR-07 | Personal data minimised; redacted before reaching the model; erasure supported | Redaction unit tests on a 500-sample corpus with 0 raw phone/email leaks; prompt audit sampling; erasure E2E test |
| NFR-08 | Approved provider only; no training on company data; human review for money/customer-facing AI output; full audit of AI calls; prompts stored redacted only and subject to retention and erasure | Provider contract/data terms on file; tests for AC-FR-10-2 and AC-FR-19-4; audit coverage test |
| NFR-09 | Metrics, logs and traces for 100% of requests with a correlation id; SLO burn-rate alerts; AI cost and latency per feature | Trace sampling check in staging; alert fire drills; AI cost dashboard per feature |
| NFR-10 | WCAG 2.2 AA (portal and staff UI) | axe-core in CI with 0 serious issues; manual keyboard and screen-reader pass per release |
| NFR-11 | EN and FR everywhere; locale-aware money and dates | CI missing-key check; snapshot tests of formats (AC-FR-16-2) |
| NFR-12 | Company-owned accounts and repo; containers + IaC; no hard single-cloud dependency | Deploy the same IaC to two targets (e.g. GCP and a local k3s/compose) once before handover |
| NFR-13 | Budgets counted in dollars (token-equivalents shown) per ticket, per feature per day, per customer and per API key per day; spend visible per feature | Budget enforcement tests (AC-FR-09-4); daily cost report |
| NFR-14 | Runbooks for every alert; one-command local environment; automated dependency/security updates | New engineer reaches a running local stack in 30 min or less with one command; runbook link on every alert; Renovate/Dependabot enabled |

---

## 6. Release slices

| Slice | FRs | Why here |
|---|---|---|
| **R0 Walking skeleton** | FR-01, FR-15, FR-16 | Registry, identity/RBAC/audit and i18n are dependencies of everything. Retrofitting auth, audit and translation later is the most expensive kind of rework. Proves the three deployables, CI/CD, IaC and the observability baseline end to end with one screen in FR and EN. |
| **R1 Payments and ledger** | FR-02, FR-03, FR-04, FR-05, FR-19, FR-14 | Highest value (M1, M2, M3) and highest risk (money correctness). Reconciliation sits here, not later, because it is the safety net for webhook gaps from the first day payments flow. The outbox is needed for receipts. |
| **R2 Support and AI** | FR-08, FR-17, FR-09, FR-10, FR-11 | Support depends on R1's payment and token visibility. The KB must exist before drafting can cite it. AI comes after the ledger so that invariant 2 and the approval path are built on a stable audit base. |
| **R3 Analytics and dashboard** | FR-18, FR-06, FR-07, FR-13, FR-12 | Telemetry needs integrator keys first. Alerts and the dashboard consume telemetry and ledger data. Ask-the-data needs the read replica, role-scoped views and enough data to be useful. |
| **R4 Hardening and handover** | FR-20 | Erasure and retention need every data store to exist. R4 also carries the NFR work: peak load and soak tests, restore drill, pen test, accessibility audit, runbooks, handover training. |

Before R1, token issuance (FR-05) in R1 uses the meter simulator's ack endpoint, authenticated by a bootstrap key that becomes a full FR-18 key in R3.

---

## 7. Non-goals, assumptions, dependencies, risks, open questions

### 7.1 Non-goals (v1)
- Real mobile-money or SMS provider contracts.
- Real meter firmware.
- Commercial-tariff invoicing.
- Native mobile apps.
- Multi-operator tenancy.
- Currency conversion between USD and CDF.
- Lingala/Swahili UI.
- AI that acts autonomously: no auto-send, no write path, no agentic refunds.
- Customer-facing chatbot.
- Field technician dispatch/work-order management.

### 7.2 Assumptions
- **A1** Each wallet has one currency, fixed by plan. A top-up in another currency is rejected.
- **A2** Energy revenue is recognised when the token is issued, not when the top-up is made **[open: finance lead]**.
- **A3** Top-up limits and the 15-minute expiry are as stated in AC-FR-02-3 and AC-FR-02-5.
- **A12** The stack is Django and Next.js (ADR-0019 to ADR-0021, D-42 to D-46); this PRD stays technology-neutral.
- **A4** Meters report every 15 minutes and the gateway batches hourly, so meter offline is judged on `last_seen` older than 90 minutes. The site gateway sends a heartbeat every 5 minutes, and 15 minutes without one means `site_offline`.
- **A9** One customer per phone number in v1 (unique); the payer's number is stored separately on each top-up.
- **A10** The provider's retry horizon is 72 hours.
- **A11** Retention periods follow the single retention table owned by `04-data-model.md` **[open: legal sign-off]**.
- **A5** The provider supplies a stable unique event id and a transaction id that also appear on its statement.
- **A6** Financial records retention is 10 years and personal data follows the DRC Digital Code (Ordonnance-loi 23/010, 2023). Both need legal confirmation.
- **A7** Staff work in business hours (08:00-18:00 local). Ops has an on-call rotation.
- **A8** Baselines in section 1.4 are estimates to be measured in R0.

### 7.3 Dependencies
- **D1 Provider stub:** initiate, status poll, signed webhooks with configurable duplicates, delays, reordering and forged/stale signatures; monthly and daily statement CSV generator with injectable mismatches. Owner: backend. Needed at R1 start.
- **D2 Meter simulator:** 300,000 virtual meters, configurable intervals, late/duplicate batches, offline sites, token ack/no-ack, register tampering. Needed in R1 (tokens) and R3 (telemetry).
- **D3 SMS/email stub** with failure injection (R1). **D4 Inbound-email stub** (R2).
- **D5 Claude API account** owned by the company, with data-use terms (NFR-08) signed before R2.
- **D6 Cloud account and secrets manager** owned by the company (R0).
- **D7 Bilingual reviewer** for UI and KB content (R0 onward).

### 7.4 Risk register

| ID | Risk | L | I | Mitigation |
|---|---|---|---|---|
| R-01 | Double credit from concurrent webhook retries or webhook + reconciliation racing | M | H | DB unique payment reference (AC-FR-04-2); concurrency chaos test; reconciliation reuses the same posting path |
| R-02 | Real provider behaviour differs from the stub (signature scheme, statement format) | H | M | Adapter interface per provider; contract tests; statement parser configurable per provider |
| R-03 | AI draft with wrong tariff/refund info is approved by a tired agent | M | H | Citations required; `unverified` and `money_affecting` gates (AC-FR-10-3/4); weekly sample review of 5% of approved replies |
| R-04 | Personal data leaks to the model or ask-the-data | M | H | Redaction before prompt; no identifiers in views; audit sampling; redaction corpus test |
| R-05 | Ask-the-data returns wrong numbers that look plausible, or reads across all sites | M | M | SQL always shown; golden-question regression set (50 questions) per release; "verify with finance report" hint on money answers; aggregate-first role views and no query rows to the model; analytics not site-scoped is an accepted residual risk (D-24) |
| R-06 | Telemetry volume outgrows partitioned Postgres | M | M | Load test at 1,700/s in R3; TimescaleDB migration trigger defined in ADR |
| R-07 | AI cost overrun from default `claude-opus-5-5` at volume | M | M | NFR-13 dollar budgets per ticket, feature per day, customer and API key, with separate pools for drafts and ask-the-data and a triage floor; per-feature cost dashboard; measured cheaper-model routing later |
| R-08 | Ask-the-data p95 above 8 s because of model latency with thinking | M | L | Budget of 3 s model, 5 s query, 8 s outer; explicit low effort for SQL generation; prompt caching of the schema prefix; no second model call over rows; measure in R3 |
| R-09 | Shared customer phones expose balances or tickets | H | M | 5-min idle timeout, no local persistence (AC-FR-15-5); OTP per session |
| R-10 | Alert floods during site outages hide real issues | M | M | Site-level rollup and dedup (AC-FR-07-1/4) |
| R-11 | Low-bandwidth users cannot use the web app | M | M | first-load budgets of 130 KB gzipped (customer portal) and 250 KB (staff app); code splitting; testing on a throttled 3G profile |
| R-12 | Single maintainer after handover | H | M | Runbooks, one-command environment, modular monolith, training in R4 |
| R-13 | Legal retention vs erasure conflict | M | H | Pseudonymise, keep the ledger; legal review before R4 (Q3) |

### 7.5 Open questions for stakeholders
- **Q1** [open: finance lead] Revenue recognition on top-up or on token issuance (A2; the working decision is token issuance)?
- **Q2** What happens to the remaining wallet balance on erasure or account closure: refund through the provider, or forfeit after notice?
- **Q3** Confirm the legal retention periods and lawful basis under DRC law. Is a local data-residency requirement in force?
- **Q4** [open: wording by legal] The portal terms say staff may use AI to prepare replies. Is any further disclosure in the reply itself required?
- **Q5** Triage detects `sw` and `ln`; drafts are `fr` or `en` only with a translator warning. Do Lingala/Swahili-speaking customers need human translation workflows in v1.1?
- **Q6** Which provider(s) first, and their real webhook signature and statement formats?
- **Q7** Is there a dedicated analyst or support-lead role, or do those duties stay with Finance, Ops and Support (the administrator has no business data access, D-14)?
- **Q8** Does the company bear provider fees, or is the customer charged a fee on top (affects the `fees` posting)?
- **Q9** Default low-balance threshold and SMS opt-out rules.

---

## 8. Traceability seed

| FR | Primary persona(s) | Journey(s) | Slice |
|---|---|---|---|
| FR-01 | Ops engineer, Admin, Support | J1, J2 | R0 |
| FR-02 | Customer | J1 | R1 |
| FR-03 | Integrator (provider), Finance | J1, J3 | R1 |
| FR-04 | Finance | J1, J3 | R1 |
| FR-05 | Customer, Ops engineer | J1, J2, J4 | R1 |
| FR-06 | Integrator (meter gateway), Ops | J4 | R3 |
| FR-07 | Ops engineer, Customer | J4 | R3 |
| FR-08 | Customer, Support agent | J2, J4 | R2 |
| FR-09 | Support agent | J2 | R2 |
| FR-10 | Support agent, Customer | J2 | R2 |
| FR-11 | Support agent | J2 | R2 |
| FR-12 | Finance, Ops engineer | J5 | R3 |
| FR-13 | Ops engineer | J4 | R3 |
| FR-14 | Finance | J3 | R1 |
| FR-15 | Admin (all roles) | J1, J2, J3, J4, J5 | R0 |
| FR-16 | Customer, Support agent (all) | J1, J2, J3, J5 | R0 |
| FR-17 | Support agent (KB editor) | J2 | R2 |
| FR-18 | Admin, Integrator | J4 (gateway auth) | R3 |
| FR-19 | Customer | J1, J2, J4 | R1 |
| FR-20 | Admin, Finance | J3, J5 | R4 |

---

## Resolutions in revision 1

The seven challenges the product planner raised against the first draft of the shared context were resolved by the tech lead in section 7 of `00-shared-context.md`, or accepted or rejected as follows.

| # | Earlier challenge | Resolution |
|---|---|---|
| 1 | No analyst persona for J5 | Accepted as written: no seventh role. J5 is acted by finance (operations equivalent). D-13 and D-14 fix the roles; Q7 stays open for a later analyst role. |
| 2 | No KB-editor or support-lead role | Accepted: permissions inside a role (`kb:edit`, `kb:publish`), not roles (D-13). KB publication also needs a second approver and records the approver (D-36, D-15). |
| 3 | EN/FR only versus Lingala and Swahili customers | Accepted in part: triage detects `fr`, `en`, `sw`, `ln`, `other` (section 7.2, D-32); drafts stay `fr` or `en` with a translator warning (D-35). Lingala and Swahili UI remains a non-goal in v1. |
| 4 | Ask-the-data under 8 s with a thinking model | Resolved by D-24: model 3 s, query 5 s, outer 8 s, and query rows never reach the model (D-31). R-08 updated. |
| 5 | "Revenue today" with two time zones and two currencies | Accepted: per-site local midnight, per currency, no conversion (AC-FR-13-3); the contract's money rules agree (section 3, D-10). |
| 6 | Retention versus erasure | Resolved by D-29 and D-28: pseudonymisation in place, blocked while the wallet is non-zero or tokens are unacknowledged, one retention table owned by `04-data-model.md` **[open: legal sign-off]**. |
| 7 | FR-02 status lifecycle (`pending_unknown`, `credited_late`) | Resolved by D-05, D-06 and section 7.2: canonical statuses plus a boolean `late`; `credited_late` is rejected as a status name (D-01: the schema is the authority). |
