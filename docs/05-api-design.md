# 05. API design: conventions, contracts and rationale

Owner: API designer. Status: revision 1 (binding decisions D-01 to D-38 of `00-shared-context.md`
section 7 are applied). The contract is `api/openapi.yaml` (OpenAPI 3.1, 164 operations,
validated with `openapi-spec-validator`). Roles per operation live in one file,
`api/permissions.yaml`; `x-roles` in the OpenAPI document is generated from it and
`tests/test_alignment.py` fails if they differ (D-14). Every enumeration follows `db/schema.sql`
(D-01); the same test compares them. This document holds what a schema cannot say: conventions,
error catalogue, idempotency, rate-limit tiers (authoritative for every other document),
webhook and SSE behaviour, authentication, approvals, and the FR-to-operation matrix.

## 1. Style decisions

**REST + JSON under `/api/v1`, not GraphQL.** The API is resource-shaped CRUD plus a few
money-moving commands where status codes, idempotency keys and per-operation limits matter. GraphQL
would hide those behind one endpoint and weaken per-operation rate limiting, role scoping and
caching. Flexible staff reads are "ask the data" (FR-12), which has its own safety envelope.

**Resources** are plural kebab-case nouns. Non-CRUD changes are sub-resource verbs
(`POST /top-ups/{id}/cancel`, `/drafts/{id}/approve`, `/drafts/{id}/recall`, `/tokens/{id}/retry`).
Durable things with their own lifecycle are collections (`/approvals`, `/data-exports`,
`/erasure-requests`, `/summaries`, `/ask-the-data/questions`, `/incidents`).

**Envelope.** Single resources: `{"data": {...}}`. Lists: `{"data": [...], "meta": {"has_next",
"next_cursor", "limit"}}`. Errors are problem documents (section 5).

**Values.** UUIDv7 ids; RFC 3339 UTC timestamps; money is `{"amount_minor": 500, "currency":
"USD"}`, integers only. Requests and ledger lines carry positive amounts and an explicit
`direction`; the database sign convention (debit positive, credit negative, wallet balance is the
negative of the sum, D-03) never leaks into the API. `Accept-Language` (`en`, `fr`) localises server
text; `locale` is stored on customers and users. Field names are snake_case. Where the schema column
name differs from the DTO name, the mapping table in `03-code-architecture.md` rules.

**Optimistic concurrency (one rule).** Customers, meters, tickets, drafts and KB articles carry
an integer `version` and a weak `ETag`. `If-Match` is **required** on every update of these
resources (the parameter is declared `required: true` in the spec): a stale value gives
`412 precondition_failed`, a missing one `428 precondition_required`. Other resources do not use it.

**Scoped reads, no shadow API.** A customer calling `GET /tickets` sees only its own rows because
the principal's id and role are set in the transaction and row-level security applies (T-03). The
same operation serves several roles; `x-roles` lists them. Every operation that takes a customer,
meter or ticket id carries two markers: `x-ownership` (what each principal class may reach: own
records for customers, key scope for integrators, bound sites for gateways) and `x-site-scoped`
(staff are limited to their assigned sites, `user_sites`). A record outside scope is `404`, never
`403`, so existence is not leaked; the generated authorization test (07 T-03) walks the markers.

## 2. Versioning and deprecation

URL-path version. Within `v1` only additive changes ship: new endpoints, optional request fields,
response fields, and new enum values only where the schema authority adds them (readers treat unknown
values as "other"). Renaming or removing a field, narrowing validation, changing auth or the meaning
of a code needs `/api/v2`. At most two majors live. A deprecated operation sends `Deprecation: true`
and `Sunset: <HTTP-date>` and is flagged `deprecated: true` in the spec; integrator-facing operations
get six months, staff-UI-only operations one release. After sunset: `410 endpoint_sunset`. CI diffs
the spec against the previous release with a breaking-change detector and generates the frontend
client from it. The webhook signature prefix `v1=` versions the signing scheme; a `v2=` would run
alongside during migration.

## 3. Routing and pools (D-04)

One public origin; the balancer routes by path prefix to four pools, recorded per operation as
`x-pool`:

| Pool | Paths | Deployable |
|---|---|---|
| `be_webhooks` | `/api/v1/webhooks/providers/*` | hub-ingest (webhooks Deployment) |
| `be_telemetry` | `/api/v1/telemetry/*`, `/api/v1/gateway/*` | hub-ingest (telemetry Deployment) |
| `be_stream` | `/api/v1/dashboard/stream` | hub-api (stream replicas) |
| `be_api` | everything else | hub-api |

The staff view of the webhook log is `/api/v1/webhook-events` (so a prefix rule can never send a
staff call to the ingest pool). Health is `/api/v1/health/live` and `/api/v1/health/ready`; `live`
checks only the process, `ready` checks PostgreSQL, Redis, object storage and queue and outbox lag
and returns `503` to leave the pool. AI degradation is reported `degraded` and never fails readiness,
so a model outage cannot take the payment-confirmation path out of rotation. There is no test-only
endpoint (D-38).

## 4. Authentication, sessions and step-up

