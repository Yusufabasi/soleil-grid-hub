# Soleil Grid Hub: shared context (the contract every document follows)

Status: design phase. Nothing is implemented. Every figure is an assumption unless marked
measured. "Soleil Grid" is a fictional mini-grid operator; this is a portfolio system-design
exercise shaped by the Terms of Reference for an AI & Internal Tools Lead, not a Nuru product.

## 1. What it is

**Soleil Grid Hub** is the internal platform of a prepaid solar mini-grid operator. Customers
top up their meter by mobile money; the hub confirms the payment exactly once, credits a
ledger, issues energy credit to the meter, ingests meter telemetry, runs a support desk with
Claude-assisted triage and reply drafting, answers staff questions in plain language over a
read-only, role-scoped database, and shows operations a live dashboard.

**Why this application.** It forces real system-design decisions: money correctness
(idempotency, double-entry ledger), bursty write ingestion (webhooks, telemetry), read-heavy
dashboards (caching), horizontal scaling behind a load balancer, async work (outbox and
queue), strict security and privacy, and AI features whose output must be bounded, audited and
human-reviewed where it touches money or customers.

### Skills the Terms of Reference ask for, and where this shows them

| ToR requirement | Where |
|---|---|
| Full-stack (TypeScript/Python, REST, cloud) | Django backend, Next.js + TypeScript frontend, containers deployable to GCP or AWS |
| Hands-on LLM / AI projects | AI gateway: triage, reply drafting, summaries, "ask the data" (FR-09 to FR-12) |
| Automation and integration | Mobile-money webhooks, notifications, reconciliation, outbox + queue |
| Sound data modelling and security | Ledger schema, RBAC, row-level security, threat model (NFR-05 to NFR-08) |
| Non-technical users, handover, maintenance | Docs, runbooks, SLOs, backups, EN/FR UI (NFR-09, NFR-11, NFR-14) |

## 2. Scope

**In v1:** FR-01 to FR-20 below. **Non-goals:** real mobile-money or SMS provider contracts
(simulated by a provider stub), real meter firmware (simulated by a meter simulator), invoicing
for commercial tariffs, mobile native apps, multi-tenancy across operators.

### Functional requirements (ids are fixed; documents elaborate, never renumber)

| ID | Requirement |
|---|---|
| FR-01 | Customer and meter registry (sites, plans, status) |
| FR-02 | Prepaid top-up: initiate with a mobile-money provider, confirm by webhook |
| FR-03 | Webhook ingestion: signature check, replay protection, exactly-once effect |
| FR-04 | Double-entry ledger and wallet balances |
| FR-05 | Energy-credit token issuance to the meter, with retry and reconciliation |
| FR-06 | Meter telemetry ingestion (batched readings, late and duplicate tolerant) |
| FR-07 | Alerts: meter offline, low balance, anomalous consumption |
| FR-08 | Support tickets from portal, API and inbound-email stub |
| FR-09 | AI triage of a ticket: category, urgency, language, personal-data flags |
| FR-10 | AI reply drafting with knowledge-base citations; human approval before sending |
| FR-11 | AI ticket and conversation summaries |
| FR-12 | "Ask the data": natural-language questions to read-only, role-scoped SQL, SQL shown |
| FR-13 | Operations dashboard: live site status, revenue today, alerts (cached, near real time) |
| FR-14 | Reconciliation of provider statements against the ledger, with an exception queue |
| FR-15 | Role-based access control and an immutable audit log |
| FR-16 | English and French UI and AI output |
| FR-17 | Knowledge-base article management (versioned) |
| FR-18 | Integrator API keys, scopes and rate limits |
| FR-19 | Notifications (SMS and email stubs) through a reliable outbox |
| FR-20 | Data export, retention and erasure (GDPR principles and DRC law) |

### Non-functional requirements (targets are assumptions to validate by load test)

