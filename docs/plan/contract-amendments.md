# Contract amendments and binding decisions made while planning

The slice plans were written by separate authors who found gaps between documents. The tech lead
decided each one here. Where a slice file and this file disagree, this file wins. Frozen files
(`api/openapi.yaml`, `api/permissions.yaml`, `db/schema.sql`) are changed only as listed in section 1;
everything else the executor needs from the database arrives as additive numbered migrations
(section 2). Never edit `db/schema.sql`.

## 1. Amendments applied to the frozen API files

OIDC start and callback operations; `confirm_non_delivery` resolution; approval kinds for period
lock, period reopen and money-affecting drafts; provider value `synthetic`; optional `result` on
`getApproval` (new API key secret, returned once to the maker); `ApiKeyCreate` requires `site_ids`
or `all_sites: true` (all-sites integrator keys need a second approver); audit log readable by
`administrator` only; KB-publish approvals may be approved by a different `support_agent`;
`last_event_id` query parameter on the dashboard stream; corrected `AskQuestion` example.

## 2. Migrations the slices add (additive, expand-only)

Numbering uses the next free number at execution time; the slice names the purpose. List: period
lock and reopen approval actions and system-account seed (3); `post_suspense_receipt` and the ingest
read grant on ledger accounts (4); `reverse_token_purchase` (6); notification columns, preferences,
templates (7); hourly usage tables and TEMPORARY grant for ingest (8); `ticket_number`, inbound email
dedupe, KB tag and translation columns (10); `ai_runs` extras and column grants (11);
`draft.money_affecting` approval action (12); `ask_questions` (13); reconciliation evidence columns (14);
`verify_api_key_v2` (15); `retention_policies` and drop functions (16). The verifier checks that the full
series applies on top of the frozen baseline and that `tests/test_schema_in_postgres.py` stays green.

## 3. Decisions

| # | Topic | Decision |
|---|---|---|
| A-01 | Settings modules | Follow D-43/D-49/D-50: base, api, ingest, worker, stream, migrate, test |
| A-02 | Names | Follow docs/03 (`wsgi_api`, `run_worker`, `run_relay`, `frontend/`, `infra/`); RLS settings `app.user_id` and `app.customer_id` per schema; per-role DB env names per docs/04 |
| A-03 | Redis failure on readiness | Degraded, HTTP 200 |
| A-04 | Webhook and telemetry wire format | OpenAPI wins over docs/09 sketches; wire event `payment.confirmed` is stored as `payment.succeeded` |
| A-05 | Redis duplicate hint | Written only after commit, never before |
| A-06 | Failed pull verification | Return 503 so the provider retries (docs/03) |
| A-07 | Telemetry batch response | 202 with `in_future` and `duplicates` (OpenAPI) |
| A-08 | Other site's meter | `site_not_permitted`; TC-FR-06-6 adjusted accordingly |
| A-09 | Dashboard cache TTL, heartbeat, revocation | 5 s soft and 300 s hard TTL; 20 s heartbeat; key revocation within 30 s |
| A-10 | Dashboard SQL | Raw read-only SQL on the replica in one reviewed file is an accepted exception to the analytics import rule |
| A-11 | "Revenue today" | Confirmed top-ups, labelled "collected today"; finance recognises revenue at issuance (D-10) |
| A-12 | AI audit | One terminal `ai_runs` row per attempt through the insert-only `ai_gateway` role; docs/06 corrected |
| A-13 | SQL guard | sqlglot parse, re-parse of the rendered text with pglast, no repair retry (D-24) |
| A-14 | Staff paging for AI incidents | Port with a logging adapter; real channel [open] |
| A-15 | Period lock | Refused with 409 while reconciliation exceptions are open; finance_lead approves |
| A-16 | AI spend visibility | Follows `api/permissions.yaml` (operations and finance); docs/08 corrected |
| A-17 | Default UI locale | French (D-44); TC-FR-16-3 asserts the French default |
| A-18 | Synthetic probe | Staging only until the provider enum change is deployed |
| A-19 | Time control and fixtures alias | `OffsetClock` and the `fixtures` database alias exist in test and staging settings only, never production |
| A-20 | Erasure log copy | Exported to the locked bucket so restore replay can use it |
| A-21 | Two-approver erasure | Two distinct `dp_admin` approvals; stays [open] for business sign-off |
| A-22 | Frontend portal prefix | `/portal`; no CSP-report route handler; no zustand |
| A-23 | `GET /me` | Built in slice 01, not slice 00 |

## 4. How the verifier treats these

Each row is a spot-check in `99-verification.md`. A deviation from a decision above, without a
change request, fails the slice.