| Scheme | Who | Notes |
|---|---|---|
| `bearerJwt` | staff, customers | EdDSA, **10 minutes**, claims `sub, typ, roles, scp, site_ids, locale, tv, acr, amr, auth_time, jti`. `tv` (token version) is checked for revocation. |
| `refreshCookie` / body | staff, customers | Opaque, rotating, **no grace window**, reuse detection per family. |
| `apiKey` | gateway and integrator keys | `sgh_<env>_<keyid>_<secret>`, HMAC with a server pepper, section 9. |
| `webhookHmac` | mobile-money provider | section 7. |
| `streamTicket` | browser EventSource | section 8. |

**Refresh token transport (D-17).** For browsers the refresh token is delivered and read **only**
as a cookie `sg_refresh; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`; it never appears in a
response body. Every token operation (`staffMfaVerify`, `confirmMfaEnrollment`,
`verifyWebauthnAssertion`, `verifyCustomerOtp`, `stepUp`, `refreshToken`) documents the
`Set-Cookie` response header. Non-browser clients send `X-Token-Transport: body` and receive and send
the token in JSON. `POST /auth/refresh` and `/auth/logout` read the cookie; the cookie is cleared on
logout and on reuse detection (`401 refresh_token_reused`, whole family revoked). Staff sessions: idle
30 minutes, absolute 8 hours. Customers: idle 5 minutes, absolute 12 hours; customers dormant for 90
days must re-verify.

**CSRF (D-46).** Only two operations are authenticated by the refresh cookie: `POST /auth/refresh`
and `POST /auth/logout`. Both require the request header `X-Hub-CSRF: 1` (declared as a required
parameter on exactly these two operations; a non-simple header that a cross-site form or simple request
cannot send) and an `Origin` header equal to the site origin, on top of `SameSite=Strict`. A failure is
`403 csrf_check_failed`. **Every other operation authenticates with the `Authorization` header only**;
no other operation accepts a cookie, so CSRF does not apply to them. Django's CSRF middleware is not
used for the API and Next.js Server Actions are disabled.

**Staff login (D-16).** The company IdP over OIDC is preferred and sits outside this API; the
local flow exists for break-glass and for sites without the IdP: `POST /auth/staff/login` (password,
never returns an access token) then `POST /auth/staff/mfa/verify` (TOTP or a single-use backup code)
or the WebAuthn pair (`/auth/webauthn/assertion-options`, `/auth/webauthn/assertions`).
`finance_officer` and `administrator` must enrol WebAuthn before go-live
(`/auth/webauthn/registration-options`, `/auth/webauthn/registrations`). Backup codes:
`POST /auth/staff/mfa/recovery-codes` regenerates ten hashed single-use codes. MFA loss for another
user is `POST /users/{id}/mfa-reset`, which is an approval that a **second administrator** must
approve (section 6). Wrong password and unknown account are indistinguishable.

**Step-up.** Operations marked `x-step-up` require `auth_time` within 5 minutes and `acr` at
least `mfa`. Otherwise `401 step_up_required` with `WWW-Authenticate: Bearer
error="insufficient_user_authentication", max_age=300` and `step_up.methods` in the body. The client
calls `POST /auth/step-up` (TOTP or WebAuthn assertion) and retries. Guarded: approving and
rejecting approvals, proposing money and platform approvals, draft approval, role grants, key
creation and rotation, key revocation, webhook replay, export, export download link, erasure, KB
publish, period lock, and registering WebAuthn or regenerating backup codes.

**Company IdP sign-in (D-47).** `GET /auth/oidc/start` answers 302 to the identity provider (PKCE, state bound to the browser by a short-lived cookie, nonce). `GET /auth/oidc/callback` (code exchange in `hub-api`, a confidential client) validates the ID token, maps it to a staff user and answers 302 to the web app with the `sg_refresh` cookie; provider tokens never reach the browser, failures are `application/problem+json`. Both are public and use the `auth` tier.

**Customer one-time codes (D-21).** `GET /auth/proof-challenge` returns a proof-of-work (or CAPTCHA)
challenge; `POST /auth/customer/otp/request` requires the solved `proof_token` and **always returns
202** with a challenge, whether or not the number is registered, and sends no SMS to unknown numbers.
Limits per phone, per address and per network, and an allow-list of country prefixes. Failures are
counted per challenge: five wrong codes kill it (`otp_invalid`), independent of the phone counter.

## 5. Errors

All errors are `application/problem+json` (RFC 9457): `type`, `title`, `status`, `detail`,
`instance`, a stable **`code`**, `request_id` and, for validation, `errors[]` of `{field, code,
message}`. Clients branch on `code` only. `detail` never contains stack traces, SQL, secrets or
personal data. `429` and `503` carry `Retry-After`. `step_up_required` also carries `step_up`;
`approval_required` carries `approval_id`.

Status conventions: `400` malformed (bad JSON, missing header, stale webhook timestamp); `401`
identity or recent-authentication failure; `403` not permitted; `404` missing or out of scope; `409`
state conflict; `412`/`428` concurrency; `413` oversize; `422` semantically invalid; `429` limits and
budgets; `500` without detail; `503` with `Retry-After`.

### Error catalogue