| ID | Requirement | Target |
|---|---|---|
| NFR-01 | Availability of the payment-confirmation path, defined as: valid provider webhooks are accepted and posted to the ledger (provider outages excluded) | 99.9% monthly (43.2 min budget); confirmation-to-ledger freshness is a separate SLO. Proven in production only; before release the mechanisms are tested |
| NFR-02 | Latency (p95) | webhook ack under 300 ms; read API under 200 ms; cached dashboard under 500 ms; ask-the-data under 8 s |
| NFR-03 | Scale target | 300,000 customers and meters (100x today's 3,000), see capacity below |
| NFR-04 | Durability | RPO at most 5 minutes (zero for committed ledger transactions with the synchronous standby), RTO at most 30 minutes for instance and zone failure; 2 hours for logical corruption or region loss |
| NFR-05 | Ledger consistency | strict: every posting balances; a payment affects the ledger at most once |
| NFR-06 | Security | OWASP ASVS L2 as a baseline; MFA for staff; least privilege; secrets manager; encryption in transit and at rest |
| NFR-07 | Privacy | personal data minimised, never sent to the model unless required and redacted; erasure supported |
| NFR-08 | AI safety policy | approved provider only, no training on company data, a person reviews AI output that affects money or goes to a customer, every call audited; prompts are stored redacted only and follow the retention and erasure rules |
| NFR-09 | Observability | metrics, logs, traces for every request; SLO alerts; AI cost and latency per feature |
| NFR-10 | Accessibility | WCAG 2.2 AA |
| NFR-11 | Internationalisation | EN and FR everywhere, locale-aware money and dates |
| NFR-12 | Portability | company-owned accounts and repository; container and IaC based; no hard dependency on one cloud |
| NFR-13 | AI cost bounds | budgets counted in dollars (shown as token-equivalents): per ticket, per feature per day, per customer and per API key per day; spend visible per feature |
| NFR-14 | Maintainability | documented runbooks, one-command local environment, automated dependency and security updates |

### Capacity assumptions (design target, NFR-03)

300,000 meters reporting every 15 minutes = 28.8 million readings per day. Each meter sends one
batch an hour (4 readings), so the average is about 83 batches per second = 333 readings per
second, and the 5x peak is about 417 batches per second = 1,667 (about 1,700) readings per second
(corrected in revision 1: the first draft paired 1,700 readings per second with 83 batches).
If site gateways aggregate many meters into one batch, the request rate falls and rows per second
becomes the only limit. 300,000 customers x 4 top-ups a month = 1.2 million a month (about 0.5 per second
average, 10 per second on paydays; provider retries can triple webhook calls). About 6,000
tickets a month, 3 AI calls each. About 200 concurrent staff users.

## 3. Architecture decisions made up front (ADRs elaborate and may challenge them)

- **Modular monolith first**, three Django deployables from one backend codebase: `hub-api` (staff and
  customer API), `hub-ingest` (webhooks and telemetry, scaled and rate-limited separately),
  `hub-worker` (queue consumers and scheduled jobs), plus the Next.js frontend `hub-web` (revision 1:
  stack changed to the user's preference, D-42 to D-45). Extract a module into a service only when
  a measured trigger is hit.
- **Modules** (Python packages): `identity`, `customers`, `payments`, `ledger`, `meters`,
  `telemetry`, `support`, `ai_gateway`, `analytics`, `notifications`, `reconciliation`,
  `admin`, `shared`. Modules talk through service interfaces and domain events, not each other's tables.
- **Datastores:** PostgreSQL 16 (a primary, a synchronous HA standby and an asynchronous read replica, PgBouncer),
  Redis 7 (cache, rate limits, idempotency fast path, pub/sub for live updates), S3-compatible
  object storage (attachments). Telemetry in a time-partitioned Postgres table (revisit
  TimescaleDB by trigger).
- **Async:** transactional outbox in Postgres is the source of truth; a relay publishes to
  Redis Streams; consumers are idempotent and at-least-once.
- **Edge:** an L7 load balancer (nginx or HAProxy) terminates TLS, health-checks and spreads
  traffic over stateless replicas, with separate pools for `hub-api` and `hub-ingest`.
- **API:** REST + JSON described by OpenAPI 3.1 under `/api/v1`; Server-Sent Events for the
  live dashboard; inbound webhooks. GraphQL is not used (ADR).
- **Frontend:** Next.js (App Router) + TypeScript (strict), next-intl, TanStack Query in client components (D-44).
- **Backend:** Python 3.12, Django 5 with Django REST framework, psycopg 3; the database schema is SQL-first and the Django models are unmanaged (`managed = False`), see D-42 and D-43.
- **Auth:** short-lived JWT access tokens plus rotating refresh tokens; staff TOTP MFA;
  customers by phone one-time code; integrators by hashed, scoped API keys.
- **Money:** integer minor units plus ISO-4217 currency (USD, CDF); ledger accounts
  `customer_wallet`, `provider_clearing`, `revenue_energy`, `fees`, `suspense`,
  `chargeback_receivable`. Sign convention: `amount_minor > 0` is a debit, `< 0` a credit, and a
  wallet balance (money the customer holds) is the negative of the sum of its entries.
- **Ids and time:** UUIDv7 ids, UTC timestamps, snake_case in the database, API prefix `/api/v1`,
  event names like `payment.confirmed`.
- **Claude API:** Python `anthropic` SDK behind the `ai_gateway` module only. Default model
  `claude-opus-5-5` read from configuration; effort set explicitly per feature (thinking cannot be
  disabled on this model); no forced `tool_choice`, no assistant prefill; structured outputs for
  JSON results; prompt caching on the stable prefix; the `refusal` stop reason handled; server-side
  `fallbacks` enabled by default. Cheaper-model routing is a later, measured optimisation, not a
  starting assumption.
- **Invariants that no design may break:** (1) there is no model-controlled path to any write: the gateway's database role may only INSERT into `ai_runs`, and suggestions are written by module services after validation;
  (2) nothing AI-written reaches a customer without a staff approval; (3) a payment is applied to
  the ledger at most once however often the provider retries; (4) every ledger transaction sums
  to zero; (5) staff roles see only what their role allows, including through "ask the data".

## 4. Roles and personas

Customer (portal, FR/EN), Support agent, Finance officer, Operations engineer, Administrator,
Integrator (machine client, mobile-money provider and meter gateway).

## 5. Document map and ids

| File | Owner role |
|---|---|
| `00-shared-context.md` | Tech lead |
| `01-prd.md` | Product planner |
| `02-system-architecture.md` | System architect |
| `03-code-architecture.md` | Software architect |
| `04-data-model.md`, `db/schema.sql` | Database engineer |
| `05-api-design.md`, `api/openapi.yaml` | API designer |
| `06-ai-integration.md` | AI engineer |
| `07-security-threat-model.md` | Security engineer |
| `08-frontend-ux.md` | UX and accessibility |
| `09-devops-sre.md` | SRE |
| `10-test-strategy.md` | QA |
| `adr/NNNN-*.md` | Tech lead |
| `11-implementation-plan.md` | Tech lead |
| `12-review-notes.md` | Review round |

Ids: requirements FR-nn and NFR-nn; decisions ADR-nnnn; test cases `TC-<FR or NFR id>-n`
(for example TC-FR-03-2); risks R-nn; threats T-nn.

## 6. Glossary

**Top-up:** a customer payment that adds energy credit. **Token:** the credit instruction sent
to a meter. **Provider:** the mobile-money operator. **Posting:** one balanced set of ledger
lines. **Outbox:** a table written in the same transaction as the business change, drained to a
queue. **Idempotency key:** a client- or provider-supplied id that makes a repeated request a no-op.

## 7. Decisions of revision 1 (binding on every document and artefact)

These decisions resolve the challenges the specialists raised and the two review reports in
`review/` (consistency findings C-nn, adversarial findings A-nn and S-nn). Where a document or
artefact disagrees with this section, this section wins and the document changes. Open items that
need a business or legal answer are marked **[open]**.

### 7.1 Authority and naming

- **D-01 The database schema is the authority for every enumeration and state machine.** The
  OpenAPI enums are validated against `db/schema.sql` by an automated test; display labels in the
  UI are a separate mapping. (C-02, C-03, C-26, C-27)
- **D-02 One vocabulary for tables and streams.** Documents use the schema's names:
  `provider_events`, `top_ups`, `meter_tokens`, `ledger_entries`, `ledger_transactions`,
  `meter_readings`, `ai_runs`, `consumer_inbox`. `03-code-architecture.md` carries a mapping table
  from its DTO field names to columns. Event names follow the code architecture catalogue
  (`payment.confirmed`, `token.requested`, `token.issued`, `token.failed`, `notification.requested`,
  `ticket.triaged`, ...). Five stream families as in the system architecture; dead-letter streams
  are named `dlq.<stream>`. (C-22, C-23, C-25)
- **D-03 Ledger sign convention** is the one in section 3: debit positive, credit negative, wallet
  balance equals the negative of the sum of its entries. The API shows ledger lines with an explicit
  `direction` and a positive `amount_minor`. (C-05)
- **D-04 Routing.** Ingest paths are `/api/v1/webhooks/providers/*`, `/api/v1/telemetry/*` and
  `/api/v1/gateway/*` (gateway traffic is served by the telemetry pool, `be_telemetry`); the staff view of the webhook log is `/api/v1/webhook-events`. The balancer
  has five pools: `be_webhooks`, `be_telemetry`, `be_stream`, `be_api` and `be_web` (the Next.js
  frontend, serving `/` and `/_next/*`; `/api/*` goes to the Django pools on the same site). `hub-ingest` is one deployable run as two Deployments (webhooks, telemetry). Health
  endpoints are `/api/v1/health/live` and `/api/v1/health/ready`. (C-09, C-40)

### 7.2 Canonical state machines

| Entity | States (the only allowed values) | Notes |
|---|---|---|
| `top_ups.status` | `initiated`, `pending`, `pending_unknown`, `confirmed`, `failed`, `expired`, `cancelled`, `reversed` | Plus a boolean `late` set when a confirmation arrives after `expired` or `cancelled`. UI labels map these (for example `pending_unknown` is shown as "needs review"). |
| `meter_tokens.status` | `pending`, `sent`, `acknowledged`, `failed`, `cancelled`, `unknown` | "Retrying" is `pending` with `attempts > 0`. Every token references a ledger posting (NOT NULL), has a monotonic per-meter `counter` and an `expires_at`, and a resend reuses the same token id. |
| `alerts.kind` | `meter_offline`, `site_offline`, `low_balance`, `anomalous_consumption` | Offline rules: meter silent more than 90 minutes; site gateway heartbeat (every 5 minutes) missing 15 minutes. |
| `reconciliation_items.kind` | `statement_only`, `ledger_only`, `amount_mismatch`, `duplicate_in_statement`, `currency_mismatch` | status: `open`, `pending_approval`, `resolved`, `written_off`; resolution note is mandatory. |
| Reply drafts | Stored as `ticket_messages` with `author_type = 'ai_draft'`; message status `draft`, `approved`, `sent`, `rejected`, `recalled` | The API's generation states (`generating`, `ready`, `failed`, `refused`, `superseded`) map to `ai_runs.status` plus the message status. One permission name: `ticket.ai_approve`. (C-21) |
| `ai_runs.status` | `started`, `ok`, `refused`, `truncated`, `invalid`, `timeout`, `error`, `budget_blocked`, `guard_blocked` | `ai_runs.feature`: `triage`, `reply_draft`, `summary`, `ask_data`. |
| Triage vocabulary | category: `billing_payment`, `no_power`, `meter_fault`, `tariff_question`, `account_change`, `complaint`, `safety_hazard`, `other`; urgency `low`, `normal`, `high`, `critical` (P4 to P1); language `fr`, `en`, `sw`, `ln`, `other`; PII flags `phone`, `email`, `national_id`, `payment_reference`, `address`, `health` | (C-20) |

### 7.3 Money rules

- **D-05 Late confirmations are credited, never dropped.** A success webhook for an `expired` or
  `cancelled` top-up credits the wallet once and sets `late`. Only an amount or currency mismatch, a
  decommissioned meter or an erased customer routes the money to `suspense` with a reconciliation
  exception. (C-01, A-16)
- **D-06 Provider reversals** (`payment.reversed`) are posted as reversing entries. If the wallet no
  longer holds the money (energy already issued), the shortfall posts to `chargeback_receivable`
  and opens an exception. (A-16)
- **D-07 Token delivery.** Resend with the same token id on exponential backoff (30 s doubling,
  capped at 30 minutes) for up to 72 hours. Reverse the posting only on a definitive rejection or
  after reconciliation confirms the meter never applied the token; otherwise the token is
  `unknown` and raised as an exception. Token issuance is the single, explicitly approved write
  command to site systems: bound to meter id, counter and expiry; no remote disconnect and no remote
  configuration in v1. (C-04, A-03, A-14; Terms of Reference rule on read-only site access)
- **D-08 Tariff and meter snapshots.** A top-up stores the quoted tariff and `energy_wh` at
  initiation. Token issuance re-checks, inside its transaction, that the meter still belongs to the
  top-up's customer and is active. Tariffs are versioned with `effective_from`, changes need
  maker-checker approval and a maximum change per revision. Energy is rounded down to the watt-hour
  and the remainder stays in the wallet. (A-02, A-15)
- **D-09 Webhook processing is synchronous**: verify the signature, then in one transaction record
  the event in `provider_events`, post to the ledger, write the outbox row, and acknowledge. Invalid
  signatures are not stored in `provider_events` (a quarantine log only). Uniqueness is on
  `(provider, event_id)` and `(provider, provider_txn_id)`, in PostgreSQL only; Redis is a hint.
  The payer phone number is checked against the pending top-up; above a threshold the amount is
  confirmed by a pull from the provider's status API **[open: threshold, default every confirmation
  of 100 USD or more]**. The signed string is `timestamp.event_id.body`. (C-10, C-42, T-01)
- **D-10 Revenue recognition** happens at token issuance (sale of energy credit), pending the
  finance lead's confirmation **[open]**. What customers see as their balance is the energy credit
  (kWh) and the wallet remainder; the wallet is normally near zero after issuance. (C-37)
- **D-11 One customer per phone number** in v1 (unique), with the payer's number stored separately on
  each top-up. Shared phones are handled by short sessions. (database challenge 2)
- **D-12 Idempotency windows**: 7 days for top-ups, 24 hours elsewhere. (C-36)

### 7.4 Authorization and authentication

- **D-13 Principals.** Staff roles in the database are `support_agent`, `finance_officer`,
  `operations_engineer`, `administrator`. Customers authenticate separately. Machine principals are
  authentication schemes, not roles: `provider` (HMAC, one endpoint), `gateway` and `integrator` (API
  key kinds with disjoint scopes), `public`. Permissions inside a role (`kb:edit`, `kb:publish`,
  `export:bulk`) and user attributes (`finance_lead`, `dp_admin`) are not new roles. (C-28)
- **D-14 The administrator is a platform administrator**: users, roles, keys, configuration, audit.
  No money capability, no ticket or customer content, no ask-the-data, no top-up initiation. The
  threat model's matrix stands. One file, `api/permissions.yaml`, is the single source for
  `x-roles` in the OpenAPI document and for the schema's role-permission seed; a test fails when
  they differ. (C-08)
- **D-15 Maker-checker through one `approvals` resource.** An approval records the maker, a hash of
  the exact proposed payload, and the approver, who must differ from the maker (user and session). It
  is required for: every money-posting reconciliation resolution, manual adjustments and refunds,
  tariff changes, role grants, user creation, MFA reset, API-key creation, erasure approval, data
  export, and KB publication. The threshold is zero (always) in v1. `match_to_top_up` also requires
  provider evidence obtained by the system (status pull or statement fetched by a job), not an
  uploaded file alone. A conflict check (`staff_customer_link`) blocks staff from approving money
  for customers linked to them. (C-11, C-30, A-01, A-07)
- **D-16 Staff authentication.** The company identity provider over OIDC with enforced MFA is
  preferred; local accounts exist for break-glass and use TOTP; WebAuthn is required for
  `finance_officer` and `administrator` before go-live. Step-up authentication (within 5 minutes)
  guards approvals, role grants, key creation, export and erasure. MFA recovery: one-time backup
  codes; an administrator reset needs a second administrator. (C-30)
- **D-17 Sessions.** Access token: JWT signed with EdDSA, 10 minutes, with a `tv` token-version claim
  checked for revocation. Refresh token: opaque, rotating, with reuse detection per family and no
  grace window; for browsers it lives in an `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`
  cookie and is never in a response body (body transport only for non-browser clients). Staff idle
  30 minutes, absolute 8 hours; customer idle 5 minutes, absolute 12 hours. (C-12, C-13, A-05)
- **D-18 Live dashboard stream.** `POST /dashboard/stream-tickets` returns a single-use ticket valid
  30 seconds, bound to the caller's IP; `GET /dashboard/stream?ticket=` carries it in the query
  string (browsers cannot set headers on EventSource), which the balancer log format strips. A
  stream lives at most 10 minutes and the client requests a new ticket; the role and `tv` are
  re-checked on every heartbeat. (C-14, A-04)
- **D-19 API keys.** Format `sgh_<env>_<keyid>_<secret>`, stored as an HMAC with a server pepper,
  maximum life 180 days. Two kinds with disjoint scopes: `gateway` (telemetry, token
  acknowledgement, heartbeat; bound to a set of sites; mandatory source address range) and
  `integrator`. One scope list in the OpenAPI document. Default rate limit follows the tier table
  in `05-api-design.md`, which is authoritative. (C-31, C-33, A-13)
- **D-20 Gateways.** One key per physical gateway. The hub pushes tokens to the gateway through an
  outbound adapter; the gateway acknowledges with `POST /api/v1/gateway/tokens/{id}/acknowledge` and
  sends a heartbeat every 5 minutes. (C-32, C-39)
- **D-21 Customer one-time codes** are rate-limited per phone, address and network, restricted to an
  allow-list of country prefixes, protected by a proof-of-work or CAPTCHA step, never send an SMS to
  an unknown number (the response is always 202), count failures per challenge, and require
  re-verification after dormancy. Data export and erasure need a second proof of identity. (A-06)

### 7.5 Data, infrastructure and operations

- **D-22 Database roles and functions.** Roles: `hub_api`, `hub_ingest`, `hub_worker`,
  `audit_writer`, `ledger_owner` (no login, owns the definer functions), `migrator`, and
  `analytics_support`, `analytics_finance`, `analytics_ops`. Ledger writes go through a
  `SECURITY DEFINER` function `post_ledger_transaction`; `wallet_balances` and `ledger_accounts` are
  not directly writable by application roles; the immutability triggers are `ENABLE ALWAYS`.
  Definer functions pin `search_path` to `pg_catalog, hub, pg_temp`. `ALTER ROLE ... SET`
  (read-only default, statement timeout, idle timeout, temp-file limit) and connection limits are
  set on each analytics **login** role, with a test that checks them. PUBLIC loses CONNECT and TEMP.
  Row-level security is `FORCE`d and covers customers, tickets and ticket messages by site scope
  (`user_sites`) and the portal-visible money tables by customer. `erase_customer` validates the
  request (status, kind, customer, hold, two distinct approvers) and is owned by a non-superuser
  role. The audit log is append-only through `audit_append` with a per-stream hash chain
  (`stream`, `seq`, `prev_hash`, `hash`), a verifier job and a daily signed anchor. (S-01 to S-12, A-08)
- **D-23 Additional tables.** `refresh_tokens`, `customer_otp`, `mfa_recovery_codes`, WebAuthn
  credentials, `user_sites`, `approvals`, `staff_customer_link`, `accounting_periods` (with a
  posting guard and the `period_locked` error), `incidents` and `tickets.incident_id`,
  `attachments`, `notifications` and templates, `alert_rules`, `data_exports`,
  `reading_batches`, `consumer_inbox`, `ledger_balance_snapshots`, `gateways`, and a `version`
  column for optimistic concurrency on customers, meters and tickets. (section (b) of the consistency
  review)
- **D-24 Ask the data.** Per-role view schemas `analytics_support`, `analytics_finance`,
  `analytics_ops`; the administrator has none. Row-level customer attributes and phone digits are not
  exposed; support and operations get aggregate views; finance gets pseudonymous money rows. Site
  scoping is not enforced in the analytics path in v1 (accepted residual risk: it is a role-wide read
  of aggregated and pseudonymous data) and the main API enforces site scope. Time budget: model 3 s,
  query 5 s (`statement_timeout`), outer 8 s; PgBouncer enforces a query timeout. Query rows never
  reach the model. (C-06, C-07, S-01, S-06)
- **D-25 Telemetry.** Key `(meter_id, read_at)`; a batch has at most 1,000 readings or 256 KB;
  readings up to 7 days late are accepted and anything more than 5 minutes in the future is
  rejected per reading; `seq` is advisory; batch idempotency uses `reading_batches`. Monthly
  partitions in v1, weekly above 50,000 meters. Leave PostgreSQL for a dedicated telemetry store when
  hot data exceeds 500 GB, sustained ingest exceeds 3,000 rows a second, or insert latency breaks its
  target. (C-15, C-16, C-44)
- **D-26 Topology and recovery.** Primary, synchronous HA standby, asynchronous read replica. The
  standby is required from the 30,000-customer tier; at 3,000 customers promoting the replica is
  accepted with a documented RPO of seconds. Ledger transactions commit synchronously. (C-38)
- **D-27 Queue.** The outbox is the source of truth; the relay publishes to Redis Streams; consumers
  dedupe in `consumer_inbox`; a replay window of 10 minutes plus business sweepers covers Redis loss.
  Provider retry horizon assumed 72 hours; `provider_events` is kept 13 months. (C-23, architecture C3, C8)
- **D-28 One retention table**, owned by `04-data-model.md`: telemetry raw 90 days in PostgreSQL then
  13 months archived then hourly aggregates; tickets 3 years after closure then pseudonymised; AI audit
  metadata 13 months; redacted prompt payloads 90 days in object storage keyed by prompt hash; provider
  events 13 months; quarantined webhooks 30 days; logs 30 days hot and 90 cold; ledger 10 years; audit
  log 7 years; backups 35 days plus 12 monthly. **[open: legal sign-off]** (C-34)
- **D-29 Erasure** is pseudonymisation in place (ledger integrity); no crypto-shredding in v1. It is
  blocked while the wallet is non-zero or tokens are unacknowledged, needs a 7-day cooling-off period
  with a notice to the old contact, a data-protection administrator and a second approver. The
  erasure log is replayed after any restore from backup. (C-35, A-12)
- **D-30 Limits.** Attachments: 3 files of at most 5 MB (jpg, png, pdf), scanned, served from a
  cookie-less domain; message body 5,000 characters; statement uploads capped in size and rows with
  formula neutralisation. Rate limits: the tier table in `05-api-design.md`. SLO probe every 30
  seconds on a 30-day window. (C-41, C-43, A-10)

### 7.6 AI

- **D-31 Invariants** are quoted verbatim from section 3. Query rows never reach the model in v1
  (the explanation describes the SQL and its assumptions only). (C-06, C-45)
- **D-32 Triage** results are applied automatically as suggestions with an override record; this is
  an accepted risk guarded by a recall gate of at least 98% on `needs_human_now`, a low-confidence
  queue and staff override. Paging on-call from `needs_human_now` is rate-limited and confirmed by
  staff above a threshold. (C-20, A-17)
- **D-33 Fallbacks** are pinned to a list of approved models inside the approved agreement (not the
  provider-chosen default), only on synchronous calls; the served model is recorded in
  `ai_runs.served_model` and fallbacks are disabled for evaluation runs. (06 challenge 1, A-17)
- **D-34 Budgets** in dollars with token-equivalents shown: per ticket, per feature per day, per
  customer and per API key per day, with separate pools for drafts and ask-the-data and a triage
  floor. (C-18, A-17)
- **D-35 Drafts.** Money-affecting drafts need a finance co-approval (`money_affecting`); lints re-run
  on any text changed at approval; a 30-second recall window holds approved messages (outbox
  `available_at`, `POST /drafts/{id}/recall`). Customers are told in the portal terms that staff may
  use AI to prepare replies **[open: wording by legal]**. (A-09, A-10, 08 challenge C2, C3)
- **D-36 KB publication** needs a second approver and records `published_by` and `approved_by`; the
  forbidden-promise lint applies even to amounts present in retrieved passages. (A-09)
- **D-37 Prompt audit.** `ai_runs` records `served_model`, `fallback_ran`, `stop_category` and a
  `payload_ref`; the redacted prompt is stored in object storage keyed by hash and follows retention
  and erasure. The model alias `claude-opus-5-5` has no dated snapshot, so drift monitoring is
  required. (C-19, test-strategy challenge 4 and 5)
- **D-38 No test-only HTTP endpoint.** End-to-end setup uses a fixtures command against the test
  database. (test-strategy challenge 7)
- **D-39 The availability probe** for NFR-01 posts a signed webhook from a registered synthetic
  provider that credits a dedicated synthetic wallet every 30 seconds; this exercises the real path
  without a test-only endpoint. Synthetic activity is flagged and excluded from finance reports,
  revenue and dashboards **[open: finance accepts a synthetic wallet in production]**. (SRE)
- **D-40 Accounting periods.** Locking or reopening a period goes through maker-checker (D-15);
  the approver must hold the `finance_lead` attribute. A locked period rejects postings dated inside
  it (`period_locked`); a correction after the lock is a reversing posting dated in the open period.
  (UX, PRD AC-FR-04-5, AC-FR-14-5)
- **D-41 Triage hazard floor.** In code, after the model answers: text matching hazard terms (fire,
  smoke, electric shock, injury, exposed cable, in French and English) is never given an urgency below
  `high`, and the model's output can raise but never lower a ticket's urgency, close it or re-route it
  away from the safety queue. This narrows the accepted risk of D-32 together with the recall gate.
  Triage text is stored as a text hash on the suggestion. (security review T-53)

### 7.7 Stack and delivery model (added after the user stated their preferred stack)

- **D-42 Backend stack: Django 5 with Django REST framework.** Python 3.12 or newer, psycopg 3,
  gunicorn (sync or threaded workers) for `be_api`, `be_webhooks` and `be_telemetry`, uvicorn (ASGI)
  with async views for `be_stream` only. `ATOMIC_REQUESTS` is off; transactions are explicit
  (`transaction.atomic`), because the webhook path needs one precisely bounded transaction. The Django
  admin site is **not installed** in any deployed environment: it would bypass maker-checker (D-15).
  Authentication is custom (OIDC with the company identity provider, WebAuthn, TOTP, EdDSA JWT with
  PyJWT) over the `hub.users` table through a custom user model and authentication backend; Django's
  session and password machinery is not the staff login. Conformance to `api/openapi.yaml` is proven
  by schemathesis against the running application, not by generating the specification from code.
  The REST layer uses DRF serializers and viewsets behind a permission class generated from
  `api/permissions.yaml`.
- **D-43 Database mapping.** `db/schema.sql` (plus numbered SQL migrations, expand then contract) stays
  the authority (D-01). Django models are `managed = False` in each app and map to tables in the `hub`
  schema; the Django migration files contain `RunSQL` operations that apply the numbered SQL files;
  a test compares the models with the introspected database. The ledger is written only through the
  `SECURITY DEFINER` function `hub.post_ledger_transaction` (D-22). Each deployable has its own settings
  module and database role (`hub.settings.api`, `.ingest`, `.worker`, `.stream`, `.migrate`); the `search_path` is a
  role setting, not an application setting. Partitioned tables and bulk inserts use raw SQL or
  `COPY` through psycopg 3, not the ORM.
- **D-44 Frontend stack: Next.js (App Router) with TypeScript strict.** `hub-web` is a fourth deployable
  (Node, standalone output) behind the same balancer and the same site as the API, so the refresh cookie
  of D-17 keeps working unchanged. Authenticated screens are client components (the access token lives in
  memory, D-17); server components render public pages (login shell, public help from the knowledge
  base, tariffs, status). The Next.js server holds no secrets, never reads the database and does not
  proxy authenticated calls (a backend-for-frontend pattern is deferred to an ADR if measurement shows
  it is needed). Internationalisation with next-intl (`/fr` and `/en`); the API client is generated from
  `api/openapi.yaml` (openapi-typescript and openapi-fetch); Playwright and axe for end-to-end and
  accessibility tests; a strict Content-Security-Policy with nonces from middleware; the image
  optimiser is disabled (no remote images, so no server-side request forgery surface). Bundle budgets are
  revised for Next.js: customer portal first load at most 130 KB gzipped (the 90 KB figure assumed a Vite
  build with Preact), staff app at most 250 KB, both confirmed by an early spike **[open]**.
- **D-45 Delivery model: plan, then implement, then verify.** The design agents write documents only and
  never application code. An implementing agent works from `docs/plan/` slice by slice; the tech lead
  verifies afterwards against `docs/plan/99-verification.md`. The contract files are frozen for the
  implementer: `docs/00-shared-context.md`, `api/openapi.yaml`, `api/permissions.yaml`,
  `db/schema.sql`, `db/invariant_tests.sql` and `docs/adr/*`; a change to any of them follows the change
  request procedure in `docs/plan/00-executor-guide.md`.
- **D-46 CSRF stance.** Only two endpoints are authenticated by the refresh cookie: `POST
  /api/v1/auth/refresh` and `POST /api/v1/auth/logout`. Both require the `Origin` header to equal the
  site origin and the custom request header `X-Hub-CSRF: 1` (a non-simple header, so a cross-site form or
  simple request cannot send it), on top of `SameSite=Strict`. Every other endpoint authenticates with the
  bearer access token in the `Authorization` header and never with a cookie, so CSRF does not apply to
  them. Django's CSRF middleware is not used for the API. Next.js Server Actions are disabled.
- **D-47 OIDC code exchange.** The authorization-code exchange with the company identity provider happens
  in `hub-api` (a confidential client using PKCE and a state value bound to the browser); `hub-web` only
  starts the redirect and shows the result, holds no client secret and never sees the provider's tokens.
  The callback URL is on the API side of the same site. (security review T-78, ADR-0006)
- **D-48 No edge cache for HTML in v1.** Every page carries a per-request CSP nonce (D-44), so HTML is
  rendered per request and is never cached at the balancer; page data is cached inside the Next.js
  server with `fetch` revalidation, and `/_next/static` assets are cached immutably at the edge. A
  hash-based CSP for static public pages, which would allow an HTML micro-cache, is a spike option
  only if the measured render cost of public pages demands it. (UX review, architecture 3 and 4)
- **D-49 Operational details.** The migration job uses its own settings module `hub.settings.migrate`
  with the `migrator` role. The stream pool runs with the `hub_api` database role (it only checks token
  versions and permissions). `hub-web` releases carry forward the previous release's static chunks and
  set a Next.js deployment id so that open browser tabs survive a rollout; this is accepted without a
  separate ADR and is described in `docs/09-devops-sre.md`. (SRE)
- **D-50 Code-level additions accepted.** The finance co-approval of `money_affecting` drafts is an
  `approvals` action (it extends the list of D-15). A `hub.settings.test` module exists for tests. The
  Django app for the `admin` module uses the label `hub_admin`. The AI audit row is written on its own
  database alias so it commits independently of the caller's transaction; that alias must connect as a
  role that can only INSERT into `ai_runs` (the invariant of section 3), not as `hub_api`. (code architecture)