| Code | HTTP | Meaning and client action |
|---|---|---|
| `invalid_request` | 400 | Malformed JSON, wrong shape or unknown filter. |
| `idempotency_key_required` | 400 | Missing `Idempotency-Key` on an operation that requires it. |
| `invalid_cursor` | 400 | Expired or tampered cursor; restart. |
| `stale_timestamp` | 400 | Webhook timestamp more than 300 s from server time. |
| `unauthenticated`, `token_expired` | 401 | Missing or expired access token; refresh once. |
| `invalid_credentials` | 401 | Wrong email or password (also for unknown accounts). |
| `mfa_invalid`, `otp_invalid` | 401 | Wrong or expired second factor or code. |
| `refresh_token_reused` | 401 | Spent refresh token; family revoked; sign in again. |
| `invalid_signature` | 401 | Webhook signature mismatch; not retryable. |
| `step_up_required` | 401 | Re-authenticate through `/auth/step-up`, then retry. |
| `forbidden` | 403 | Role or attribute does not allow it. |
| `csrf_check_failed` | 403 | Cookie-authenticated call without `X-Hub-CSRF: 1` or with a foreign `Origin`. |
| `insufficient_scope` | 403 | API-key scope missing. |
| `site_not_permitted` | 403 | Outside the assigned or bound sites. |
| `self_approval_forbidden` | 403 | Approver equals maker (user or session). |
| `conflict_of_interest` | 403 | Approver is linked to the customer (`staff_customer_link`). |
| `not_found` | 404 | Missing or not visible. |
| `conflict`, `state_conflict`, `duplicate_resource` | 409 | Wrong state or natural-key clash. |
| `serial_conflict` | 409 | Meter serial already exists. |
| `idempotency_in_progress` | 409 | Same key still running; repeat the identical request. |
| `customer_has_balance` | 409 | Cannot close with balance or pending top-ups. |
| `period_locked` | 409 | Posting dated in a locked accounting period. |
| `approval_required` | 409 | The action needs an approved approval first. |
| `approval_payload_mismatch` | 409 | Payload hash differs from the approved one. |
| `money_co_approval_required` | 409 | Money-affecting draft lacks the finance co-approval. |
| `recall_window_closed` | 409 | The 30-second recall window has passed. |
| `statement_already_imported` | 409 | Same statement checksum already imported. |
| `erasure_blocked` | 409 | Wallet non-zero, unacknowledged tokens or legal hold. |
| `precondition_failed` | 412 | Stale `If-Match`. |
| `precondition_required` | 428 | `If-Match` missing. |
| `payload_too_large` | 413 | Telemetry batch over 1,000 readings or 256 KB; oversize body. |
| `validation_error` | 422 | Field errors in `errors[]`. |
| `amount_out_of_range` | 422 | Outside the plan min or max. |
| `currency_mismatch`, `currency_change_blocked` | 422 | Wrong currency, or plan currency change with a non-zero wallet. |
| `meter_not_active` | 422 | Top-up or token for a meter that is not active. |
| `idempotency_key_reuse` | 422 | Same key, different body. |
| `sql_rejected` | 422 | Generated SQL failed validation; never run. |
| `proof_required` | 422 | Missing or invalid proof-of-work or CAPTCHA token. |
| `attachment_rejected` | 422 | Disallowed type or size; ticket text still saved. |
| `evidence_required` | 422 | `match_to_top_up` without system-obtained evidence. |
| `draft_lint_failed` | 422 | Validators rejected the draft or the edited text. |
| `tariff_change_too_large` | 422 | Change beyond the maximum per revision. |
| `rate_limited`, `otp_rate_limited` | 429 | Over the tier limit; wait `Retry-After`. |
| `topup_limit` | 429 | More than 10 top-ups per meter per 24 hours. |
| `budget_exceeded` | 429 | Dollar budget reached (per ticket, feature, customer or key). |
| `endpoint_sunset` | 410 | Deprecated operation past sunset. |
| `internal_error` | 500 | Quote `request_id`. |
| `service_unavailable`, `ai_unavailable` | 503 | Overload or dependency down; back off. |

The catalogue is closed. Adding a code is additive; changing an existing code's status is breaking.

## 6. Approvals (maker-checker, D-15)

One resource, `/approvals`, serves every action that needs a second person. An approval records the
**maker**, the **exact proposed payload**, a **SHA-256 payload hash** and, later, the **approver**,
who must differ from the maker in user **and** session (`403 self_approval_forbidden`) and must pass
the `staff_customer_link` conflict check (`403 conflict_of_interest`). Approving requires the stored
`payload_hash` in the body; any change to the payload after proposal makes the hash fail
(`409 approval_payload_mismatch`). Approvals expire after 48 hours. The threshold is zero in v1:
always required.

| Kind (`approvals.kind`) | Proposed by | Dedicated operation | Approver |
|---|---|---|---|
| `reconciliation_resolution` | finance_officer | `resolveReconException` | finance lead (not linked to the customer) |
| `manual_adjustment` | finance_officer | `createLedgerAdjustment` | finance lead |
| `refund` | finance_officer | `createRefund` | finance lead |
| `tariff_change` | finance_officer | `createPlanTariff` | finance lead |
| `role_grant` | administrator | `replaceUserRoles`, `replaceUserSites` | second administrator |
| `user_creation` | administrator | `createUser` | second administrator |
| `mfa_reset` | administrator | `resetUserMfa` | second administrator |
| `api_key_creation` | administrator | `createApiKey`, `rotateApiKey` | second administrator |
| `data_export` | dp_admin (administrator) or customer | `createDataExport` | second administrator |
| `erasure` | administrator (dp_admin) | `approveErasureRequest` | second administrator, after cooling-off |
| `kb_publish` | support_agent or administrator | `publishKbArticle` | a support_agent or administrator other than the maker (support decides this kind only) |
| `draft_co_approval`, `draft_money_affecting` | n/a | `coApproveDraft` | finance lead (money claims only) |
| `period_lock`, `period_reopen` | finance_officer | `lockAccountingPeriod` (reopen has no operation yet) | finance lead |

Schema action names win (`approvals.action`): `recon.resolve`, `ledger.adjustment`, `ledger.refund`, `tariff.change`, `role.grant`, `user.create`, `mfa.reset`, `apikey.create`, `erasure.approve`, `data.export`, `kb.publish`; the kinds `period_lock`, `period_reopen`, `draft_money_affecting` and `draft_co_approval` have no schema action yet and must be added to the schema CHECK before storage. `api/permissions.yaml` also holds `approval_kinds` (who may decide each kind); `approveApproval` lists `support_agent` only so that support can decide `kb_publish`, and `x-approver-roles-by-kind` restricts every other kind.

The dedicated operations validate their own payload and return `202` with the pending `Approval`;
`POST /approvals` is the generic equivalent. The operations that move money return an approval and
never post directly: **no ledger write endpoint exists**. `approveApproval` executes the action in the
same transaction (for money kinds through the single posting function), and records the result in the
audit log. Approving is itself step-up guarded. Note the roles on `approveApproval` are
`support_agent`, `finance_officer`, `operations_engineer` and `administrator`; which kinds a caller may decide is a
permission check inside the use case (the approval kinds above), not an extra role.

Related money operations: `POST /ledger/adjustments` and `POST /refunds` (new in revision 1);
`POST /plans/{id}/tariffs` and `GET /plans/{id}/tariffs` (tariffs are versioned with `effective_from`,
never edited, and a top-up quotes and stores the tariff at initiation, D-08);
`GET /accounting-periods` and `POST /accounting-periods/{id}/lock` (lock refused while exceptions are
open; afterwards postings dated in the period fail with `409 period_locked`);
`POST /reconciliation/exceptions/{id}/evidence` (queues a provider status pull) so that
`match_to_top_up` has system-obtained **evidence**; `confirm_non_delivery` (reconciliation confirms the meter never applied the token, D-07) is an additional resolution (`evidence_id`, `422 evidence_required` without it).

## 7. Idempotency, top-ups, webhooks and tokens

### 7.1 Client idempotency

Operations that create money-relevant state or side effects take `Idempotency-Key`. **Required**:
`POST /top-ups`, `/plans/{id}/tariffs`, `/ledger/adjustments`, `/refunds`,
`/accounting-periods/{id}/lock`, `/reconciliation/statements`,
`/reconciliation/exceptions/{id}/resolve`, `/webhook-events/{id}/replay`, `/approvals` and its
`approve`, `/drafts/{id}/approve`, `/tickets/{id}/drafts`, `/notifications`, `/users`,
`/users/{id}/roles`, `/users/{id}/sites`, `/users/{id}/mfa-reset`, `/api-keys` and `rotate`,
`/kb-articles/{id}/publish`, `/data-exports`, `/erasure-requests` and its `approve`. Optional where a
repeat is merely untidy. Semantics are the same everywhere: 16 to 128 characters; scoped to the
principal and operation; same key and body returns the stored response with `Idempotent-Replayed:
true`; same key with a different body is `422 idempotency_key_reuse`; same key while running is `409
idempotency_in_progress`; a `5xx` stores nothing. **Windows: 7 days for top-ups, 24 hours elsewhere**
(D-12). PostgreSQL is the durable record; Redis is a fast path only.

### 7.2 Top-up lifecycle

`POST /top-ups` is available to the **customer** (own meter) and to an **integrator key** with
`topups:write`; no staff role initiates (support N, finance N, administrator N, D-14). It quotes and
stores the tariff and `energy_wh`, returns `202`, and the provider request carries our top-up id so the
idempotency record exists before the provider call. States (D-01): `initiated`, `pending`,
`pending_unknown` (provider outcome unknown after timeout, shown to staff as "needs review"),
`confirmed`, `failed`, `expired`, `cancelled`, `reversed`, plus the boolean `late`. Limits: plan min
and max (`amount_out_of_range`) and 10 top-ups per meter per 24 hours (`429 topup_limit`).

`POST /top-ups/{id}/cancel` works only while nothing has been confirmed. **A success confirmation
that arrives for an `expired` or `cancelled` top-up credits the wallet once and sets `late`** (D-05);
it is never dropped. Only an amount or currency mismatch, a decommissioned meter or an erased customer routes
the money to `suspense` and opens a reconciliation exception. `payment.reversed` posts a reversing
entry; if the energy was already issued, the shortfall goes to `chargeback_receivable` and an
exception opens (D-06).

### 7.3 Provider webhook contract

`POST /api/v1/webhooks/providers/{provider}` on `be_webhooks`; provider is `mpesa_cd`, `airtel_cd` or
`orange_cd` (simulated by the stub), or `synthetic` (the staging availability probe only, D-39).

| Header | Content |
|---|---|
| `X-Webhook-Id` | provider event id; must equal `body.event_id` |
| `X-Webhook-Timestamp` | unix seconds at signing |
| `X-Webhook-Signature` | `v1=` + hex HMAC-SHA256 |

**Signed string (D-09): `timestamp.event_id.body`**, that is `HMAC_SHA256(secret, timestamp + "." +
event_id + "." + raw_body)` over the raw bytes, compared in constant time. Per-provider secrets, 24-hour
dual-secret overlap on rotation. Replay window 300 s (`400 stale_timestamp`); bad signature
`401 invalid_signature`, and an invalid request is **not** stored in `provider_events` (a quarantine
log keeps it 30 days).

**The webhook is deduplicated by the provider event id in the body**, enforced by unique constraints
on `(provider, event_id)` and `(provider, provider_txn_id)` in PostgreSQL (Redis is only a hint). It
is **not** deduplicated by `Idempotency-Key`: providers do not send one and the contract must not
depend on it. Processing is synchronous: after verification, one transaction records the event in
`provider_events`, posts to the ledger and writes the outbox row, then the handler returns `200
{"status": "accepted"|"duplicate"}` within the 300 ms p95 target. The payer phone number is checked
against the pending top-up, and confirmations of 100 USD or more (threshold **[open]**) are confirmed
by a pull from the provider status API. A `5xx` means nothing committed, so provider retries (assumed
horizon 72 hours) are always safe; `200 duplicate` means it committed earlier; unknown references are
acknowledged and parked for reconciliation. Out-of-order delivery never downgrades a confirmed top-up;
it becomes an exception.

Operator tooling: `GET /webhook-events` (finance, operations) shows stored events and
processing status (`received, processed, ignored, failed, dead`); `POST /webhook-events/{id}/replay`
re-drives a stored event through the same exactly-once path (step-up, audited, a no-op if the posting
exists).

### 7.4 Tokens and gateways (D-07, D-20)

Token states: `pending`, `sent`, `acknowledged`, `failed`, `cancelled`, `unknown` ("retrying" is
`pending` with `attempts > 0`). Every token references a ledger posting, has a monotonic per-meter
`counter` and an `expires_at`; a resend reuses the **same token id** (30 s doubling, cap 30 minutes, up
to 72 hours). `POST /tokens/{id}/retry` (operations) triggers the same resend. The hub pushes tokens
through an outbound adapter; the gateway answers with `POST /gateway/tokens/{id}/acknowledge`
(`applied`, `rejected`, `counter_replayed`). A definitive rejection reverses the posting; otherwise an
unacknowledged token is `unknown` and raised as an exception. There is no remote disconnect and no
remote configuration in v1. Gateways send `POST /gateway/heartbeat` every 5 minutes; a missing
heartbeat for 15 minutes raises `site_offline`.

### 7.5 Telemetry (D-25)

`POST /telemetry/batches` takes at most **1,000 readings or 256 KB** (`413 payload_too_large`). The
dedupe key is `(meter, recorded_at)`; `seq` is advisory; `batch_id` idempotency uses `reading_batches`.
Readings up to **7 days late are accepted** (older than one hour are counted `late`); readings **more
than 5 minutes in the future are rejected per reading** (`in_future`), as are those older than 7 days
(`too_old`), unknown meters and meters outside the key's bound sites (`site_not_permitted`). One bad
reading never fails the batch. The response is `202`; aggregates run asynchronously. A gateway that
gets `503` or `429` backs off and resends the identical body.

## 8. Server-Sent Events (D-18)

`GET /api/v1/dashboard/stream` returns `text/event-stream` on `be_stream`. Browsers cannot set headers
on `EventSource`, so the client first calls `POST /dashboard/stream-tickets` and receives a **single-use
ticket valid 30 seconds, bound to the caller's IP**, then opens `/dashboard/stream?ticket=...`; the
balancer log format strips the query string. A stream lives **at most 10 minutes**; the server then
sends `resync_required` (reason `max_age`) and closes, and the client requests a new ticket. Role
and token version `tv` are re-checked on every heartbeat; a revoked or changed principal gets
`resync_required` (`role_changed`) and is closed. Limit: 3 concurrent streams per user. A client that cannot send `Last-Event-ID` may pass the optional `last_event_id` query parameter (replay within the 5-minute buffer).

| Event | Payload | When |
|---|---|---|
| `snapshot` | `DashboardSummary` | first, and after resync |
| `site.status_changed` | site id, computed health, meters online and total | health or count changes |
| `revenue.updated` | revenue today per currency | at most 1 per second; withheld without revenue access |
| `alert.raised`, `alert.updated` | `Alert` | open, acknowledge, resolve |
| `heartbeat` | server time | every 20 seconds |
| `resync_required` | reason `cursor_too_old`, `server_restart`, `max_age`, `role_changed` | state must be rebuilt |

Reconnect: `Last-Event-ID` replays from a 5-minute buffer, else `resync_required` then a snapshot.
The Next.js client falls back to polling `GET /dashboard/summary` (cached 5 s, `ETag`). Site health
(`operational`, `degraded`, `offline`) is a **computed** field derived from the gateway heartbeat and
meter silence; the stored site `status` is the registry lifecycle (`planned`, `active`,
`decommissioned`).

## 9. API keys (D-19)

Format `sgh_<env>_<keyid>_<secret>`, stored as HMAC-SHA256 with a server pepper, shown once, expiry
**required and at most 180 days**. Two kinds with **disjoint scopes** and **one scope list**:

| Kind | Scopes | Extra constraints |
|---|---|---|
| `integrator` | `customers:read`, `meters:read`, `topups:read`, `topups:write`, `tickets:read`, `tickets:write`, `tickets:inbound` | optional CIDR allow-list; ticket reads limited to tickets created by the key |
| `gateway` | `telemetry:write`, `tokens:acknowledge`, `gateway:heartbeat` | **bound to a set of sites**, **mandatory CIDR range**, one key per physical gateway |

`tickets:inbound` is dedicated to the inbound-email operation. Creating or rotating a key is an
approval (second administrator, step-up); `ApiKeyCreate` needs a non-empty `site_ids` or `all_sites: true`, and `all_sites: true` (integrator keys only) needs one more approver; the secret is returned once, only to the maker, as `result.api_key_secret` on `GET /approvals/{id}`; scopes, sites and expiry can never be widened by `PATCH`.
Revocation takes effect within 30 seconds. Default tier follows the table below.

## 10. Rate-limit tiers (authoritative for every document)

Enforced in Redis (sliding window) at the edge middleware, in addition to the balancer's coarse
limits. Every response carries `RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset`
(seconds); `429` adds `Retry-After`. The tier is recorded per operation in `x-rate-limit-tier`.
Numbers are starting assumptions to tune from the load test.

| Tier | Limit | Key | Applies to |
|---|---|---|---|
| `anonymous` | 30/min | IP | other unauthenticated calls |
| `auth` | 10/min per IP; 5 per 15 min per account or phone; OTP SMS also per network | IP and identity | login, MFA, WebAuthn, OTP, refresh, step-up |
| `customer` | 60/min | customer | customer-callable operations |
| `staff` | 300/min | user | staff operations |
| `payment` | 20/min; 10 top-ups per meter per 24 h; 5 per payer number per hour | principal | top-ups, adjustments, refunds, resolutions |
| `ai` | 20/min plus dollar budgets | user | triage, drafts, summaries |
| `ask` | 10/min plus budget | user | ask-the-data |
| `export` | 5/hour | user | exports and links |
| `integrator` | 600/min (standard default); 3,000/min (high) | key | integrator-key operations |
| `gateway` | 300/min | key | telemetry, heartbeat, token acknowledgement |
| `webhook` | 6,000/min, burst 200 | provider | provider webhooks |
| `stream` | 3 concurrent | user | dashboard stream |
| `probe` | exempt | n/a | `/health/*` |

Webhooks are limited by provider, never by customer: rejecting a legitimate confirmation to protect a
limit would break NFR-01, so overload on ingest sheds telemetry first (`503` with `Retry-After`).

## 11. AI endpoints (D-31 to D-37)

* **Triage** (`GET`/`POST /tickets/{id}/triage`): applied automatically as a **suggestion** with an
  override record; staff override with `POST /tickets/{id}/triage/override` (reason mandatory). The
  vocabulary is the canonical one: categories `billing_payment, no_power, meter_fault, tariff_question,
  account_change, complaint, safety_hazard, other`; urgency `low, normal, high, critical`; languages
  `fr, en, sw, ln, other`; PII flags `phone, email, national_id, payment_reference, address, health`.
  `needs_human_now` paging is rate-limited and staff-confirmed above a threshold.
* **Drafts** are `ticket_messages` with `author_type = ai_draft`; the API shows `generation_status`
  (`generating, ready, failed, refused, superseded`, mapped to `ai_runs.status`) and `message_status`
  (`draft, approved, sent, rejected, recalled`). Only `approveDraft` makes a draft customer-visible
  (permission `ticket.ai_approve`, step-up). Validators (lints) re-run on every edit and on any text
  changed at approval. A `money_affecting` draft needs a **finance co-approval**: the co-approver
  uses `GET /drafts/{id}/money-claims` and `POST /drafts/{id}/co-approve` and sees only the money
  claims, never the conversation (the single, explicit exception to "finance sees no tickets"). After
  approval the message is held 30 seconds; `POST /drafts/{id}/recall` cancels it inside that window
  (`409 recall_window_closed` afterwards).
* **Summaries** are staff-only and never sent to customers.
* **Ask the data**: roles support, finance and operations, each bound to its own view schema; **no
  administrator**. Outer budget 8 s (model 3 s, query 5 s `statement_timeout`); `201` within budget,
  `202` otherwise. Single SELECT over role views, 1,000-row cap; unsafe SQL is `422 sql_rejected` and is
  never run. **Query rows never reach the model**; the explanation describes the SQL and its
  assumptions only. Each user sees only their own history.
* **Usage and audit**: `GET /ai/usage` shows spend in **dollars** (token-equivalents for display) with
  caps per ticket, per feature per day, per customer and per API key per day; `GET /ai/runs` shows
  audit metadata of calls (`feature` in `triage, reply_draft, summary, ask_data`; `status` in
  `started, ok, refused, truncated, invalid, timeout, error, budget_blocked, guard_blocked`; served
  model, fallback flag, stop category). Prompts are never returned.

## 12. Support, attachments, incidents, notifications, privacy

**Tickets and email.** `POST /tickets/inbound-email` needs scope `tickets:inbound` and carries
`auth_results` and `envelope_from`: failed DMARC or SPF is quarantined; a reply is appended only when
the sender matches the ticket's customer, otherwise a new ticket opens; a repeated `message_id` returns
`{status: duplicate}` with **no ticket body or id**. Message body limit 5,000 characters.

**Attachments** (`POST /tickets/{id}/attachments`, `GET /tickets/{id}/attachments`,
`GET /attachments/{id}/link`): at most 3 files per message, 5 MB each, jpg, png or pdf checked by
content; rejected files return `422 attachment_rejected` while the text is saved; files are scanned
(`scan_status`: `pending, clean, infected, failed`) and downloaded only through a 60-second link on a
cookie-less domain after a clean scan.

**Incidents** (`/incidents`, `POST /incidents/{id}/tickets`): link up to 100 tickets to one incident;
tickets outside the caller's site scope are skipped and reported.

**Notifications** use templates only: `POST /notifications` accepts template-defined parameters, never
free text, and a per-agent daily cap applies (`429`). Delivery is through the outbox, at least once.

**Privacy (D-29).** Export and erasure are for **`dp_admin` administrators** (a user attribute),
with step-up, one data subject per request and approval by a second administrator; customers submit
requests with a **second proof of identity**. Exports have no standing URL: `POST
/data-exports/{id}/link` issues a **60-second** presigned link (audited, weekly report). Erasure:
`verify` (identity) starts a 7-day **cooling-off** with a notice to the old contact; `approve` is an
approval after it ends; it is **blocked** (`409 erasure_blocked`, `blockers[]`) while the wallet is
non-zero or tokens are unacknowledged, and a legal hold sets `blocked_by_retention`. Erasure is pseudonymisation
in place and the erasure log is replayed after any restore. KB publication is an approval too (D-36).

## 13. Roles and `api/permissions.yaml`

`permissions.yaml` has one top-level key, `operations:`, mapping every `operationId` to a list of
roles from: `support_agent`, `finance_officer`, `operations_engineer`, `administrator`, `customer`,
`integrator`, `gateway`, `provider`, `public`. The machine principals (`integrator`, `gateway`,
`provider`, `public`) are authentication schemes, not database roles (D-13). Permissions inside a role
(`kb:edit`, `kb:publish`, `customer:close`, `ticket.ai_approve`) and user attributes (`finance_lead`,
`dp_admin`) are not roles; they appear as `x-permission` and `x-requires-attribute`.

The mapping agrees with the RBAC matrix of `07-security-threat-model.md` section 4.6:

| Capability | Customer | Support | Finance | Operations | Administrator | Integrator or gateway |
|---|---|---|---|---|---|---|
| Own wallet, top-ups | own | none | read | none | none | `topups:*` |
| Initiate top-up | own | none | none | none | none | `topups:write` |
| Tickets | own | assigned sites | none | technical, pseudonymised | none | `tickets:*` |
| Customer profile | own | masked | as needed | **pseudonymous only** | none | `customers:read` |
| AI drafts | none | approve | co-approve money claims only | none | none | none |
| Ledger, adjustments, refunds, periods | none | none | yes (maker; lead approves) | none | none | none |
| Reconciliation | none | none | yes | none | none | none |
| Meter status and readings | own | status | none | yes | none | `meters:read`, gateway `telemetry:write` |
| Ask the data | none | `analytics_support` | `analytics_finance` | `analytics_ops` | **none** | none |
| KB | none | edit, propose | none | none | edit, publish by approval | none |
| Users, roles, keys | none | none | none | none | by approval | none |
| Audit log | none | none | none | none | all, read only (`audit.read`) | none |
| Export and erasure | request | none | none | none | dp_admin | none |

**D-14 rule (tested):** `administrator` appears on no operation whose `x-fr` contains FR-02, FR-04,
FR-08, FR-09, FR-10, FR-11, FR-12 or FR-14. The generic approvals operations are therefore tagged
FR-15, FR-17, FR-18 and FR-20 only, and the administrator has no ask-the-data, ticket, customer-content
or top-up operation. An approval for money kinds can only be decided by a finance lead at the use-case
level even though `approveApproval` lists three roles.

## 14. FR to operations matrix

Every FR has HTTP operations. FR-16 is cross-cutting (`Accept-Language`, `locale` fields). The two
health probes have no FR and carry `x-nfr` (NFR-01, NFR-09) instead.

| FR | Operations |
|---|---|
| FR-01 | `/customers` (list, create, get, patch, close), `/customers/{id}/meters`, `/sites`, `/plans` and `/plans/{id}/tariffs`, `/meters` (list, create, get, patch, decommission) |
| FR-02 | `POST /top-ups` (idempotent), `GET /top-ups[/{id}]`, `POST /top-ups/{id}/cancel`, `POST /webhooks/providers/{provider}`, `POST /refunds` |
| FR-03 | `POST /webhooks/providers/{provider}`, `GET /webhook-events[/{id}]`, `POST /webhook-events/{id}/replay` |
| FR-04 | `/ledger/accounts`, `/ledger/transactions`, `GET /customers/{id}/wallet`, `POST /ledger/adjustments`, `POST /refunds`, `/accounting-periods`, tariff operations, reconciliation resolve, draft co-approval. No ledger write endpoint. |
| FR-05 | `/tokens`, `POST /tokens/{id}/retry`, `GET /meters/{id}/tokens`, `POST /gateway/tokens/{id}/acknowledge` |
| FR-06 | `POST /telemetry/batches`, `GET /meters/{id}/readings`, `POST /gateway/heartbeat` |
| FR-07 | `/alerts` (list, get, acknowledge, resolve), `/alert-rules`, `POST /gateway/heartbeat` |
| FR-08 | `/tickets` (list, create, get, patch), messages, `POST /tickets/inbound-email`, attachments, `/incidents` |
| FR-09 | triage get, re-run, override; `POST /tickets` (auto-triage); `/ai/usage`, `/ai/runs` |
| FR-10 | drafts (request, list, get, edit, approve, reject, recall), `co-approve`, `money-claims`, `/ai/*` |
| FR-11 | `/summaries`, `/ai/usage`, `/ai/runs` |
| FR-12 | `/ask-the-data/questions` (create, list, get), `/ai/usage`, `/ai/runs` |
| FR-13 | `GET /dashboard/summary`, `POST /dashboard/stream-tickets`, `GET /dashboard/stream` |
| FR-14 | `/reconciliation/statements`, `/batches`, `/exceptions` (list, get, evidence, resolve), `/accounting-periods` |
| FR-15 | `/auth/*` including WebAuthn, step-up and recovery codes, `/me`, `/approvals`, `/users` (roles, sites, mfa-reset), `/roles`, `/audit-log` |
| FR-16 | `Accept-Language`, `locale` fields, `PATCH /me`, OTP request, `/kb-articles`, `/notification-templates` |
| FR-17 | `/kb-articles` (list, create, get, patch, versions, publish by approval, archive) |
| FR-18 | `/api-keys` (list, create by approval, get, patch, revoke, rotate) |
| FR-19 | `/notifications`, `/notification-templates`, `/me/notification-preferences`, indirect outbox use by draft approval and ticket replies |
| FR-20 | `/data-exports` (create, list, get, link), `/erasure-requests` (create, list, get, verify, approve, reject, cancel), `/retention-policies` |

## 15. Resolutions in revision 1

Earlier challenge, then the outcome.

| Earlier challenge (revision 0) | Outcome |
|---|---|
| 1. Split the machine principals; model `provider` as its own role | Accepted in a refined form, **D-13**: `provider`, `gateway`, `integrator`, `public` are authentication schemes listed in `permissions.yaml`, not database roles; `gateway` split out of `integrator` with disjoint scopes (**D-19**). |
| 2. Envelope and RFC 9457 instead of the conventions' error shape | Accepted; unchanged. Documented in sections 1 and 5; the error catalogue gained the codes the PRD uses (C-40). |
| 3. Idempotency retention of 24 h is short for payments (7 days for top-ups) | Accepted as **D-12**: 7 days for top-ups, 24 hours elsewhere; webhook deduplication is separate (**D-09**, section 7.3). |
| 4. FR-16 has no natural operation | Accepted: cross-cutting, stated in the matrix; tests target `Accept-Language` and `locale` fields. |
| 5. Cancelling a top-up conflicts with exactly-once confirmation (late confirmations to `suspense`) | Rejected as written, replaced by **D-05**: a late success credits the wallet once and sets `late`; only mismatches route to `suspense`. The cancel operation stays, limited to unconfirmed top-ups. |
| 6. Four-eyes approvals are an API-level addition; threshold is configuration | Accepted and generalised by **D-15**: one `approvals` resource, threshold zero in v1, payload-hash binding, approver differs from maker, conflict check. The `four_eyes_required` code is replaced by `approval_required`. |
| 7. Telemetry auth: one key per gateway, not per meter | Accepted as **D-20/D-19**: one key per physical gateway, bound to sites and a CIDR range; acknowledgement and heartbeat operations added; batch size, late window and dedupe key per **D-25**. |

New in revision 1, beyond the earlier challenges: ingest paths and `webhook-events` (**D-04**),
canonical enumerations from the schema (**D-01**, **D-03**), cookie refresh transport, 10-minute
EdDSA tokens, WebAuthn and step-up (**D-16, D-17**), the stream ticket rules (**D-18**), the
administrator's reduced role (**D-14**), approvals, adjustments, refunds, tariffs and period lock
(**D-15, D-08**), AI controls (**D-31 to D-37**), attachments, incidents, scoped inbound email and
privacy workflows (**D-29, D-30**).

Enumerations: all twelve API enums with a schema twin, including `Customer.status` (`active, suspended,
closed, erased`), `Meter.status` (`provisioned, active, offline, disconnected, decommissioned`),
`User.status` (`invited, active, suspended`), `Ticket.status` (`open, in_progress, pending_customer,
resolved, closed`) and `ErasureRequest.status` (`pending_approval, verified, approved, processing,
completed, rejected, cancelled, blocked_by_retention`), equal the revised `db/schema.sql`. Open items: the 100 USD confirmation threshold and the
KB-publish approver set remain **[open]** business items.

## 16. Implementation conformance

The implementation uses Django REST framework serializers for request and response shapes and a single
DRF permission class generated from `api/permissions.yaml` (D-42, ADR-0019 to ADR-0021). **This
specification is the contract: it is not generated from the code.** Conformance is tested by running
schemathesis against `api/openapi.yaml` (status codes, schemas, headers, error bodies) together with
the alignment tests (`tests/test_alignment.py`: enumerations against `db/schema.sql`, roles against
`api/permissions.yaml`, the administrator rule) and the traceability tests. The Next.js application
(`hub-web`) is a client of this API, not an API pool, so it has no `x-pool` marker.
