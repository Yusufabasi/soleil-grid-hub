# 03. Code Architecture: Soleil Grid Hub

Status: design, revision 2 (stack change to Django 5 with Django REST framework and Next.js, D-42 to D-46, ADR-0019 to ADR-0021). Owner: software architect. Follows `00-shared-context.md` including its section 7 (decisions D-01 to D-38, binding). FR and NFR ids are used as defined there. Section 11 records how each challenge raised in the first draft was resolved. Where this document and `db/schema.sql` differ on a name or an enumeration, the schema wins (D-01); section 1.3 maps names.

## 0. Principles

1. **Modular monolith, hexagonal inside each module.** A module is a Python package with four layers (`api`, `application`, `domain`, `infrastructure`). Dependencies point inward.
2. **One public surface per module: `contracts.py` and `events.py`.** Another module may import only these two files. They hold Protocols, DTOs (frozen dataclasses), event payloads (frozen dataclasses) and domain errors that cross the boundary. Nothing else in a module is importable from outside.
3. **Cross-module calls are synchronous in-process calls through a Protocol, or asynchronous domain events.** Never the other module's tables, ORM models or repositories.
4. **Money and exactly-once logic live in one database transaction** where both sides are in the same Postgres. Events (via the outbox) cover everything that can be eventually consistent.
5. **Ports for every side effect** (clock, ids, provider, meter, LLM, notifier, object store). Business code never calls `datetime.now()`, `uuid`, `httpx` or `anthropic` directly.
6. **Composition root only wires.** `hub/bootstrap.py` is the single place that knows concrete adapters.
7. **Django is a delivery and persistence framework, not the architecture.** Business rules live in `domain` and `application`, which import no Django. Models are unmanaged and private to their app (ADR-0020); the Django admin is not installed; `ATOMIC_REQUESTS` is off; services are synchronous (async exists only in the stream pool, section 7.7).

---

## 1. Monorepo layout

```
soleil-grid-hub/
├── backend/                      # Django 5 project (D-42); Python 3.12+, psycopg 3
│   ├── pyproject.toml            # uv-managed; one distribution "hub"; ruff, mypy (django-stubs), pytest-django, import-linter config
│   ├── manage.py                 # defaults to hub.settings.api; commands select their own module with --settings
│   ├── .importlinter             # architecture contracts (section 3.3)
│   ├── src/hub/
│   │   ├── settings/             # one module per deployable, each with its own database role (D-22, D-43)
│   │   │   ├── base.py           # shared settings: INSTALLED_APPS (the 13 apps + rest_framework + django.contrib.postgres), env helpers, logging, DRF defaults
│   │   │   ├── api.py            # hub-api: DB role hub_api (+ replica alias), full DRF stack, ROOT_URLCONF hub.urls.api
│   │   │   ├── ingest.py         # hub-ingest: DB role hub_ingest; HUB_INGEST_ROLE=webhooks|telemetry picks ROOT_URLCONF hub.urls.webhooks or hub.urls.telemetry
│   │   │   ├── worker.py         # hub-worker: DB role hub_worker; no URLConf
│   │   │   ├── stream.py         # dashboard stream pool: DB role hub_api read-only alias on the replica; ROOT_URLCONF hub.urls.stream
│   │   │   ├── migrate.py        # used only by the deploy job `manage.py migrate`: DB role migrator
│   │   │   └── test.py           # extends api; test database loaded from db/schema.sql
│   │   ├── urls/                 # api.py, webhooks.py, telemetry.py, stream.py: mount only the routers of that pool
│   │   ├── wsgi_api.py           # WSGI entrypoint, settings hub.settings.api   (gunicorn, pool be_api)
│   │   ├── wsgi_ingest.py        # WSGI entrypoint, settings hub.settings.ingest (gunicorn, pools be_webhooks and be_telemetry)
│   │   ├── asgi_stream.py        # ASGI entrypoint, settings hub.settings.stream (uvicorn, pool be_stream)
│   │   ├── bootstrap.py          # composition root: build_container(settings) wires adapters; get_container()
│   │   ├── shared/               # kernel app: ids, clock, money, errors, UoW, outbox, jobs, logging, flags, DRF helpers, management commands
│   │   │   ├── management/commands/   # run_worker, run_relay, run_scheduler, load_fixtures, check_db_alignment
│   │   │   └── migrations/       # Django migrations; each contains RunSQL applying one numbered file of db/migrations
│   │   ├── identity/  customers/  payments/  ledger/  meters/  telemetry/
│   │   ├── support/  ai_gateway/  analytics/  notifications/  reconciliation/  admin/
│   │   │                         # each is a Django app (AppConfig name "hub.<module>", label "<module>") with the layers of section 1.2
│   └── tests/
│       ├── unit/                 # domain + application with fakes, no I/O, no Django DB
│       ├── integration/          # pytest-django against real PostgreSQL and Redis (testcontainers), repositories, adapters, definer functions
│       ├── contract/             # provider stub and meter simulator contract tests; schemathesis conformance
│       ├── architecture/         # import-linter, Django-config checks, event registry completeness, model-versus-schema introspection
│       └── e2e/                  # API-level flows against docker compose
├── frontend/                     # Next.js App Router app "hub-web" (D-44, ADR-0021), TypeScript strict
│   ├── package.json, next.config.ts, tsconfig.json, eslint.config.mjs, playwright.config.ts
│   ├── middleware.ts             # locale routing (next-intl) + CSP nonce + Server-Action rejection
│   ├── openapi-ts.config.ts      # client generation from api/openapi.yaml
│   ├── src/                      # see section 8
│   └── e2e/                      # Playwright + axe
├── db/
│   ├── schema.sql                # canonical schema INCLUDING roles, grants, definer functions (post_ledger_transaction, audit_append, erase_customer), RLS, analytics_* view schemas
│   ├── migrations/               # numbered SQL files (expand, then contract); 0001_baseline.sql equals schema.sql at first release
│   ├── invariant_tests.sql       # SQL-level invariant tests run in CI against a fresh database
│   └── seed/                     # dev seed: sites, plans, KB articles, demo customers
├── api/
│   ├── openapi.yaml              # OpenAPI 3.1 contract (frozen for the implementer; source of the TS client; NOT generated from code)
│   └── permissions.yaml          # single source of x-roles and of the role-permission seed (D-14); a test fails on drift
├── infra/
│   ├── docker/                   # Dockerfiles (backend image with three commands, hub-web image), compose.yaml for local
│   ├── lb/                       # HAProxy config, pools be_webhooks, be_telemetry, be_stream, be_api, be_web
│   ├── terraform/                # cloud-neutral modules plus gcp/ and aws/ overlays
│   └── observability/            # OTel collector, Prometheus rules, Grafana dashboards
├── tools/
│   ├── provider_stub/            # simulated mobile-money provider (initiate, status pull, signed webhooks, statements)
│   ├── meter_simulator/          # simulated site gateway and meters: token receiver, acknowledgements, heartbeats, telemetry batches, failure modes
│   ├── loadtest/                 # k6 scripts for webhook and telemetry paths
│   ├── check_traceability.py     # requires every module name and FR id to appear in the docs
│   ├── check_django_config.py    # CI: admin not installed, ATOMIC_REQUESTS off, no managed models, no forbidden middleware (section 9)
│   └── scripts/                  # `make` helpers: gen-client, check-openapi-drift, seed
├── docs/                         # 00 to 12 documents, adr/, plan/, runbooks/
└── Makefile                      # `make up`, `make test`, `make lint`, `make gen`, one-command local env (NFR-14)
```

### 1.1 One codebase, deployables and how they differ

The backend image runs one of several commands; `hub-web` is a separate Node image (fourth deployable, D-44).

| Aspect | hub-api | hub-ingest | hub-worker | stream pool | hub-web |
|---|---|---|---|---|---|
| Command | `gunicorn hub.wsgi_api:application` (sync or threaded workers) | `gunicorn hub.wsgi_ingest:application` with `HUB_INGEST_ROLE=webhooks` or `telemetry` (two Deployments) | `manage.py run_worker`, `run_relay`, `run_scheduler` (separate processes, one image) | `uvicorn hub.asgi_stream:application` | `node server.js` (Next.js standalone) |
| Settings module | `hub.settings.api` | `hub.settings.ingest` | `hub.settings.worker` | `hub.settings.stream` | n/a |
| LB pool (D-04) | `be_api` | `be_webhooks`, `be_telemetry` | none | `be_stream` | `be_web` (`/`, `/_next/*`) |
| URLConf | `hub.urls.api` | `hub.urls.webhooks` (`/api/v1/webhooks/providers/*`) or `hub.urls.telemetry` (`/api/v1/telemetry/*`, `/api/v1/gateway/*`) | none | `hub.urls.stream` (`/api/v1/dashboard/stream`) | n/a |
| Auth | EdDSA bearer JWT, API key (`integrator`), customer OTP session, cookie endpoints under `/api/v1/auth` | `provider`: HMAC over `timestamp.event_id.body`; `gateway`: API key bound to sites and a source range | n/a | single-use stream ticket | none: public pages only; tokens stay in browser memory |
| Middleware (Django) | request-context, security headers, rate limit by tier; no session, CSRF or messages middleware | request-context, body-size cap (64 KB webhooks, 256 KB telemetry), signature or key check | n/a | request-context | n/a |
| DB role (D-22) | `hub_api` (`default` primary; `replica` alias for reads) | `hub_ingest` (primary only; narrow grants: `provider_events`, `top_ups`, `reading_batches`, `meter_readings`, `outbox`, `meter_tokens` status columns, EXECUTE on `post_ledger_transaction`) | `hub_worker` (outbox, `consumer_inbox`, tokens, notifications, jobs; EXECUTE on `post_ledger_transaction`) | `hub_api` on the replica alias only | none; never touches the database |
| Scaling | Pool behind LB, CPU bound | Separate pools; webhook capacity reserved (NFR-01) | Replicas by stream lag; scheduler singleton by advisory lock | `leastconn`, about 300 connections | Replicas by CPU |
| Background | None (writes events via outbox only) | None; webhook confirmation runs inline in one transaction (section 6.1) | Outbox relay, stream consumers, token sender, cron jobs | Redis pub/sub listener | none |
| Failure isolation | A staff outage must not stop payments | Minimal dependency set (Postgres, Redis) | Lag does not affect acknowledgements | Dashboard only | Public pages degrade; API unaffected |

Health endpoints on every Django deployable are `/api/v1/health/live` and `/api/v1/health/ready`. Rules: `wsgi_*.py`, `asgi_stream.py` and `settings/*` contain no business logic; each entrypoint sets `DJANGO_SETTINGS_MODULE`, calls `django.setup()` through the standard WSGI or ASGI loader, then `bootstrap.build_container(settings)` once at start (AppConfig `ready()` of `shared` triggers it, so management commands get the same container).

### 1.2 Layout of a module (a Django app)

```
hub/<module>/
├── apps.py               # AppConfig: name = "hub.<module>", label = "<module>", default_auto_field unused (no managed models)
├── models.py             # ONE-LINE SHIM: `from .infrastructure.models import *  # noqa`; the only reason it exists is Django's model discovery
├── contracts.py          # PUBLIC: Protocols, DTOs (frozen dataclasses), errors other modules may use
├── events.py             # PUBLIC: event payload dataclasses produced by this module
├── api/                  # DRF: serializers.py, views.py (viewsets), urls.py, deps.py (use-case lookup from the container)
├── application/          # use cases (one class per use case), ports (Protocols), event handlers, wiring.py
├── domain/               # entities, value objects, domain services, domain errors; pure Python, no Django
└── infrastructure/       # models.py (managed = False), repositories.py, mappers.py, queries.py, adapters, sql/ (raw SQL strings)
```

Django rules every app follows (checked in CI, section 9):

1. Every model has `class Meta: managed = False` and an unqualified `db_table` (the `hub` schema is the role's `search_path`); the column set mirrors `db/schema.sql` and `check_db_alignment` compares models with the introspected database (ADR-0020).
2. **No model class is imported outside its own app's `infrastructure` package** (and the one-line `models.py` shim). Other modules get data only through `contracts.py` Protocols.
3. `django.contrib.admin`, `auth`, `sessions`, `messages` and `contenttypes` are not installed; no `CsrfViewMiddleware`, no `SessionMiddleware`; `ATOMIC_REQUESTS` is not set (default false) in any settings module; `AUTH_USER_MODEL = "identity.User"`.
4. Transactions are explicit: `UnitOfWork` (a thin wrapper over `transaction.atomic`, section 2.1). Reads outside a use case use no transaction.
5. ORM use is limited to repositories and query objects. Ledger, wallet, audit and erasure writes go through database functions (rule in 2.5 and 7.4). Bulk and partitioned inserts use raw SQL through the psycopg connection.
6. No signals, no model `save()` overrides with business rules, no `post_save` handlers; side effects are outbox events.
7. Use cases and repositories are synchronous. The only async code is in `hub.analytics.api.stream` and `hub.shared.streaming` (stream pool), which reach the database through `sync_to_async(thread_sensitive=False)` on a read-only function.

### 1.3 Name mapping to the schema (D-02, D-01)

The first draft of this document used working names. The code uses the schema's names; DTOs and events may keep readable field names but the repository layer maps them as below. Table names are those of `db/schema.sql`; enumerations are those of section 7.2 of the shared context.

| Working name in draft 0 | Schema name (authority) | Notes |
|---|---|---|
| `topups`, `TopupView`, `topup_id` | `top_ups`, `TopUpView`, `top_up_id` (event names keep `topup.initiated`) | Status values: `initiated, pending, pending_unknown, confirmed, failed, expired, cancelled, reversed`; boolean `late` replaces `confirmed_late` |
| `provider_events` (raw webhook rows) | `provider_events` | Only events with a valid signature are stored (D-09). Unique `(provider, event_id)` and `(provider, provider_txn_id)` |
| `provider_accounts` | none | Provider configuration is typed settings plus secrets; `provider` is a text column |
| `tokens`, `TokenView` | `meter_tokens` | Status `pending, sent, acknowledged, failed, cancelled, unknown`; "retrying" is `pending` with `attempts > 0` |
| `token_attempts`, `meter_commands` | columns `attempts`, `next_attempt_at`, `last_error` on `meter_tokens` | No separate tables |
| ledger `PostingLine(direction, amount)` | `ledger_entries.amount_minor` signed (debit positive, credit negative, D-03) | The Python DTO keeps `direction` and a positive `Money`; the ledger adapter converts. API shows direction plus positive amount |
| `ledger_transactions`, `posting_key`, `kind` | same | Kinds are the schema's `ck_ledger_transactions_kind` set |
| `wallet_balances`, `balance_snapshots` | `wallet_balances`, `ledger_balance_snapshots` | `wallet_balances` is maintained inside the definer function, never written by application roles |
| `reading_batches`, `meter_readings`, `consumption_hourly/daily` | `reading_batches`, `meter_readings`, `meter_daily_usage` (hourly aggregate per `04-data-model.md`) | Key `(meter_id, read_at)` |
| `anomaly_rules` | `alert_rules`; outputs in `alerts` | `alerts.kind`: `meter_offline, site_offline, low_balance, anomalous_consumption` |
| `ai_calls`, `ai_call_payloads` | `ai_runs`, with `payload_ref` to an object-store key by prompt hash | `ai_runs.feature`: `triage, reply_draft, summary, ask_data`; `served_model`, `fallback_ran`, `stop_category` |
| `ai_budgets` | typed settings (dollar limits) plus sums over `ai_runs` | Cached in Redis |
| `ai_triage_results` | triage fields on `tickets` plus the full structured output in `ai_runs.output` | Override records are rows in `ai_runs` with `human_decision` |
| `reply_drafts` | `ticket_messages` with `author_type = 'ai_draft'` (C-21) | Message status `draft, approved, sent, rejected, recalled`; `ai_run_id` links the run |
| `ticket_summaries` | `ai_runs` (feature `summary`) output, cached by `(ticket_id, last_message_id)` | |
| `notification_requests`, `notification_attempts` | `notifications` (and templates) | Unique `dedupe_key` |
| `provider_statements`, `statement_lines`, `reconciliation_runs`, `reconciliation_exceptions` | `reconciliation_batches`, `reconciliation_items` | Item kinds `statement_only, ledger_only, amount_mismatch, duplicate_in_statement, currency_mismatch`; status `open, pending_approval, resolved, written_off` |
| `mfa_secrets`, `api_key_scopes`, `customer_contacts` | columns and tables per `04-data-model.md`: `mfa_recovery_codes`, WebAuthn credentials, `api_keys.scopes`, contact columns on `customers` | |
| `export_jobs`, `erasure_requests` | `data_exports`, `data_erasure_requests` | |
| `feature_flags` table | none in v1 | Flags come from typed settings with an audited Redis override (section 7.6) |
| consumer dedupe | `consumer_inbox` | The only dedupe table for consumers |
| outbox `DomainEvent.name` / `aggregate_id` | `outbox.event_type` / `aggregate_type` + `aggregate_id` | Envelope fields beyond the columns live in `outbox.payload` |
| permissions `support.draft.approve` | `ticket.ai_approve` | One permission name (C-21) |
| SQLAlchemy models, Alembic revisions, FastAPI routers | unmanaged Django models (`managed = False`), numbered SQL files in `db/migrations` applied by `RunSQL` migrations, DRF viewsets | ADR-0020; the schema stays the authority |
| Pydantic DTOs and settings | frozen dataclasses; Django settings modules with `django-environ`; Pydantic only inside `ai_gateway` | D-42 |
| `hub_app`, `hub_readonly_ai`, `roles.sql` | `hub_api`, `analytics_support`, `analytics_finance`, `analytics_ops`; roles are part of `db/schema.sql` | D-22 |

---

## 2. Module responsibilities

Table names are the schema's (section 1.3); `04-data-model.md` and `db/schema.sql` are authoritative for columns. Public interface Protocols live in `<module>/contracts.py`.

### Django specifics per module

| App (`hub.<module>`) | Unmanaged models (tables) | Repositories and query objects | API surface (DRF unless noted) | Writes that bypass the ORM |
|---|---|---|---|---|
| `shared` | `Outbox`, `ConsumerInbox`, `IdempotencyKey` | `OutboxRepository` (claim with `SKIP LOCKED`), `InboxRepository`, `IdempotencyRepository` | `health` views (plain Django), problem handler, pagination, permission and throttle classes | none |
| `identity` | `User` (custom user model), `Role`, `Permission`, `RolePermission`, `UserRole`, `UserSite`, `RefreshToken`, `CustomerOtp`, `MfaRecoveryCode`, `WebauthnCredential`, `ApiKey`, `Approval`, `StaffCustomerLink`, `AuditLogRow` (read only) | `UserRepository`, `SessionRepository`, `ApiKeyRepository`, `ApprovalRepository` | `/auth/*` (cookie endpoints are plain Django views, section 7.7), `/users`, `/approvals`, `/api-keys` | audit writes through `hub.audit_append` |
| `customers` | `Customer`, `Site`, `Plan` | `CustomerRepository`, `PlanRepository`, `CustomerQueries` | `/customers`, `/sites`, `/plans` | erasure through `hub.erase_customer` (admin orchestrates) |
| `payments` | `TopUp`, `ProviderEvent` | `TopUpRepository` (`select_for_update`), `ProviderEventRepository` | `/top-ups`, `/webhook-events` (hub-api); webhook view (hub-ingest, plain Django view, no DRF) | ledger through `LedgerService` |
| `ledger` | `LedgerAccount`, `LedgerTransaction`, `LedgerEntry`, `WalletBalance`, `LedgerBalanceSnapshot`, `AccountingPeriod` (all read only in Python) | `LedgerReader` (statements, balances) and `LedgerWriter` (calls the definer function) | `/ledger/*` read, adjustments only through approvals | **all writes** via `hub.post_ledger_transaction` |
| `meters` | `Meter`, `MeterState`, `Gateway`, `MeterToken` | `MeterRepository`, `TokenRepository` (due-scan with `SKIP LOCKED`) | `/meters`, `/tokens`; gateway views `/gateway/tokens/{id}/acknowledge`, `/gateway/heartbeat` (hub-ingest, DRF with the `gateway` auth class) | ledger through `LedgerService` |
| `telemetry` | `MeterReading` (partitioned; read only in Python), `ReadingBatch`, `MeterDailyUsage`, `AlertRule`, `Alert` | `ReadingWriter` (raw SQL), `UsageQueries` | `/telemetry/batches` (hub-ingest, DRF envelope serializer plus a tight validation loop), `/meters/{id}/usage` | readings by raw SQL and `COPY` |
| `support` | `Ticket`, `Incident`, `TicketMessage`, `TicketMessageCitation`, `Attachment`, `KbArticle`, `KbArticleVersion` | `TicketRepository`, `KbRepository`, `KbSearchQueries` (full-text SQL) | `/tickets`, `/drafts`, `/kb/*`, `/incidents` | none |
| `ai_gateway` | `AiRun` | `AiRunRepository`, `BudgetQueries` | `/ai/usage` (read) | none |
| `analytics` | `DashboardSnapshot`, `AskDataQuery` | `DashboardQueries` (replica), `AskAuditRepository` | `/dashboard/*`, `/ask-the-data/*` (DRF); stream: async Django view on the stream pool | generated SQL runs through psycopg on `analytics_*` login roles, never the ORM |
| `notifications` | `Notification`, `NotificationTemplate`, `SuppressionEntry` | `NotificationRepository` | `/notifications` read | none |
| `reconciliation` | `ReconciliationBatch`, `ReconciliationItem` | `BatchRepository`, `ItemRepository` | `/reconciliation/*` | ledger through `LedgerService` |
| `admin` | `DataExport`, `DataErasureRequest` | `ErasureRepository`, `ExportRepository` | `/data-exports`, `/erasure-requests`, `/audit-log`, `/config`, retention endpoints | erasure through `hub.erase_customer` |

The `admin` app is named for the platform-administration module of the shared context; it is **not** `django.contrib.admin`. To avoid an `AppConfig` label clash its label is `hub_admin`.

### 2.1 shared (kernel)

- **Owns:** the technical tables `outbox`, `consumer_inbox`, `idempotency_keys`. No business rules.
- **Provides:** `Clock`, `IdGenerator`, `Money`, `UnitOfWork`, `OutboxWriter`, `EventBus` contracts, `DomainError` base, job framework and the `run_worker`, `run_relay`, `run_scheduler` management commands, logging, env helpers, `FeatureFlags`, `ApprovalExecutorRegistry` and `ErasureParticipant` contracts, and the DRF helpers of section 7.7 (permission class, throttle classes, cursor pagination, problem handler, idempotency decorator, envelope renderer).
- **Idempotency windows (D-12):** `idempotency_keys` rows live 7 days for top-up initiation and 24 hours for every other scope; the purge job enforces this.
- **Must not:** import any business module; contain business vocabulary (payment, ticket, meter).

```python
# shared/money.py
@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: Literal["USD", "CDF"]
    def __add__(self, o: "Money") -> "Money": ...   # raises CurrencyMismatch

# shared/ports.py
class Clock(Protocol):
    def now(self) -> datetime: ...                      # tz-aware UTC
class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...                       # UUIDv7; adapter wraps one pinned library (uuid-utils or uuid7, pinned by version and hash in the lock file) because the Python 3.12 standard library has no v7

# shared/uow.py  (thin wrapper over django.db.transaction.atomic; no session object is exposed)
class UnitOfWork(Protocol):
    outbox: "OutboxWriter"
    def __enter__(self) -> "UnitOfWork": ...           # opens transaction.atomic(using="default"); sets the principal context with SET LOCAL
    def __exit__(self, *exc) -> None: ...               # clean exit: flush staged outbox rows, then commit; exception: roll back, outbox discarded
    def savepoint(self) -> ContextManager[None]: ...    # nested transaction.atomic() for partial failure inside a use case
    def require_active(self) -> None: ...               # raises if not inside a transaction (repositories call it before select_for_update)
    def on_commit(self, fn: Callable[[], None]) -> None: ...  # transaction.on_commit, for cache invalidation and pub/sub only

class OutboxWriter(Protocol):
    def add(self, event: "DomainEvent") -> None: ...    # staged; written to hub.outbox in the same transaction at exit

# shared/events.py
@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: UUID; name: str; version: int; occurred_at: datetime
    correlation_id: UUID; causation_id: UUID | None
    aggregate_type: str; aggregate_id: UUID; payload: Mapping[str, Any]
# each module's events.py defines one frozen dataclass per event of section 4 with to_payload() and from_payload();
# from_payload rejects unknown or missing fields (strict construction), which replaces schema validation of events
```

Usage rule: a use case opens `with uow:` exactly once per transaction boundary named in section 6; it never calls `transaction.atomic` itself and never reads `connection` directly (repositories do).

### 2.2 identity (FR-15, FR-18, part of FR-16)

- **Owns:** `users`, `roles`, `permissions`, `role_permissions`, `user_roles`, `user_sites`, `mfa_recovery_codes`, WebAuthn credentials, `refresh_tokens`, `customer_otp`, `api_keys`, `gateways` key binding (the key side; the device side is `meters`), `audit_log` (append-only through `audit_append`), `approvals`, `staff_customer_link`.
- **Events produced:** `user.locked`, `api_key.revoked`, `approval.requested`, `approval.granted`, `approval.rejected`.
- **Responsibilities of the auth part (D-13, D-16, D-17, D-19, D-21):**
  - Staff: company identity provider over OIDC with enforced MFA preferred; local break-glass accounts use TOTP; WebAuthn required for `finance_officer` and `administrator` before go-live; step-up within 5 minutes guards approvals, role grants, key creation, export and erasure; backup codes; an administrator MFA reset needs a second administrator (an approval).
  - Sessions: access token is a JWT signed with EdDSA, 10 minutes, with a `tv` claim compared with `users.token_version` (cached in Redis for 60 s) for revocation; refresh token is opaque and rotating with family reuse detection and no grace window, delivered as an `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` cookie for browsers and never in a response body (body transport only for non-browser clients); staff idle 30 minutes and absolute 8 hours, customer idle 5 minutes and absolute 12 hours.
  - Customers: phone one-time code (`customer_otp`) with limits per phone, address and network, a country-prefix allow-list, a proof-of-work or CAPTCHA step, an always-202 response that never sends an SMS to an unknown number, failure counting per challenge, re-verification after dormancy, and a second proof of identity before export or erasure.
  - Machine principals are authentication schemes, not roles: `provider` (HMAC verification lives in `payments`' `ProviderGateway`, which returns a `Principal(kind="provider")`), `gateway` and `integrator` API keys (format `sgh_<env>_<keyid>_<secret>`, stored as an HMAC with a server pepper from the secrets manager, maximum life 180 days, disjoint scopes, gateway keys bound to sites and a source address range), and `public`.
- **Public interface:**

```python
@dataclass(frozen=True, slots=True)
class Principal:
    subject_id: UUID
    kind: Literal["staff", "customer", "provider", "gateway", "integrator", "public"]
    roles: frozenset[str]            # support_agent, finance_officer, operations_engineer, administrator
    attributes: frozenset[str]       # finance_lead, dp_admin (attributes, not roles; D-13)
    permissions: frozenset[str]      # from api/permissions.yaml via role_permissions
    scopes: frozenset[str]           # API-key scopes
    site_ids: frozenset[UUID]        # user_sites or gateway key sites
    customer_id: UUID | None
    session_id: UUID | None
    token_version: int
    stepped_up_at: datetime | None
    locale: Literal["en", "fr"]

class Authenticator(Protocol):
    def authenticate_bearer(self, token: str) -> Principal: ...
    def authenticate_api_key(self, raw_key: str, *, source_ip: str) -> Principal: ...
    def rotate_refresh(self, presented: str) -> "SessionTokens": ...      # reuse detection revokes the family
    def step_up(self, principal: Principal, proof: "StepUpProof") -> Principal: ...

class Authorizer(Protocol):
    def require(self, principal: Principal, permission: str) -> None: ...       # raises PermissionDenied
    def require_site(self, principal: Principal, site_id: UUID) -> None: ...

class AuditLog(Protocol):
    def record(self, *, actor: Principal | None, action: str, target_type: str,
                     target_id: str, detail: Mapping[str, Any], uow: UnitOfWork) -> None: ...
    # adapter calls hub.audit_append(...) (SECURITY DEFINER, owned by audit_writer) inside the caller's
    # transaction; per-stream hash chain; application roles have EXECUTE only, never INSERT
```

- **Implementation notes (Django):**
  - **User model:** `identity.infrastructure.models.User` is the custom user model (`AUTH_USER_MODEL = "identity.User"`), `managed = False`, mapped to `hub.users`. It does not inherit `AbstractBaseUser`; it exposes `is_authenticated = True`, `is_active` from `status`, and no password methods. Break-glass local accounts store an Argon2id hash (`argon2-cffi`) in a dedicated column verified only by `identity.application.LocalLogin`. Django's session, password-reset and `auth` machinery are not used.
  - **Bearer JWT:** `hub.identity.api.authentication.BearerJwtAuthentication` (a DRF `BaseAuthentication`) reads `Authorization: Bearer`, verifies with **PyJWT** (`algorithms=["EdDSA"]`, key from the secrets manager, `aud=hub-api`, `iss=hub`, 30 s leeway), then checks `tv` against `users.token_version` (Redis cache 60 s) and returns `(user, Principal)`; the `Principal` is also stored as `request.principal`. Claims: `sub`, `sid`, `tv`, `kind`, `loc`, `su` (step-up time), `iat`, `exp` (10 minutes). Two further classes: `ApiKeyAuthentication` (header `Authorization: ApiKey sgh_...`, HMAC lookup, source range check) and `StreamTicketAuthentication` (stream pool only).
  - **Step-up:** `POST /auth/step-up` verifies a fresh TOTP code or WebAuthn assertion and returns a new access token with `su = now`. Operations flagged `step_up: true` in `api/permissions.yaml` are refused with 401 `step_up_required` unless `now - su <= 300` seconds; `ApprovalService.approve` re-checks the same rule server-side from the principal, never from client input.
  - **OIDC:** **Authlib** (Starlette-free `authlib.integrations.requests_client`) performs the authorization-code flow with PKCE against the company identity provider; the callback maps the verified ID token (`sub`, `email`, `amr` must include an MFA method) to a `hub.users` row by `oidc_subject`; unknown subjects are refused (users are created through the `user.create` approval, never auto-provisioned). The hub then issues its own EdDSA access token and refresh cookie.
  - **TOTP:** **pyotp** (30 s step, SHA-1 only for authenticator compatibility, one-step drift, replay blocked by storing the last accepted counter). **WebAuthn:** **py_webauthn** (`webauthn` package) for registration and assertion, `user_verification="required"`, sign-count check, credentials in the WebAuthn table. Backup codes: random 10-character codes stored hashed in `mfa_recovery_codes`.
  - **Refresh and cookie endpoints** are plain Django views, specified in section 7.7.
- **Approvals** are specified in section 2.14.
- **Must not:** know about customers' billing data, payments or tickets; store AI prompts (that is `ai_gateway`). Permission names are strings declared in `api/permissions.yaml` (the single source, D-14) and exposed as constants in each module's `contracts.py`.

### 2.3 customers (FR-01, part of FR-20)

- **Owns:** `customers` (unique phone, D-11; contact columns; `version` for optimistic concurrency), `sites`, `plans` (tariffs versioned with `effective_from`), and the customer-to-meter assignment (columns per `04-data-model.md`).
- **Events produced:** `customer.registered`, `customer.status_changed`, `meter.assigned`, `customer.erased`.
- **Public interface:**

```python
class CustomerDirectory(Protocol):
    def get(self, customer_id: UUID) -> CustomerView: ...
    def find_by_phone_hash(self, phone_hash: str) -> CustomerView | None: ...
    def current_plan(self, customer_id: UUID) -> PlanView: ...
    def quote_energy(self, customer_id: UUID, amount: Money, at: datetime) -> EnergyQuote: ...  # tariff version + energy_wh, floor to Wh (D-08)
    def meter_for_customer(self, customer_id: UUID) -> MeterRef | None: ...
    def customer_for_meter(self, meter_id: UUID) -> UUID | None: ...
    def contact(self, customer_id: UUID, *, purpose: Literal["notify"]) -> ContactView: ...
```

- **Tariff changes** are an approval action `tariff.change` (section 2.14): the executor inserts a new plan version with `effective_from`, rejecting a change above the maximum per revision.
- **Must not:** hold balances (ledger), device state (meters), or log raw phone numbers.

### 2.4 payments (FR-02, FR-03)

- **Owns:** `top_ups` (statuses in section 1.3; `late` flag; stored quoted tariff version, `energy_wh` and `payer_msisdn` snapshot, D-08), `provider_events` (valid-signature events only, D-09; kept 13 months).
- **Events produced:** `topup.initiated`, `payment.confirmed`, `payment.failed`, `payment.reversed`.
- **Public interface:**

```python
class PaymentService(Protocol):
    def initiate_top_up(self, cmd: InitiateTopUp, *, idempotency_key: str) -> TopUpView: ...   # 7-day window
    def get_top_up(self, top_up_id: UUID) -> TopUpView: ...
    def confirmed_between(self, start: datetime, end: datetime, provider: str) -> list[ConfirmedPaymentRef]: ...   # for reconciliation
    def provider_evidence(self, top_up_id: UUID) -> ProviderEvidence: ...   # pull from the provider status API, stored and returned (D-15 match_to_top_up)
```

- **Calls:** `LedgerService.post` (same transaction, section 6.1), `CustomerDirectory.quote_energy`, port `ProviderGateway`.
- **Must not:** compute token counters or call the site gateway; mutate ledger tables; treat the webhook acknowledgement as "token issued"; store invalid-signature requests in `provider_events` (a quarantine log with 30-day retention takes them).

### 2.5 ledger (FR-04, NFR-05)

- **Owns:** `ledger_accounts` (kinds `customer_wallet, provider_clearing, revenue_energy, fees, suspense, chargeback_receivable`), `ledger_transactions`, `ledger_entries`, `wallet_balances`, `ledger_balance_snapshots`, `accounting_periods`. Entries are insert-only; immutability triggers are `ENABLE ALWAYS`.
- **Write path (D-22):** the adapter calls the `SECURITY DEFINER` function `hub.post_ledger_transaction(...)`, owned by the no-login role `ledger_owner` with `search_path` pinned to `pg_catalog, hub, pg_temp`. Application roles (`hub_api`, `hub_ingest`, `hub_worker`) have EXECUTE on that function and no INSERT, UPDATE or DELETE on `ledger_*` or `wallet_balances`. The function inserts the transaction (unique `posting_key`), the entries, locks wallet balance rows in ascending account id, applies balances, rejects postings into a locked `accounting_periods` row with `period_locked`, and the deferred constraint trigger verifies that every transaction sums to zero per currency.
- **Events produced:** `ledger.posted`, `wallet.low_balance`.
- **Public interface:**

```python
@dataclass(frozen=True, slots=True)
class PostingLine:
    account: AccountRef            # AccountRef(kind="customer_wallet", customer_id=..., currency="USD")
    direction: Literal["debit", "credit"]
    amount: Money                  # positive; the adapter stores debit + and credit - (D-03)

@dataclass(frozen=True, slots=True)
class PostingRequest:
    posting_key: str               # unique, e.g. "topup:<id>:confirm", "token:<id>:purchase"
    kind: Literal["topup_received", "energy_purchase", "fee", "refund", "adjustment", "reconciliation_fix", "reversal"]
    lines: list[PostingLine]       # validated in Python and again in the database
    effective_at: datetime
    reference: Reference           # (type, id) of the originating aggregate, opaque to ledger
    created_by_user_id: UUID | None

class LedgerService(Protocol):
    def post(self, req: PostingRequest, *, uow: UnitOfWork) -> PostingResult: ...        # idempotent on posting_key
    def reverse(self, posting_key: str, *, reason: str, uow: UnitOfWork) -> PostingResult: ...  # at most once per transaction
    def reverse_with_shortfall(self, posting_key: str, *, uow: UnitOfWork) -> ReversalResult: ...  # D-06: reverses what the wallet holds, posts the rest to chargeback_receivable
    def balance(self, account: AccountRef) -> Money: ...
    def statement(self, account: AccountRef, page: PageRequest) -> Page[EntryView]: ...
```

- **Approvals:** manual `adjustment` and `refund` postings are never callable from an HTTP handler directly; they are executed by the ledger's `ApprovalExecutor` for actions `ledger.adjustment` and `ledger.refund` after a second person approves (section 2.14).
- **Must not:** know what a top-up, token or meter is (it sees `kind` and `reference` as data); expose update or delete; call any other business module. It is a leaf that depends on `shared` only.

### 2.6 meters (FR-01 device part, FR-05, part of FR-07)

- **Owns:** `meters` (device registry, firmware, `last_seen`, monotonic `token_counter`, `version`), `meter_state`, `gateways` (device side: site, last heartbeat), `meter_tokens` (statuses in section 1.3; `ledger_transaction_id` NOT NULL; per-meter `counter`; `expires_at`; `attempts`, `next_attempt_at`; encrypted payload; unique `top_up_id`).
- **Events produced:** `token.requested`, `token.issued`, `token.failed`, `meter.offline`, `meter.online`, `site.offline`.
- **Events consumed:** `payment.confirmed` (to issue a token), `telemetry.heartbeat_missed` (meter silence over 90 minutes).
- **Gateway interface (D-20):** the hub pushes tokens through the outbound adapter behind the `MeterGateway` port; the site gateway answers with `POST /api/v1/gateway/tokens/{id}/acknowledge` and sends `POST /api/v1/gateway/heartbeat` every 5 minutes (routes in `meters.api.gateway`, mounted on `hub-ingest` role `telemetry`, authenticated by the one-key-per-gateway `gateway` API key). A missing heartbeat for 15 minutes raises `site.offline`. Token issuance is the single approved write command to site systems: bound to meter id, counter and expiry; there is no remote disconnect and no remote configuration in v1.
- **Public interface:**

```python
class MeterDirectory(Protocol):                # used by telemetry
    def resolve(self, device_ref: str, *, site_ids: frozenset[UUID]) -> MeterRef | None: ...  # None if the meter is outside the key's sites
    def mark_seen(self, meter_id: UUID, at: datetime) -> None: ...

class TokenService(Protocol):
    def issue_for_payment(self, payment: PaymentConfirmed) -> TokenView: ...   # idempotent on top_up_id
    def acknowledge(self, token_id: UUID, *, gateway: Principal, counter: int) -> TokenView: ...
    def retry_now(self, token_id: UUID, *, actor: Principal) -> TokenView: ...  # resets next_attempt_at, same token id
    def status_for_top_up(self, top_up_id: UUID) -> TokenView | None: ...
    def confirm_non_delivery(self, token_id: UUID, evidence: ReconciliationEvidence, *, uow: UnitOfWork) -> None: ...  # called by the reconciliation approval executor only
```

- **Calls:** `LedgerService` (energy purchase posting, reversal), `CustomerDirectory`, port `MeterGateway`.
- **Must not:** read provider data; re-derive payment validity (it trusts `payment.confirmed`, then re-checks meter ownership and status, D-08); store readings; expose any command other than token push.

### 2.7 telemetry (FR-06, anomaly part of FR-07)

- **Owns:** `meter_readings` (monthly partitions, key `(meter_id, read_at)`), `reading_batches` (batch idempotency), `meter_daily_usage` and the hourly aggregate, `alert_rules` for consumption, `alerts` rows for telemetry-derived kinds.
- **Events produced:** `telemetry.batch_ingested` (summary only, not rows), `telemetry.heartbeat_missed`, `consumption.anomaly_detected`.
- **Public interface:**

```python
class TelemetryService(Protocol):
    def ingest_batch(self, batch: ReadingBatch, *, source: str) -> IngestResult: ...  # idempotent
    def usage(self, meter_id: UUID, start: datetime, end: datetime, grain: Literal["hour", "day"]) -> list[UsagePoint]: ...
```

- **Calls:** `MeterDirectory` only.
- **Limits (D-25):** at most 1,000 readings or 256 KB per batch; readings up to 7 days late are accepted; a reading more than 5 minutes in the future is rejected individually; `seq` is advisory.
- **Must not:** emit one event per reading (cardinality); touch ledger or customers; run model calls.

### 2.8 support (FR-08, FR-09 orchestration, FR-10, FR-11, FR-17)

- **Owns:** `tickets` (including triage suggestion fields, `incident_id`, `version`), `incidents`, `ticket_messages` (an AI reply draft is a message with `author_type = 'ai_draft'` and status `draft, approved, sent, rejected, recalled`), `ticket_message_citations`, `attachments` (3 files of at most 5 MB, scanned, cookie-less download domain), `kb_articles`, `kb_article_versions`.
- **Events produced:** `ticket.created`, `ticket.triaged`, `ticket.triage_failed`, `draft.created`, `draft.approved`, `draft.rejected`, `draft.recalled`, `ticket.resolved`.
- **Events consumed:** `ticket.created` (triage), `ticket.triaged` (draft generation for categories that allow it), `message.received`.
- **Public interface:**

```python
class SupportService(Protocol):
    def open_ticket(self, cmd: OpenTicket, *, idempotency_key: str | None) -> TicketView: ...
    def approve_draft(self, message_id: UUID, *, edited_body: str | None, actor: Principal) -> DraftView: ...   # permission ticket.ai_approve; re-runs lints on changed text; starts the 30 s recall window
    def recall_draft(self, message_id: UUID, *, actor: Principal) -> DraftView: ...                              # only while the outbox row is not yet available
    def reject_draft(self, message_id: UUID, *, reason: str, actor: Principal) -> DraftView: ...
    def search_kb(self, query: str, locale: str, limit: int) -> list[KbPassage]: ...

class TriagePipeline(Protocol):                 # driven by event handlers inside support.application
    def triage(self, ticket_id: UUID) -> TriageResult: ...
    def draft_reply(self, ticket_id: UUID) -> DraftView: ...                # creates the ai_draft message
    def summarise(self, ticket_id: UUID) -> SummaryView: ...
```

- **KB publication** is an approval action `kb.publish` (D-36, section 2.14); `kb_article_versions` records `published_by` and `approved_by`; the forbidden-promise lint applies even to amounts present in retrieved passages.
- **Money-affecting drafts** (`money_affecting` set by triage or by lint) need a second approval by a `finance_officer` before sending (D-35), recorded through `ApprovalService` with action `draft.money_affecting`.
- **Calls:** `AiService` (ai_gateway), `CustomerDirectory` (account context, minimised), `NotificationService` (request send after approval), `ApprovalService`.
- **Must not:** import `anthropic`, build prompts that bypass `ai_gateway`, send text to a customer unless the `ticket_messages` row has `approved_by` and `approved_at` set by a staff `Principal` (invariant 2; the schema CHECK `ck_ticket_messages_ai_approval` enforces it too).

### 2.9 ai_gateway (FR-09 to FR-12 engine, NFR-07, NFR-08, NFR-13)

- **Owns:** `ai_runs` (feature, model, `served_model`, `fallback_ran`, tokens including cache read and write, latency, cost in micro-dollars, status, `stop_category`, prompt hash, `payload_ref`, human decision). The redacted prompt and output live in object storage keyed by prompt hash (retention 90 days, erased with the customer). `prompt_templates` are versioned in code and their hash is recorded.
- **Status vocabulary** (D-01): `started, ok, refused, truncated, invalid, timeout, error, budget_blocked, guard_blocked`; features `triage, reply_draft, summary, ask_data`.
- **Events produced:** `ai.budget_exceeded`.
- **Budgets (D-34):** dollars with token equivalents shown; per ticket, per feature per day, per customer and per API key per day; separate pools for drafts and ask-the-data and a floor reserved for triage.
- **Fallbacks (D-33):** the `LLMClient` adapter sends a pinned list of approved fallback models (inside the approved agreement, never the provider default), only on synchronous calls, never for evaluation runs; the served model is recorded. The model alias has no dated snapshot, so a drift monitor compares served model and eval scores daily.
- **Public interface:**

```python
@dataclass(frozen=True, slots=True)
class AiRequest:
    feature: Literal["triage", "reply_draft", "summary", "ask_data"]
    subject_ref: Reference                        # ticket id or question id: budgets and audit
    prompt_version: str
    variables: Mapping[str, str]
    locale: Literal["en", "fr"]
    output_schema: type[BaseModel] | None         # a Pydantic v2 model class from ai_gateway.contracts (TriageOutput, DraftOutput, SummaryOutput, SqlProposal); structured outputs when set
    actor: Principal | None
    sync: bool                                    # fallbacks only when True
    deadline: timedelta                           # ask_data uses 3 s (D-24)

class AiResult(Generic[T]):
    parsed: T | None; text: str | None
    status: Literal["ok", "refused", "truncated", "invalid", "timeout", "error", "budget_blocked", "guard_blocked"]
    usage: UsageView; run_id: UUID; served_model: str

class AiService(Protocol):
    def run(self, req: AiRequest) -> AiResult: ...

class Redactor(Protocol):
    def redact(self, text: str) -> RedactedText: ...      # maps phone, email, names, national id, meter ids to placeholders
```

- **Pydantic stays here only.** The `pydantic` library is used inside `ai_gateway` (and the output-schema classes in `ai_gateway.contracts`) to define the structured-output schemas sent to the model and to validate what comes back (status `invalid` on failure). No other module imports `pydantic` (an import-linter contract enforces it); callers receive plain frozen dataclasses mirrored from the schema classes by `ai_gateway.application`.
- **Calls:** port `LLMClient` and `ObjectStore` only.
- **Must not:** hold write credentials outside its own tables; execute SQL; know ticket or analytics business rules; let a caller bypass `Redactor`, the guards or the budget check; receive query result rows (D-31). It depends on `shared` only. The `LLMClient` adapter is the only code that imports `anthropic`, sets model, effort, prompt caching and fallbacks, and handles the `refusal` stop reason.

### 2.10 analytics (FR-12, FR-13)

- **Owns:** `dashboard_snapshots` (optional), the ask-the-data audit rows (question, generated SQL, validator verdict, row count, duration, role; stored in `ai_runs.generated_sql` through `ai_gateway` plus an `ask_data_queries` audit table per `04-data-model.md`), and the **semantic catalog** (allow-listed views and column descriptions per role). The views themselves are in the schemas `analytics_support`, `analytics_finance`, `analytics_ops` in `db/schema.sql`.
- **Ai views as a published read contract:** these views are the one place where cross-module data meets (a deliberate, controlled exception to "no other module's tables"). They are owned jointly by `analytics` and the database engineer, versioned, reviewed like an API, and covered by a test that lists every column exposed to each role. Support and operations get aggregate views; finance gets pseudonymous money rows; customer attributes and phone digits are never exposed; site scoping is not enforced in this path in v1 (accepted residual risk, D-24) while the main API enforces it.
- **Events produced:** `askdata.query_executed`, `dashboard.updated` (pub/sub only, not outbox).
- **Events consumed:** `payment.confirmed`, `ledger.posted`, `meter.offline`, `meter.online`, `site.offline`, `alert.raised` (update live dashboard cache and publish to SSE).
- **Public interface:**

```python
class DashboardQueries(Protocol):
    def site_status(self, filters: SiteFilter, *, principal: Principal) -> list[SiteStatus]: ...
    def revenue_today(self, site_id: UUID | None, tz: str, *, principal: Principal) -> RevenueSummary: ...
    def open_alerts(self, filters: AlertFilter, *, principal: Principal) -> list[AlertView]: ...

class AskData(Protocol):
    def ask(self, question: str, *, actor: Principal, locale: str) -> AskDataAnswer: ...  # sql, assumptions, columns, rows, caveats

AskRole = Literal["analytics_support", "analytics_finance", "analytics_ops"]   # derived from the staff role; administrator maps to none
```

- **Calls:** `AiService`, `Authorizer`, its own `ReadOnlySqlExecutor` port.
- **Must not:** write to any table other than its own audit rows; run generated SQL under an application role (only the per-role `analytics_*` login roles on the replica); serve the `administrator` role (platform administrator, D-14); pass rows to the model.

### 2.11 notifications (FR-19)

- **Owns:** `notifications` (unique `dedupe_key`, attempts, status), templates (EN/FR), `suppression_list`.
- **Events produced:** `notification.sent`, `notification.failed`.
- **Events consumed:** `notification.requested`, `token.issued`, `token.failed`, `wallet.low_balance`, `meter.offline`, `draft.approved`.
- **Public interface:**

```python
class NotificationService(Protocol):
    def request(self, req: NotificationRequest, *, uow: UnitOfWork) -> UUID: ...  # idempotent on dedupe_key
```

- **Calls:** `CustomerDirectory.contact` **at send time** (never copied into events or into the `notifications` row beyond a masked hint, so erasure and contact changes take effect immediately), ports `Notifier` (SMS, email). Approved drafts are enqueued with outbox `available_at` set 30 seconds ahead (the recall window, D-35).
- **Must not:** decide business rules about when to notify (event consumers inside `notifications.application.handlers` map events to templates; the rules per event live there and only there).

### 2.12 reconciliation (FR-14)

- **Owns:** `reconciliation_batches` (one provider statement per day, upload size and row caps, formula neutralisation) and `reconciliation_items` (kinds `statement_only, ledger_only, amount_mismatch, duplicate_in_statement, currency_mismatch`; status `open, pending_approval, resolved, written_off`; a resolution note is mandatory).
- **Events produced:** `reconciliation.completed`, `reconciliation.exception`.
- **Public interface:**

```python
class ReconciliationService(Protocol):
    def import_statement(self, provider: str, day: date, source: StatementSource) -> UUID: ...   # job fetches via ProviderGateway or staff uploads
    def run(self, batch_id: UUID) -> RunSummary: ...
    def propose_resolution(self, item_id: UUID, resolution: Resolution, *, actor: Principal, uow: UnitOfWork) -> ApprovalView: ...
```

- **Resolution flow (D-15):** every resolution that posts money (`match_to_top_up`, `post_adjustment`, `write_off`, token non-delivery confirmation) is a proposal: `propose_resolution` moves the item to `pending_approval` and creates an approval whose payload hash covers the exact resolution. A different staff member approves; the reconciliation `ApprovalExecutor` then calls `LedgerService.post` or `TokenService.confirm_non_delivery` in the approval's transaction. `match_to_top_up` also requires provider evidence the system obtained itself (`PaymentService.provider_evidence` or a statement fetched by the job); an uploaded file alone is not accepted. The conflict check blocks approvers linked to the customer.
- **Calls:** `PaymentService.confirmed_between` and `provider_evidence`, `LedgerService`, `TokenService.confirm_non_delivery` (reconciliation therefore depends on `meters`, see section 3.1), `ApprovalService`, port `ProviderGateway.fetch_statement`.
- **Must not:** edit `top_ups` or `ledger_*` directly; auto-correct money; resolve without a note.

### 2.13 admin (FR-15 views, FR-18 admin, FR-20)

- **Owns:** `data_exports`, `data_erasure_requests`, retention configuration (the single retention table of `04-data-model.md`, D-28). Thin: it orchestrates, it owns no domain data.
- **Role (D-14):** the administrator is a platform administrator: users, roles, keys, configuration, audit. No money capability, no ticket or customer content, no ask-the-data, no top-up initiation. The audit-log views (read of `audit_log` and the hash-chain verifier status) live here.
- **Erasure and export (D-29, FR-20):** erasure is pseudonymisation in place, blocked while the wallet is non-zero or tokens are unacknowledged, with a 7-day cooling-off period and a notice to the old contact, a `dp_admin` and a second approver. The `erase_customer` database function (owned by a non-superuser role) validates the request and pseudonymises; `admin` then drives the other `ErasureParticipant`s (object-store payload deletion in `ai_gateway`, attachment removal in `support`, `notifications` cleanup). The erasure log is replayed after any restore. Export needs `export:bulk`, an approval, and a second proof of identity for a customer export.
- **Public interface:**

```python
class ErasureParticipant(Protocol):             # defined in shared.contracts; each module registers one in its wiring
    name: str
    def export_subject(self, customer_id: UUID) -> SubjectExport: ...
    def erase_subject(self, customer_id: UUID, *, uow: UnitOfWork) -> ErasureReport: ...
    def retention_sweep(self, policy: RetentionPolicy, *, uow: UnitOfWork) -> int: ...
```

- **Calls:** only `ErasureParticipant`s through the container registry (not by importing modules), `AuditLog`, `ApprovalService`, `identity` administration through `UserAdmin` and `ApiKeyAdmin` Protocols (role grants, user creation, MFA reset and API-key creation are approval actions).
- **Must not:** import any business module; delete rows itself; read customer content. Ledger participants anonymise references, never delete entries (NFR-05).

### 2.14 Approvals (maker-checker) (D-15, D-16)

**Where it lives: `identity`.** The approval needs the maker's and approver's user and session ids, the step-up timestamp, role and attribute membership and the `staff_customer_link` conflict table, all of which `identity` owns. `identity` is already importable by every module that needs approvals. `admin` cannot host it because no module may import `admin` (it sits at the top of the dependency graph and must stay thin). The capability is split in two so that `identity` never imports business modules: `identity` records and validates the approval; the module that owns the action supplies an executor that runs in the same transaction.

```python
# identity/contracts.py
@dataclass(frozen=True, slots=True)
class ApprovalView:
    id: UUID; action: str; status: Literal["pending", "approved", "rejected", "expired", "executed"]
    maker_id: UUID; approver_id: UUID | None; payload_hash: str; subject_customer_id: UUID | None

class ApprovalService(Protocol):
    def propose(self, *, action: str, payload: Mapping[str, Any], subject: Reference,
                      subject_customer_id: UUID | None, maker: Principal, uow: UnitOfWork) -> ApprovalView: ...
    def approve(self, approval_id: UUID, *, approver: Principal, uow: UnitOfWork) -> ApprovedAction: ...
    def reject(self, approval_id: UUID, *, approver: Principal, reason: str, uow: UnitOfWork) -> ApprovalView: ...

# shared/contracts.py
class ApprovalExecutor(Protocol):
    action: str                                         # e.g. "tariff.change"
    required_permission: str                            # approver must hold it
    def execute(self, payload: Mapping[str, Any], approval: ApprovalView, *, uow: UnitOfWork) -> None: ...

class ApprovalExecutorRegistry(Protocol):
    def register(self, executor: ApprovalExecutor) -> None: ...
    def get(self, action: str) -> ApprovalExecutor: ...
```

`approve` checks, in one transaction: the approver differs from the maker by user and by session; the approver holds the executor's permission; step-up occurred within 5 minutes; the stored hash equals the hash of the payload being executed (the executor receives the stored payload, never a client-supplied one); the approval is pending and not expired; and the `staff_customer_link` conflict check passes (a staff member linked to `subject_customer_id` cannot approve money for that customer). The route `POST /approvals/{id}/approve` in `identity.api` calls `approve` and then `registry.get(action).execute(...)` inside the same unit of work, so a failed execution leaves the approval pending. The threshold is zero in v1: every listed action requires a second person.

| Action | Executor lives in | What executes | Approver permission |
|---|---|---|---|
| `reconciliation.resolve_money` | `reconciliation` | `LedgerService.post` (kind `reconciliation_fix`), item to `resolved` or `written_off`, optional `TokenService.confirm_non_delivery` | `reconciliation:approve` (`finance_officer`) |
| `ledger.adjustment`, `ledger.refund` | `ledger` | `LedgerService.post` (kinds `adjustment`, `refund`) | `ledger:approve` (`finance_officer`) |
| `tariff.change` | `customers` | New plan version with `effective_from` within the per-revision maximum | `tariff:approve` (`finance_officer`) |
| `role.grant`, `user.create`, `mfa.reset` | `identity` | Role assignment, user creation, MFA reset (second administrator for resets) | `user:approve` (`administrator`) |
| `api_key.create` | `identity` | Issues the key, secret shown once | `apikey:approve` (`administrator`) |
| `erasure.approve` | `admin` | Moves the request out of cooling-off once the period has elapsed; requires `dp_admin` attribute on one approver | `erasure:approve` |
| `export.release` | `admin` | Releases a `data_exports` bundle | `export:approve` |
| `kb.publish` | `support` | Publishes the article version, records `published_by` and `approved_by` | `kb:publish` |
| `draft.money_affecting` | `support` | Allows a money-affecting AI draft to be sent | `finance_officer` role permission `ticket.money_approve` |

Permission names above are declared in `api/permissions.yaml`; the table is illustrative of the pairing, the file is authoritative.

---

## 3. Dependency rules

### 3.1 Allowed directions (rows may import columns' `contracts`/`events`)

| from \ to | shared | identity | customers | payments | ledger | meters | telemetry | support | ai_gateway | analytics | notifications | reconciliation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| identity | yes | - | | | | | | | | | | |
| customers | yes | yes | - | | | | | | | | | |
| payments | yes | yes | yes | - | yes | | | | | | | |
| ledger | yes | | | | - | | | | | | | |
| meters | yes | yes | yes | | yes | - | | | | | | |
| telemetry | yes | yes | | | | yes | - | | | | | |
| support | yes | yes | yes | | | | | - | yes | | yes | |
| ai_gateway | yes | | | | | | | | - | | | |
| analytics | yes | yes | | | | | | | yes | - | | |
| notifications | yes | yes | yes | | | | | | | | - | |
| reconciliation | yes | yes | | yes | yes | yes | | | | | | - |
| admin | yes | yes | | | | | | | | | | |

Notes: `reconciliation` also depends on `meters` (for `TokenService.confirm_non_delivery`) and `payments` also on `ledger`; the table's `reconciliation` row therefore adds `meters` (see the updated DAG contract below). `api` layers use `identity.contracts` for `Principal`, `Authorizer` and `ApprovalService`, which is why almost every module depends on `identity`. Approval executors are registered through `shared.contracts`, so `identity` and `admin` never import the modules whose actions they approve. No module imports `admin`. `analytics` consumes events (never imports producers). `meters` does not import `payments`: it reacts to `payment.confirmed` and trusts the payload (payment id, customer id, amount, currency), which is why that payload is complete.

```
shared <- identity <- customers <- payments --> ledger (leaf)
                          ^           ^
                          +-- meters -+--> ledger
telemetry --> meters      support --> ai_gateway (leaf), notifications
analytics --> ai_gateway  reconciliation --> payments, ledger
admin --> identity (+ registry, no business imports)
```

### 3.2 Layering inside a module

| Layer | May import | Must not import |
|---|---|---|
| `domain` | stdlib, `shared.money`, `shared.errors`, own `domain` | `django`, `rest_framework`, `psycopg`, `pydantic`, other modules |
| `application` | own `domain`, own ports, `shared` (non-Django parts), other modules' `contracts`/`events` | `api`, `infrastructure`, `django`, `rest_framework`, `psycopg` |
| `infrastructure` | own `application` ports and `domain`, `django.db`, `psycopg`, SDKs, `shared` | own `api` |
| `api` | own `application`, own `domain` errors, `shared` (including DRF helpers), `identity.contracts`, `rest_framework`, `django.http` | own `infrastructure`, any model |

Use cases receive their collaborators in `__init__` (repositories, ports, other modules' Protocols). The `api` layer obtains ready-made use cases from `hub.<module>.api.deps.use_cases()`, which calls `bootstrap.get_container().<module>`; it never constructs a repository.

### 3.3 Enforcement with import-linter

`backend/.importlinter` (run by `make lint` and in `tests/architecture`; a failing contract blocks merge). Django apps are ordinary Python packages, so the contracts are those of the first draft adapted for Django names:

```ini
[importlinter]
root_package = hub
include_external_packages = True

# 1. Layers inside every app. models.py shims and apps.py are outside the layers by design.
[importlinter:contract:layers]
name = Layers inside each module
type = layers
layers =
    hub.*.api
    hub.*.application
    hub.*.domain
containers = hub.identity, hub.customers, hub.payments, hub.ledger, hub.meters, hub.telemetry,
             hub.support, hub.ai_gateway, hub.analytics, hub.notifications, hub.reconciliation, hub.admin

[importlinter:contract:domain-pure]
name = Domain has no frameworks
type = forbidden
source_modules = hub.*.domain
forbidden_modules = django, rest_framework, psycopg, httpx, redis, anthropic, boto3, pydantic,
                    hub.*.infrastructure, hub.*.api, hub.*.application

[importlinter:contract:application-no-outer]
name = Application does not depend on outer layers or frameworks
type = forbidden
source_modules = hub.*.application
forbidden_modules = hub.*.infrastructure, hub.*.api, django, rest_framework, psycopg, anthropic, pydantic

# 2. Only contracts/events cross app boundaries, and models never do
[importlinter:contract:public-surface]
name = Modules expose only contracts and events
type = forbidden
source_modules = hub.identity, hub.customers, ... (one entry per module, generated by tools/scripts/gen_importlinter.py)
forbidden_modules = hub.<other>.api, hub.<other>.application, hub.<other>.domain, hub.<other>.infrastructure, hub.<other>.models
# generated per ordered pair (A, B) where A may not see B's internals; allow_indirect_imports = False

[importlinter:contract:models-private]
name = Models are private to their app
type = forbidden
source_modules = hub.*.api, hub.*.application, hub.*.domain
forbidden_modules = hub.*.models, hub.*.infrastructure.models
# api, application and domain never import models, not even their own; repositories do

# 3. Module direction (section 3.1) over the app DAG
[importlinter:contract:module-dag]
name = Module dependency direction
type = layers
layers =
    hub.admin | hub.analytics | hub.support | hub.telemetry | hub.notifications
    hub.reconciliation
    hub.payments | hub.meters
    hub.customers | hub.ai_gateway
    hub.ledger
    hub.identity
    hub.shared
# siblings on one line are independent; reconciliation sits above payments and meters because it calls PaymentService and TokenService

# 4. SDK containment
[importlinter:contract:anthropic-only-in-gateway]
type = forbidden
source_modules = hub
forbidden_modules = anthropic
ignore_imports = hub.ai_gateway.infrastructure.anthropic_client -> anthropic

[importlinter:contract:pydantic-only-in-gateway]
type = forbidden
source_modules = hub
forbidden_modules = pydantic
ignore_imports = hub.ai_gateway.** -> pydantic

[importlinter:contract:no-django-admin]
type = forbidden
source_modules = hub
forbidden_modules = django.contrib.admin

# 5. Wiring and settings are not importable from business layers (they read django.conf.settings instead)
[importlinter:contract:wiring-only]
type = forbidden
source_modules = hub.*.api, hub.*.application, hub.*.domain, hub.*.infrastructure
forbidden_modules = hub.bootstrap, hub.settings, hub.urls, hub.wsgi_api, hub.wsgi_ingest, hub.asgi_stream
```

`hub.bootstrap`, `hub.urls`, `hub.settings`, `hub.wsgi_*`, `hub.asgi_stream` and the `hub.*.models` shims are excluded from the public-surface contract. `tests/architecture/test_events_registered.py` asserts that every event in section 4 has a frozen dataclass in a module's `events.py` and a registry entry, and that no handler is registered for an unknown event. `tests/architecture/test_models_alignment.py` introspects the test database and fails when a model field, table or nullability differs (ADR-0020).

A second guard: the DB grants in `db/schema.sql` give each deployable's role (`hub_api`, `hub_ingest`, `hub_worker`) only the tables it needs and no direct write on `ledger_*`; and `tools/check_django_config.py` (section 9) rejects any model declared outside `infrastructure/models.py` or without `managed = False`.

---

## 4. Domain event catalogue

**Transport and semantics (all events unless stated):** written to `outbox` (`event_type` = the event name, `aggregate_type`, `aggregate_id`, `payload`) in the producing transaction, relayed by `hub-worker` to the five Redis stream families of the system architecture, consumed by named consumer groups (one per consuming module), **at-least-once**, **idempotent consumers**: each consumer records `(consumer, event_id)` in `consumer_inbox` inside its own transaction before doing the work (the only dedupe table). Ordering holds per aggregate on a best-effort basis (a single active relay), so handlers check state and never assume order. Retries: exponential backoff (1 s base, factor 4, jitter); after 5 deliveries the entry moves to `dlq.<stream>` with an alert (matches the system architecture; D-27). A replay window of 10 minutes plus business sweepers (stuck top-ups, unacknowledged tokens, outbox rows marked published but not consumed) cover Redis loss. Envelope fields in every payload: `event_id`, `name`, `version`, `occurred_at`, `correlation_id`, `causation_id`, `aggregate_id`.

| Stream family | Event name prefixes carried |
|---|---|
| `events.payments` | `topup.*`, `payment.*`, `ledger.*`, `reconciliation.*` |
| `events.tokens` | `token.*`, `meter.*`, `site.*`, `telemetry.*` |
| `events.support` | `ticket.*`, `draft.*`, `ai.*`, `askdata.*` |
| `events.notifications` | `notification.*` |
| `events.alerts` | `alert.*`, `wallet.*`, `consumption.*`, `approval.*`, `customer.*`, `user.*`, `api_key.*` |

| Event | Producer | Consumers | Payload fields | Delivery notes |
|---|---|---|---|---|
| `topup.initiated` | payments | analytics | `top_up_id, customer_id, amount_minor, currency, provider, payer_msisdn_hash, quoted_energy_wh, tariff_version` | outbox |
| `payment.confirmed` | payments | meters (issue token), analytics, notifications (receipt) | `top_up_id, customer_id, meter_id, amount_minor, currency, provider, provider_txn_id, confirmed_at, late: bool, quoted_energy_wh, tariff_version, ledger_transaction_id` | Same transaction as the ledger posting. Consumers dedupe on `top_up_id`. `late = true` when the confirmation followed `expired` or `cancelled` (D-05); the token is still issued. |
| `payment.failed` | payments | notifications, analytics | `top_up_id, customer_id, reason_code, provider` | outbox |
| `payment.reversed` | payments | notifications (finance officers), analytics, reconciliation (opens an item when `shortfall_minor > 0`) | `top_up_id, customer_id, reversed_minor, shortfall_minor, ledger_transaction_id, chargeback_transaction_id | null` | D-06: reversing entries; the part the wallet no longer holds posts to `chargeback_receivable` |
| `ledger.posted` | ledger | analytics | `transaction_id, posting_key, kind, reference_type, reference_id, lines[{account_kind, customer_id, direction, amount_minor, currency}], effective_at` | One per posting, never for a replay of the same `posting_key` |
| `wallet.low_balance` | ledger | notifications | `customer_id, balance_minor, currency, threshold_minor` | At most once per 24 h per customer (dedupe key) |
| `token.requested` | meters | analytics | `token_id, top_up_id, meter_id, counter, energy_wh, expires_at, ledger_transaction_id` | outbox; the sender is a due-scan on `meter_tokens`, not on this event |
| `token.issued` | meters | notifications, analytics, support | `token_id, top_up_id, customer_id, meter_id, energy_wh, counter, acked_at, attempts` | On gateway acknowledgement |
| `token.failed` | meters | notifications, analytics, reconciliation (opens an item for `unknown`) | `token_id, top_up_id, customer_id, meter_id, reason_code, status(failed or unknown), attempts, final: bool` | `failed` only on a definitive rejection (posting reversed); `unknown` after 72 hours without acknowledgement (no reversal) |
| `meter.offline` | meters | notifications, analytics | `meter_id, site_id, customer_id, last_seen_at` | State change only; silent over 90 minutes |
| `meter.online` | meters | analytics | `meter_id, site_id, back_at` | outbox |
| `site.offline` | meters | notifications, analytics | `site_id, gateway_id, last_heartbeat_at` | Heartbeat (every 5 minutes) missing 15 minutes |
| `telemetry.heartbeat_missed` | telemetry | meters | `meter_id, last_reading_at, threshold_minutes` | Scheduled scan, idempotent per `(meter_id, window)` |
| `consumption.anomaly_detected` | telemetry | notifications, analytics | `meter_id, customer_id, rule_id, observed_wh, expected_wh, window_start, window_end` | outbox; creates an `alerts` row of kind `anomalous_consumption` |
| `alert.raised` | telemetry, meters | analytics, notifications | `alert_id, kind, site_id, meter_id | null, severity` | kind values of `alerts.kind` only |
| `ticket.created` | support | support (triage), analytics, notifications (acknowledgement) | `ticket_id, customer_id, channel, locale, created_at` | Body is not in the event |
| `ticket.triaged` | support | support (draft step), analytics, notifications (urgent routing) | `ticket_id, category, urgency, language, pii_flags[], confidence, ai_run_id` | Vocabulary of section 7.2 of the shared context; applied as suggestions with an override record (D-32) |
| `ticket.triage_failed` | support | analytics, admin alerts | `ticket_id, reason_code, attempts` | outbox |
| `draft.created` | support | analytics | `message_id, ticket_id, ai_run_id, citation_ids[], money_affecting` | The staff UI picks it up by query and SSE |
| `draft.approved` | support | notifications (send), analytics | `message_id, ticket_id, approved_by, edited: bool, channel, locale, send_not_before` | Only a staff `Principal` with `ticket.ai_approve` produces it; `send_not_before` = approval time + 30 s (D-35); leads to `notification.requested` |
| `draft.recalled` | support | analytics | `message_id, ticket_id, recalled_by` | Only inside the recall window |
| `draft.rejected` | support | analytics | `message_id, ticket_id, rejected_by, reason` | outbox |
| `notification.requested` | notifications (any module through `NotificationService.request`) | notifications (sender) | `notification_id, customer_id, channel, template, locale, params, dedupe_key, available_at` | Idempotent on `dedupe_key`; contact fetched at send time |
| `notification.sent` | notifications | analytics | `notification_id, channel, provider_message_id` | outbox |
| `notification.failed` | notifications | admin alerts | `notification_id, reason_code, attempts` | After the retry budget |
| `reconciliation.completed` | reconciliation | analytics | `batch_id, provider, day, matched, items_open` | outbox |
| `reconciliation.exception` | reconciliation | analytics, notifications (finance officers) | `item_id, batch_id, kind, top_up_id | null, amount_minor, currency` | `kind` is the `reconciliation_items.kind` set |
| `approval.requested` / `approval.granted` / `approval.rejected` | identity | notifications (approvers), analytics | `approval_id, action, maker_id, approver_id | null, subject_customer_id | null` | Payload hash is not published |
| `askdata.query_executed` | analytics | admin (audit view) | `run_id, actor_id, role, validator_verdict, row_count, duration_ms` | outbox |
| `ai.budget_exceeded` | ai_gateway | admin alerts, support (degrade to manual) | `scope, feature, spent_micro_usd, limit_micro_usd` | outbox |
| `customer.erased` | customers | analytics | `customer_id, erased_at` | outbox |

Rules for events: names are `<aggregate>.<past_tense_verb>` in lower snake case with a dot; breaking payload changes bump `version` and both versions run for one release; payloads carry ids and amounts but never raw personal data (ids and hashes only). `dashboard.updated` is Redis pub/sub only and lossy by design (clients refetch on reconnect).

---

## 5. Ports and adapters

All port Protocols live in the consuming module's `application/ports.py` unless they are cross-cutting (`shared/ports.py`).

| Port | Defined in | Key signature | Real adapter | Fake (tests and dev) | Wired in |
|---|---|---|---|---|---|
| `ProviderGateway` | `payments/application/ports.py` | below | `payments/infrastructure/provider_http.py` (httpx, per-provider subclass) | `FakeProviderGateway` (in-memory, scriptable) and the `tools/provider_stub` HTTP service for end-to-end tests | api, ingest, worker (reconciliation and the pending-unknown sweeper use it) |
| `MeterGateway` | `meters/application/ports.py` | below (outbound token push only) | `meters/infrastructure/gateway_push.py`: outbound adapter that pushes a token to the site gateway of the meter's site | `FakeMeterGateway` (programmable: accept, timeout, reject) and `tools/meter_simulator` | worker (token sender), api (manual retry) |
| `LLMClient` | `ai_gateway/application/ports.py` | below | `ai_gateway/infrastructure/anthropic_client.py` | `FakeLLMClient` (canned structured outputs, simulated refusal, rate-limit errors), recorded-fixture client for regression evals | api and worker |
| `Notifier` | `notifications/application/ports.py` | `async send(msg: OutboundMessage) -> DeliveryReceipt` per channel | `SmsStubNotifier`, `EmailStubNotifier` (log and store; real SMTP or SMS vendor later) | `InMemoryNotifier` collecting messages | worker |
| `Clock` | `shared/ports.py` | `now() -> datetime` | `SystemClock` | `FrozenClock` with `advance()` | all |
| `IdGenerator` | `shared/ports.py` | `new_id() -> UUID` | `Uuid7Generator` wrapping one pinned UUIDv7 library (version and hash locked) | `SequentialIdGenerator` (deterministic) | all |
| `ObjectStore` | `shared/ports.py` | below | `S3ObjectStore` (boto3 or aioboto3, any S3-compatible) | `InMemoryObjectStore` | api (attachments), worker (exports) |
| `ReadOnlySqlExecutor` | `analytics/application/ports.py` | `async run(sql: ValidatedSql, role: AskRole, query_timeout_ms: int, max_rows: int) -> ResultSet` | Replica connection pool per login role `analytics_support`, `analytics_finance`, `analytics_ops` (read-only default, `statement_timeout` 5 s, idle timeout and temp-file limit set with `ALTER ROLE ... SET`, PgBouncer query timeout) | `InMemorySqliteExecutor` for unit tests; real Postgres in integration | api |
| `FeatureFlags` | `shared/flags.py` | `is_enabled(name: str, *, subject: str | None = None) -> bool` | Typed-settings defaults plus an audited Redis override hash (30 s refresh) | `StaticFlags(dict)` | all |
| `Cache` | `shared/cache.py` | `get/set/delete`, `get_or_load(key, ttl, loader)` | Redis | `InMemoryCache` | api, worker |

```python
class ProviderGateway(Protocol):
    def initiate_collection(self, req: CollectionRequest) -> CollectionAck: ...   # idempotent via req.reference
    def verify_webhook(self, headers: Mapping[str, str], body: bytes, *, now: datetime) -> VerifiedEvent: ...  # HMAC over "timestamp.event_id.body"; 5 min tolerance; raises InvalidSignature
    def fetch_status(self, provider_txn_id: str) -> ProviderTxnStatus: ...         # pull verification (D-09) and pending_unknown sweeper
    def fetch_statement(self, day: date) -> list[StatementLine]: ...

class MeterGateway(Protocol):
    def push_token(self, gateway_ref: str, token: TokenInstruction) -> PushResult: ...
    # TokenInstruction carries token_id (the same id on every resend), meter_id, counter, expires_at, payload;
    # PushResult: accepted_for_delivery | rejected(reason) | unreachable. The final outcome arrives via
    # POST /api/v1/gateway/tokens/{id}/acknowledge (meters.api.gateway), never as the return value.

class LLMClient(Protocol):
    def complete(self, req: LLMRequest) -> LLMResponse: ...
    # LLMRequest: system blocks (with cache_control on stable prefix), messages, output_schema | None,
    #   effort: Literal["low","medium","high"], max_output_tokens; no tool_choice, no prefill fields exist on the type
    # LLMRequest also carries the approved fallback model list (only when sync); LLMResponse: parsed | text,
    #   stop_reason (incl. "refusal"), usage incl. cache_read/creation, served_model, fallback_ran

class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def presigned_get(self, key: str, ttl_s: int) -> str: ...
    def delete(self, key: str) -> None: ...
```

Wiring: `bootstrap.build_container(settings)` (called once per process from the `ready()` hook of the `shared` app) builds a `Container` with one attribute per module (the module's Protocol implementations); `get_container()` returns it; tests call `build_container(fake_settings)` or replace attributes directly. It picks adapters from `settings.adapters` (`real | fake | stub`), so `make up` runs the provider stub and meter simulator as compose services, unit tests inject fakes directly, and production never constructs a fake (a startup assertion fails if `env == "prod"` and any fake is selected).

---

## 6. Critical flows

### 6.1 Top-up confirmation (FR-02, FR-03, FR-04; NFR-01, NFR-05)

Entry: `POST /api/v1/webhooks/providers/{provider}` on `hub-ingest` (pool `be_webhooks`), handled by `payments.api.webhooks`, calling `payments.application.confirm_payment.ConfirmPayment`. Processing is **synchronous** (D-09): the acknowledgement means the effect is durable.

1. **Verify (no DB):** read the raw body (cap 64 KB), `ProviderGateway.verify_webhook` checks the HMAC over `timestamp.event_id.body` with a 5-minute timestamp tolerance. An invalid signature returns 401, increments a counter and writes a quarantine log line (30-day retention); **nothing is stored in `provider_events`**.
2. **Hint (Redis):** `SET NX provider_event:<provider>:<event_id>` (TTL 24 h). A hit returns 200 `duplicate` early. This is an optimisation only; correctness lives in PostgreSQL.
3. **Pull verification above the threshold:** if the confirmed amount is at or above the threshold (default every confirmation of 100 USD or more; **[open: threshold]**), call `ProviderGateway.fetch_status(provider_txn_id)` before the transaction (timeout 1.5 s). Disagreement is treated as an amount mismatch (step 5). An unreachable provider returns 503 so the provider retries within its 72-hour horizon; nothing has been stored.
4. **Transaction T1 on the primary (role `hub_ingest`):**
   1. `INSERT INTO provider_events ... ON CONFLICT DO NOTHING`; uniqueness on `(provider, event_id)` and on `(provider, provider_txn_id)`. Zero rows means a duplicate: return 200 with `status: duplicate`, identical to the first acknowledgement.
   2. `SELECT ... FROM top_ups WHERE provider = :p AND provider_ref = :ref FOR UPDATE`. Not found: the event is stored with status `ignored`, a `reconciliation.exception` is raised, 200 is returned.
   3. Domain transition `TopUp.confirm(event, now)`: from `pending` or `pending_unknown` to `confirmed`; already `confirmed` is a no-op; from `expired` or `cancelled` it credits the wallet once and sets `late = true` (D-05); from `failed` it credits and sets `late` as well, flagged for finance.
   4. **Payer check:** the event's payer phone number must equal `top_ups.payer_msisdn`; a mismatch, an amount or currency mismatch, a decommissioned meter or an erased customer routes the amount to `suspense` instead of the wallet and raises `reconciliation.exception` (the only cases that use `suspense`).
   5. `LedgerService.post(PostingRequest(posting_key=f"topup:{top_up_id}:confirm", kind="topup_received", lines=[debit provider_clearing, credit customer_wallet] (plus fee lines debit fees, credit provider_clearing when the provider charges the operator)))` which calls `post_ledger_transaction` in the same transaction.
   6. Set `top_ups.ledger_transaction_id` and `confirmed_at`, mark the `provider_events` row `processed`, add `payment.confirmed` (and the ledger adds `ledger.posted`) to the outbox, commit.
5. **Response:** 200 within the 300 ms p95 budget (NFR-02). No call to the site gateway, notifier or model inside the request.

**Provider reversals (`payment.reversed`, D-06):** the same pipeline with `event_type = reversal`: `LedgerService.reverse_with_shortfall` posts reversing entries up to what the wallet holds (energy already issued is not recoverable) and the shortfall to `chargeback_receivable`; the top-up becomes `reversed`; `payment.reversed` is emitted and, when there is a shortfall, an exception opens in `reconciliation`.

**Idempotency layers (defence in depth, invariant 3):** unique `(provider, event_id)` and `(provider, provider_txn_id)`; `FOR UPDATE` plus the state machine; unique `posting_key`; unique `top_ups.ledger_transaction_id`; unique `meter_tokens.top_up_id` later. Any one prevents a double credit.

**Retries:** none inside the request except a deadlock or serialization failure (2 retries, 20 ms jitter). Otherwise 503 and the provider retries; replay is safe. `provider_events` rows left in `received` or `failed` (a crash after step 4.1 cannot happen because it is one transaction; the status exists for the replay worker and for events found `ignored` later) are swept by a job every minute. `pending_unknown` top-ups (initiation call timed out) are swept every 2 minutes with `ProviderGateway.fetch_status`.

**Initiation** (`POST /api/v1/top-ups`, hub-api): `Idempotency-Key` is mandatory with a **7-day** window (`idempotency_keys`, D-12; 24 hours for every other scope); same key and body returns the stored response, same key with a different body returns 422 `idempotency_key_reuse`. Inside T0 the use case quotes the tariff (`CustomerDirectory.quote_energy`) and stores the tariff version, `energy_wh` and the payer number on `top_ups` (D-08), commits, then calls `ProviderGateway.initiate_collection` with the top-up id as the provider reference. A definite refusal moves the row to `failed`; a timeout moves it to `pending_unknown`.

**Django implementation notes (6.1).** The webhook is a plain Django function view (`payments.api.webhooks.provider_webhook`), not a DRF view, so that the raw body bytes reach `verify_webhook` untouched (`request.body`, size cap enforced before reading). The view calls `ConfirmPayment.run(raw, headers)`; the use case opens `with uow:` once around step 4 only (verification, Redis hint and pull verification are outside it). Inside it, `TopUpRepository.lock_by_provider_ref()` uses `TopUp.objects.select_for_update().get(provider=..., provider_ref=...)` (it calls `uow.require_active()` first); `ProviderEventRepository.insert_if_new()` uses raw SQL `INSERT ... ON CONFLICT DO NOTHING RETURNING id` (an ORM `get_or_create` is forbidden here); the ledger call is `LedgerService.post`, whose writer executes `SELECT hub.post_ledger_transaction(...)` through `connection.cursor()` in the same transaction. ATOMIC_REQUESTS is off, so the transaction ends exactly at the end of the `with` block, before the response is built. Deadlock or serialization errors (`psycopg.errors.DeadlockDetected`, `SerializationFailure`) are caught by `shared.db.retry_transient(max_attempts=3)` wrapped around the whole `with uow:` block. Initiation is a DRF `CreateAPIView` using the `@idempotent` decorator of section 7.7 with the 7-day window.

### 6.2 Token issuance (FR-05)

Trigger: consumer `meters.application.handlers.on_payment_confirmed` in `hub-worker` (consumer group `meters` on `events.payments`), plus the **sender**, a due-scan job over `meter_tokens` (`status IN ('pending','sent')` and `next_attempt_at <= now()`).

1. Dedupe through `consumer_inbox`.
2. **T1 (primary):** lock the `top_ups` row and the `meters` row (`FOR UPDATE`, fixed order). **Re-check (D-08):** the meter still belongs to the top-up's customer and is active; otherwise no token is created, the wallet keeps the credit, `token.failed(reason=meter_inactive, final=true)` is emitted and a reconciliation item opens. Take `energy_wh` from the top-up's stored quote (the tariff at initiation), never re-quote. Allocate `counter = meters.token_counter + 1` (monotonic per meter, same lock) and `expires_at = now + 72 h`. `INSERT INTO meter_tokens (top_up_id UNIQUE, ledger_transaction_id, counter, expires_at, status='pending', ...) ON CONFLICT DO NOTHING`. Post `token:<token_id>:purchase` (debit `customer_wallet`, credit `revenue_energy`) so that **the token is tied to a posting** (`ledger_transaction_id` NOT NULL; revenue is recognised at issuance pending the finance lead, D-10); the remainder below one watt-hour stays in the wallet. Add `token.requested`. Commit.
3. **Send (outside any DB transaction):** `MeterGateway.push_token(...)` with the same `token_id` on every attempt; the gateway deduplicates on `token_id` and refuses a counter lower than the last it applied. `accepted_for_delivery` sets `sent`; `unreachable` records the attempt.
4. **Acknowledgement:** the site gateway calls `POST /api/v1/gateway/tokens/{id}/acknowledge` (hub-ingest). T2 (idempotent): status `acknowledged`, `acked_at`, outbox `token.issued`. The gateway key must be bound to the meter's site.
5. **Retry schedule (D-07):** delay 30 s doubling, capped at 30 minutes (30 s, 1, 2, 4, 8, 16, then 30 minutes), for up to **72 hours** from creation, driven by `next_attempt_at`; each retry reuses the same token id, counter and payload. A definitive rejection from the gateway (`invalid_meter`, `counter_stale`, `expired`) sets `failed` and the **posting is reversed** (`LedgerService.reverse`, credit returns to the wallet) with `token.failed(final=true)`. After 72 hours or on `expires_at` without acknowledgement the token becomes `unknown`: **no reversal**, `token.failed(status=unknown)` is emitted and an item opens in `reconciliation`. Only an approved reconciliation resolution that confirms non-delivery (`TokenService.confirm_non_delivery`) may reverse an `unknown` token, using evidence from gateway heartbeats and meter readings. A staff `retry_now` resets `next_attempt_at` without creating a new token.
6. **Failure invariant:** the wallet debit and the token row are created together and durable before the gateway is told; an unknown outcome never creates a second token (unique `top_up_id`, one token id, monotonic counter).

**Django implementation notes (6.2).** The consumer runs under `manage.py run_worker --group meters`, one thread per concurrency slot, each calling `close_old_connections()` after every message. `TokenRepository.lock_for_issue()` locks `TopUp` and `Meter` rows with `select_for_update(of=("self",))` in the fixed order top-up, then meter (same model-agnostic helper in the repository so the order is in one place). The posting uses the same definer-function writer as 6.1. The sender is the poller `meters.send_due_tokens` registered with `register_poller` (section 7.5): it claims due rows with `select_for_update(skip_locked=True)` in a short transaction that bumps `attempts` and `next_attempt_at`, commits, then performs the HTTP push outside the transaction and records the result in a second short transaction. The acknowledgement view `/gateway/tokens/{id}/acknowledge` is a DRF `GenericAPIView` with `GatewayKeyAuthentication` and the `gateway` throttle tier.

### 6.3 Telemetry ingest (FR-06, FR-07)

Entry: `POST /api/v1/telemetry/batches` on `hub-ingest` role `telemetry` (pool `be_telemetry`), `gateway` API key, `X-Batch-Id` header.

1. Authenticate the gateway key (Redis-cached HMAC lookup, source range checked); rate limit per key from the tier table in `05-api-design.md` (authoritative).
2. Validate: the batch envelope (batch id, gateway id, reading count, byte size) with a DRF serializer, then the readings with a hand-written loop in `telemetry.domain.validation` (no per-row serializer: about 83 batches per second at 1,000 readings each would make DRF row validation the bottleneck); at most **1,000 readings or 256 KB** per batch (D-25); a reading older than 7 days is rejected individually, a reading more than 5 minutes in the future is rejected individually (the rest are accepted and the response lists the rejections); `seq` is advisory.
3. Resolve device refs through `MeterDirectory.resolve(..., site_ids=key sites)`: a meter outside the key's sites is rejected per reading.
4. **Transaction (`with uow:`):** `INSERT INTO reading_batches ... ON CONFLICT DO NOTHING` (duplicate batch: 200 `duplicate`); bulk insert through `ReadingWriter` using raw SQL on the psycopg connection (`INSERT INTO meter_readings SELECT * FROM unnest(%s::uuid[], %s::timestamptz[], %s::bigint[], ...) ON CONFLICT (meter_id, read_at) DO NOTHING RETURNING 1`, never the ORM); in the buffered mode of the system architecture the worker uses `COPY` into a temporary staging table through psycopg 3 (`cursor.copy`) in chunks of 5,000 rows, then the same `INSERT ... SELECT ... ON CONFLICT DO NOTHING` (first write wins; duplicates counted from the row count); late readings mark `dirty_hour` aggregate rows. `last_seen` updates are batched through a Redis hash flushed every 10 seconds.
5. Return 202 with counts. Emit `meter.online` only when a meter's state changes.
6. **Worker side:** scheduled jobs `telemetry.aggregate_hourly`, `telemetry.scan_heartbeats` (every minute; meter silent over 90 minutes yields `telemetry.heartbeat_missed`, then `meter.offline`; gateway heartbeat every 5 minutes missing 15 minutes yields `site.offline`), `telemetry.detect_anomalies`, partition management (`ensure_meter_reading_partitions`).
7. **Retries:** the gateway retries the whole batch (safe by batch id). Jobs are re-runnable by design.
8. **Back-pressure:** when the DB pool wait exceeds 200 ms ingest returns 503 with `Retry-After` and sheds telemetry before webhooks. Above 2,000 batches per second the buffered mode of the system architecture applies (ack then means "in Redis with AOF"); the code path is the same use case behind a `ReadingSink` port (`PostgresSink`, `StreamSink`).

**Django implementation notes (6.3).** The telemetry view is a DRF `GenericAPIView` with `GatewayKeyAuthentication`, parser limited to JSON with a 256 KB `DATA_UPLOAD_MAX_MEMORY_SIZE` override in `hub.settings.ingest`, and the `gateway` tier. Scheduled aggregation jobs run raw SQL (`INSERT ... SELECT ... ON CONFLICT DO UPDATE`) over `dirty_hour` markers.

### 6.4 Ticket AI pipeline (FR-08 to FR-11)

1. `support.application.OpenTicket` (T1): ticket, first message, attachments metadata, outbox `ticket.created`. Returns 201 immediately; no AI in the request.
2. Consumer `support.handlers.on_ticket_created` calls `TriagePipeline.triage`:
   1. Load message; call `AiService.run(AiRequest(feature="triage", variables=..., output_schema=TriageOutput))`. `ai_gateway` redacts, checks budget (per-ticket and per-day), calls `LLMClient`, validates structured output, writes the `ai_runs` row and stores the redacted payload in object storage by prompt hash (own transaction, so the audit survives even if the caller rolls back).
   2. Outcome handling: `refused`, `truncated` or `invalid` is a non-retryable "needs human" result (category `other`, low-confidence queue); `error` or `timeout` (429, 5xx) is retried by the job framework (3 attempts, 5 s, 30 s, 3 min); `budget_blocked` yields `ai.budget_exceeded` and a manual flag; `guard_blocked` is never retried.
   3. T2: apply the suggestion to the ticket fields (category, urgency, language, PII flags from the section 7.2 vocabulary) with an override record, outbox `ticket.triaged`. Recall of at least 98% on `needs_human_now` is a release gate (D-32).
3. Consumer `on_ticket_triaged` (only when `feature flag ai.draft_reply` is enabled and category allows drafting): retrieves KB passages through `search_kb` (Postgres full-text search plus trigram, locale-aware), calls `AiService.run(feature="reply_draft")` with the passages and minimised account context (balance bucket, last token status, no phone or name), requires the output to cite passage ids, rejects drafts whose citations are not in the retrieved set, inserts a `ticket_messages` row (`author_type='ai_draft'`, `direction='outbound'`, `status='draft'`, `ai_run_id`), outbox `draft.created`.
4. Human step: an agent opens the draft; `approve_draft` (T3) requires permission `ticket.ai_approve`, re-runs the lints on any text changed at approval, records `approved_by` and `approved_at` and the final text, sets `status='approved'`, and writes `draft.approved` plus a `notification.requested` outbox row with `available_at = now + 30 s` (the recall window; `recall_draft` cancels it and sets `recalled`). A `money_affecting` draft additionally needs a finance approval through `ApprovalService` (`draft.money_affecting`) before `notification.requested` is written. There is no code path from `ai_gateway` or from message status `draft` to `Notifier`.
5. Summaries (`FR-11`) are generated on demand (`POST /tickets/{id}/summary`) and cached per `(ticket_id, last_message_id)`; they are internal and need no approval, but are never auto-sent.
6. **Idempotency:** `consumer_inbox` plus a lookup of an existing `ai_runs` row for the same `(ticket_id, feature, source message id)` with status `ok`, and a unique `(ticket_id, source_message_id)` among `ai_draft` messages; a redelivery after a successful call finds the row and exits without a second model call (cost control). Retries never re-run a step whose result row exists.

**Django implementation notes (6.4).** Triage and drafting consumers run in `hub-worker`; `AiService.run` is synchronous (the Anthropic Python SDK sync client with a request timeout). The audit row is written through a second database alias `ai_audit` (same role as the deployable, its own connection, defined in `hub.settings.worker` and `hub.settings.api`) inside its own short `transaction.atomic(using="ai_audit")`, so a rollback of the caller's transaction cannot erase it. Approval endpoints are DRF actions on a `TicketMessageViewSet` (`/drafts/{id}/approve`, `/recall`, `/reject`); each action is wrapped by `@idempotent` (24-hour window).

### 6.5 Ask-the-data (FR-12)

Synchronous `POST /api/v1/analytics/ask`. Time budget (D-24): model 3 s, query 5 s (`statement_timeout`), outer 8 s (NFR-02); PgBouncer also enforces a query timeout.

1. `analytics.application.AskDataUseCase.ask(question, actor)`. The actor's role maps to `AskRole` (`support_agent` to `analytics_support`, `finance_officer` to `analytics_finance`, `operations_engineer` to `analytics_ops`); the **administrator has no mapping and is refused** with 403 `forbidden` (D-14). The role's catalog lists the allow-listed views in its schema with columns and descriptions.
2. Prompt variables include only that role's catalog (the model never sees objects the role cannot query). `AiService.run(feature="ask_data", deadline=3 s, output_schema=SqlProposal)`, where `SqlProposal` contains `sql`, `assumptions` and `explanation_of_sql`. The explanation describes the SQL and its assumptions only; **result rows are never sent to the model** and there is no second model call over results (D-31).
3. **Validate** with `SqlValidator` (pure domain code, `sqlglot`): single `SELECT` or `WITH ... SELECT`; function allow-list; relations only from the role's schema; `LIMIT` injected or capped at 1,000; no `pg_*`, comments or multiple statements. A failure allows one model retry with the validator message if time remains within the 3 s model budget; otherwise 422 `sql_rejected`.
4. **Execute** through `ReadOnlySqlExecutor` on the **read replica** with the matching login role. The role's defaults are fixed in the database (`ALTER ROLE ... SET`: read-only default, `statement_timeout`, idle timeout, temp-file limit), the transaction is `READ ONLY`, rows are capped. Invariants 1 and 5 hold even if the validator is bypassed because the database enforces them. Replica lag above 300 s disables the feature (system architecture).
5. Persist the audit (question, SQL, verdict, duration, row count, role) in its own short transaction on the primary **before** returning (also on failure), and emit `askdata.query_executed`. The response contains the SQL, the assumptions, columns, rows (max 1,000) and caveats; the explanation in the user's locale is the model's `explanation_of_sql`.
6. **Retries:** none for execution; the model call is retried once only for a transient LLM error and only within the 3 s budget.
7. **Rate limit:** per user from the tier table in `05-api-design.md`; spend counts against the separate ask-the-data budget pool (D-34).

**Django implementation notes (6.5).** The view is a DRF `GenericAPIView` on `hub-api`. `ReadOnlySqlExecutor` is implemented with a `psycopg_pool.ConnectionPool` per login role (DSNs `HUB_ANALYTICS_SUPPORT_DSN`, `_FINANCE_`, `_OPS_`), not a Django `DATABASES` entry, so the ORM can never run generated SQL; each query uses a `READ ONLY` transaction, `row_factory=dict_row` and `fetchmany(max_rows + 1)` to detect truncation.

---

## 7. Cross-cutting design

### 7.1 Error taxonomy

All errors derive from `shared.errors.DomainError(code: str, message_key: str, details: dict)`. Modules define subclasses in their `domain/errors.py` (re-exported in `contracts.py` if callers need them). One DRF exception handler, `hub.shared.api.problem.exception_handler` (set as `REST_FRAMEWORK["EXCEPTION_HANDLER"]`; plain Django views call the same converter), converts to RFC 9457 problem JSON with content type `application/problem+json`: `{type, title, status, code, detail, instance, request_id, errors[]}`; `detail` is localised via `Accept-Language` or the principal's locale (NFR-11).

| Class | Example codes | HTTP | Retry advice |
|---|---|---|---|
| `ValidationFailed` | `validation_error`, `sql_rejected`, `currency_mismatch` | 422 | no |
| `Unauthenticated` | `token_expired`, `invalid_signature`, `mfa_required`, `step_up_required` | 401 | re-auth or step-up |
| `PermissionDenied` | `forbidden`, `insufficient_scope`, `self_approval_forbidden`, `conflict_of_interest`, `role_catalog_denied` | 403 | no |
| `NotFound` | `customer_not_found`, `top_up_not_found` | 404 (also used instead of 403 where existence would leak) | no |
| `Conflict` | `idempotency_key_reuse`, `invalid_state_transition`, `four_eyes_required`, `period_locked`, `posting_unbalanced`, `version_conflict` | 409 | no |
| `Duplicate` (success-like) | `duplicate_event` | 200 with `status: duplicate` on webhooks, 409 elsewhere | n/a |
| `RateLimited` | `rate_limited`, `ai_budget_exceeded` | 429 with `Retry-After` | yes after delay |
| `ExternalUnavailable` | `provider_unavailable`, `meter_gateway_timeout`, `llm_unavailable` | 502 or 503 | yes |
| `Unexpected` | `internal_error` | 500, no details, request id returned | maybe |

Invariant breaches (`LedgerImbalance`, `PostingKeyConflictWithDifferentBody`) are `InvariantViolation(DomainError)`: HTTP 500, page on-call, never swallowed. Background handlers classify exceptions as `Retryable` (`ExternalUnavailable`, DB transient) or `Permanent` (everything else) with the job framework acting on that class.

### 7.2 Configuration

12-factor: configuration only from environment variables (and mounted secret files in containers), no config in code branches. Settings are Django settings modules (`hub.settings.base` plus one per deployable, section 1) reading the environment through `django-environ` wrapped in `hub.shared.config.env` (typed getters `env.int`, `env.bool`, `env.secret`; a missing required value raises `ImproperlyConfigured` at import, so the process fails fast at start). Secrets are read with `env.secret`, which returns a `SecretStr`-like wrapper whose `repr` is masked; they come from the cloud secrets manager injected as environment variables or files, never from the repository or images. `DATABASES` is built per module: `default` from `HUB_DB_URL` (the deployable's own role), `replica` from `HUB_DB_REPLICA_URL` (api and stream only), `ai_audit` from the same role. `search_path` is a role setting in the database, not an application setting (D-43). `.env.example` is the documented list; `docs/runbooks/config.md` mirrors it. AI settings: `ANTHROPIC_MODEL` (default `claude-opus-5-5`), per-feature `effort` map, dollar budgets (per ticket, per feature per day, per customer, per API key per day) with token equivalents, the pinned approved fallback list, `HUB_AI_FALLBACKS_ENABLED=true` (synchronous calls only). Other secrets: the API-key HMAC pepper, the JWT EdDSA private key, provider webhook secrets, the token-payload encryption key, the cursor-signing key, and one database URL per role. `DEBUG` is false everywhere except local development; `ALLOWED_HOSTS`, `SECURE_*` and cookie flags are set in `base`.

### 7.3 Logging, tracing, metrics

- `structlog` JSON logs to stdout configured through Django `LOGGING` (a `ProcessorFormatter` for stdlib loggers, so Django and gunicorn logs share the format); OpenTelemetry (`opentelemetry-instrumentation-django`, `-psycopg`, `-redis`) for traces and metrics (OTLP to collector). `hub.shared.middleware.RequestContextMiddleware` (the first middleware) binds `request_id`, `correlation_id` and the entrypoint; the authentication class adds `principal_id`.
- Context via `contextvars`: `request_id` (accepted from `X-Request-Id` only from the LB, else generated), `correlation_id` (born at the first request or event, carried in event envelopes and job messages), `causation_id`, `trace_id`/`span_id`, `principal_id` (never name), `entrypoint`, `module`, `use_case`.
- Each use case is a span named `<module>.<UseCase>`; adapters create child spans (`provider.initiate`, `provider.fetch_status`, `gateway.push_token`, `llm.complete`, `sql.execute`); outbox events propagate the trace context in the envelope.
- Metrics per NFR-09: request histograms, `webhook_ack_seconds`, `ledger_post_seconds`, outbox lag, stream lag per group, `tokens_failed_total`, `ai_tokens_total{feature,direction}`, `ai_cost_usd{feature}`, `ai_latency_seconds{feature}`, cache hit ratio.
- **Never log:** passwords, OTPs, refresh or access tokens, API keys, `Authorization` and signature headers, webhook secrets, full MSISDNs (log a hash prefix), emails, ticket message bodies, prompts or model outputs (these go to object storage by prompt hash with retention, referenced by `ai_runs.payload_ref`, not to logs), token payloads and counters, stream tickets (the balancer log format strips the query string), SQL result rows, raw webhook bodies (stored in `provider_events` only). A `redact_processor` in structlog masks keys matching a deny-list and a CI test asserts logs produced by the e2e suite contain no phone-number or token regexes.

### 7.4 Database access

- **Django ORM on psycopg 3**, PgBouncer in transaction mode: `CONN_MAX_AGE = 0` in worker and ingest, `DISABLE_SERVER_SIDE_CURSORS = True`, session-level state forbidden (use `SET LOCAL` only).
- **Unmanaged models (ADR-0020):** `managed = False`, one model per table, unqualified `db_table`, primary keys `UUIDField(primary_key=True, default=None)` with ids supplied by the `IdGenerator` (never `uuid4`). Partitioned (`meter_readings`) and write-protected (`ledger_*`, `wallet_balances`, audit) tables have **read-only models**: the repository exposes no write method and the application role has no grant to write them.
- **Unit of work:** `hub.shared.uow.DjangoUnitOfWork` wraps `transaction.atomic(using="default")`; it stages outbox rows in memory and inserts them in the same transaction on a clean exit (raw `INSERT` into `hub.outbox`), so the business change and the event commit together. Use cases never call `transaction.atomic`, `commit()` or `connection` directly.
- **Repositories** are plain classes (no base class beyond a small mixin offering `uow.require_active()`), constructed with no arguments, using models internally and returning domain entities via `infrastructure/mappers.py`. Read-heavy lists use query objects (`infrastructure/queries.py`) returning frozen DTOs through `.values()` or raw SQL, never model instances. A repository method that locks says so in its name (`lock_by_provider_ref`, `lock_for_issue`).
- **Locking:** `select_for_update()` (with `of=("self",)`, `skip_locked=True` for pollers) inside a unit of work; lock order is documented per repository (top-up, then meter, then wallet inside the definer function).
- **Definer-function calls:** `hub.shared.db.call_function(name, *args)` executes `SELECT hub.<name>(%s, ...)` on the active transaction's connection and maps SQLSTATE errors to domain errors (`period_locked`, `check_violation` to `InvariantViolation`, unique violations on `posting_key` to an idempotent success). It is used only by `ledger.infrastructure.writer` (`post_ledger_transaction`), `identity.infrastructure.audit` (`audit_append`) and `admin.infrastructure.erasure` (`erase_customer`). Arguments that are structured (ledger lines) are passed as `jsonb` parameters; the function signatures are those of `db/schema.sql`.
- **Bulk and partitioned writes:** raw SQL through `connection.cursor()` (psycopg 3), `unnest` arrays for batches below 1,000 rows and `COPY` into a temporary staging table for larger chunks (section 6.3).
- **Database roles per deployable (D-22, D-43):** each settings module connects as its own role (`hub_api`, `hub_ingest`, `hub_worker`; the stream pool uses `hub_api` on the replica alias). `audit_writer` owns `audit_append`; `ledger_owner` (NOLOGIN) owns `post_ledger_transaction`; `migrator` runs `manage.py migrate` through `hub.settings.migrate`; `analytics_support`, `analytics_finance`, `analytics_ops` are used only by the ask-the-data executor through psycopg pools on the replica. Row-level security is `FORCE`d and the unit of work sets the principal context with `SET LOCAL hub.principal_id = ...` at entry. A test checks the per-role settings.
- **Read-replica routing:** no Django database router (it hides which database a query uses). Query objects take an explicit alias from `shared.db.read_alias()`, which returns `replica` unless the request is pinned: the `read_your_writes` pin is set for 5 seconds after a write by the same principal (Redis key) or when replica lag exceeds 5 seconds. Payment, ledger and token flows use `default` only. Ask-the-data and exports use the replica only.
- **Ledger locking:** the Python adapter never writes ledger tables; `hub.post_ledger_transaction(...)` locks wallet balance rows `FOR UPDATE` in ascending account id, inserts the transaction and entries, updates `wallet_balances`, enforces no-overdraft where applicable and the deferred zero-sum trigger. System accounts (`provider_clearing`, `revenue_energy`, `fees`, `suspense`, `chargeback_receivable`) are not row-locked: balances are derived from entries and materialised in `ledger_balance_snapshots`.
- **Isolation:** READ COMMITTED with explicit locks; no `SERIALIZABLE` on hot paths.
- **Migrations (ADR-0020, D-43):** a change is a numbered SQL file in `db/migrations` (expand, then contract) plus a Django migration in `hub/shared/migrations/` whose single `RunSQL` operation applies that file (helper `apply_sql_file("00NN_name.sql")`, reverse operation `noop` because production is forward only); `db/schema.sql` is regenerated from a fresh database and diffed in CI; `manage.py makemigrations` is never run (a CI step fails if it would produce a file); `check_db_alignment` compares the models with the introspected database.

### 7.5 Background jobs and scheduling

**Decision: an in-house worker framework in `shared/jobs` over Redis Streams** (about 400 lines; no third-party task library). D-27 fixes the outbox as the source of truth with a relay publishing to Redis Streams and consumers deduplicating in `consumer_inbox`; that model (at-least-once, per-module consumer groups, replay window, `dlq.<stream>`) is exactly what the framework implements, whereas a general task library would add a second broker abstraction, a second retry and visibility model, and would not know about `consumer_inbox` or the outbox. Revisit if calendars or workflow fan-out appear. It runs as three Django management commands from the same image:

| Command | Role | Process model |
|---|---|---|
| `manage.py run_relay` | Outbox relay: claims `hub.outbox` rows with `SKIP LOCKED`, `XADD` to the stream family, marks published; holds a Postgres advisory lock so one relay is active with hot standbys | one process, sync loop, woken by `LISTEN/NOTIFY` with a 500 ms poll backstop |
| `manage.py run_worker --group <module> [--concurrency N]` | Stream consumers of one module's group plus that module's pollers (`register_poller`, for example `meters.send_due_tokens` with `skip_locked`) | N threads, each a `XREADGROUP` loop; `close_old_connections()` after each message; one Django connection per thread |
| `manage.py run_scheduler` | Cron jobs (singleton by `pg_try_advisory_lock`) and delayed jobs from the Redis sorted set | one process per replica, only the lock holder runs a slot |

```python
class JobHandler(Protocol):
    name: str
    def __call__(self, payload: Mapping[str, Any], ctx: JobContext) -> None: ...

class JobRegistry(Protocol):
    def register_consumer(self, *, stream: str, group: str, handler: EventHandler, concurrency: int) -> None: ...
    def register_cron(self, *, name: str, cron: str, handler: JobHandler, singleton: bool = True) -> None: ...
    def register_poller(self, *, name: str, interval_s: float, handler: JobHandler) -> None: ...   # not singleton; handlers claim work with SKIP LOCKED

class JobQueue(Protocol):                     # delayed and ad hoc jobs
    def enqueue(self, name: str, payload: Mapping[str, Any], *, delay: timedelta = timedelta(0), dedupe_key: str | None = None) -> None: ...
```

- **Outbox relay:** worker loop `SELECT ... FROM outbox WHERE published_at IS NULL ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 200`, `XADD` to the stream, mark published; multiple relays are safe. Lag alert at 30 s.
- **Consumers:** Redis Streams consumer groups, `XREADGROUP`, `XACK` after the handler's transaction commits, `XAUTOCLAIM` for stuck messages (idle 60 s), retries per section 4, poison messages after 5 deliveries to `dlq.<stream>`.
- **Cron:** `run_scheduler` in every worker pod, executing only if it wins `pg_try_advisory_lock(hash(job_name))` for the slot (singleton jobs). Jobs: outbox purge, `meters.send_due_tokens` (due-scan over `meter_tokens`), `meters.expire_unknown_tokens`, `payments.sweep_pending_unknown`, `payments.expire_top_ups`, `ledger.snapshot_balances`, `identity.verify_audit_chain` and daily audit anchor, `telemetry.*`, partition management (create next month, drop expired), `reconciliation.import_daily`, `identity.purge_expired_sessions`, `admin.retention_sweep`, `analytics.refresh_snapshots`, `ai_gateway.rollup_costs`.
- Every job is idempotent; each module registers its handlers in `<module>/application/wiring.py` called by `bootstrap`, so the worker discovers them without importing modules from each other.

### 7.6 Feature flags

`FeatureFlags` Protocol (section 5). Defaults are declared in code (`flags.py` per module; a flag without a default fails the architecture test) and overridden by typed settings; an administrator may toggle an override through an audited endpoint that writes a Redis hash read with a 30-second refresh. There is no `feature_flags` table in v1 (D-23 lists none). Initial flags: `ai.triage`, `ai.draft_reply`, `ai.summary`, `ask_data.enabled`, `ingest.shed_load`, `notifications.sms_real`, `payments.provider_<name>`. Flags are for rollout and kill switches only and are removed within two releases of full rollout. **Invariant-affecting behaviour is never flag-controlled**: approval requirements, redaction, the 30-second recall window, maker-checker, role scoping in ask-the-data.

### 7.7 API layer conventions (Django REST framework)

Everything here lives in `hub.shared.api` unless it is module specific. `api/openapi.yaml` is the contract and is **not generated** from the code (D-42); the code is proven against it (item 10).

1. **Serializers per resource.** Each resource has an input serializer and an output serializer in the app's `api/serializers.py` (`TopUpCreateSerializer`, `TopUpSerializer`); serializers are plain `Serializer` subclasses, not `ModelSerializer` (models are private, rule 2 of 1.2). They validate and shape; they call no repository. Money is a `MoneyField` (`{"amount_minor": int, "currency": "USD"|"CDF"}`), ids are `UUIDField`, timestamps RFC 3339 UTC. Enumerations are `ChoiceField`s built from constants that a test compares with `db/schema.sql` (D-01).
2. **Views** are `GenericViewSet`s or `GenericAPIView`s with explicit methods; every handler is decorated `@operation("<operationId>")` using the id from `api/openapi.yaml`. A handler body is: parse input, `use_case(...)`, serialize the DTO. No business logic, no ORM.
3. **Envelope.** `EnvelopeRenderer` wraps single resources as `{"data": ...}`; lists as `{"data": [...], "meta": {"has_next", "next_cursor", "limit"}}`.
4. **One permission class**, `PermissionsYamlPermission`, set as the only `DEFAULT_PERMISSION_CLASSES` entry. At start it loads `api/permissions.yaml` (path from settings, hash checked against the copy baked in the image) into an `operationId` to rule map: allowed principal kinds, roles, permissions, scopes, `step_up`, `site_scoped`. `has_permission` reads `view.operation_id` (set by `@operation`), refuses with 401 or 403 and the problem codes of 05, and `require_site` is applied by the use case. A view without `@operation` or an operationId missing from the file fails the architecture test and refuses every request at runtime (deny by default).
5. **Pagination.** `CursorEnvelopePagination` implements keyset pagination on `(sort_key, id)`; the default order is `id` (UUIDv7, time ordered); `limit` default 50, maximum 200; the cursor is the base64url of `{sort_value, id, order, exp}` signed with HMAC-SHA-256 using the cursor key and valid for 24 hours; a tampered or expired cursor returns 400 `invalid_cursor`. It queries `limit + 1` rows to set `has_next`. `LimitOffsetPagination` and `PageNumberPagination` are not installed.
6. **Problem handler.** `exception_handler` maps: `DomainError` subclasses by the table in 7.1 (code, status), DRF `ValidationError` to 422 `validation_error` with `errors[]` (field, code, message), `NotAuthenticated` and `AuthenticationFailed` to 401, `PermissionDenied` to 403, `Throttled` to 429 `rate_limited` with `Retry-After`, `Http404` and `NotFound` to 404, `ParseError` to 400, database errors through `shared.db.map_error`, anything else to 500 `internal_error` (logged with the request id, no detail returned). Every response carries `request_id`.
7. **Idempotency.** `@idempotent(scope="<METHOD path template>", window=timedelta(...))` on a handler: requires the `Idempotency-Key` header (400 `idempotency_key_required` where the scope demands it); computes `sha256(method, path, canonical body)`; inside its own short `transaction.atomic` inserts into `idempotency_keys` (`ON CONFLICT DO NOTHING`); a stored completed row with the same hash replays the stored status and body; the same key with a different hash returns 422 `idempotency_key_reuse`; a row still running returns 409 `idempotency_in_progress`; a 5xx response stores nothing and releases the row. Windows: 7 days for `POST /top-ups`, 24 hours elsewhere (D-12). The key is scoped by principal.
8. **Rate limits.** `TierThrottle` (a DRF `BaseThrottle`) reads the operation's tier from the `x-rate-limit-tier` extension of `api/openapi.yaml` (loaded at start) and the numbers from `hub.shared.api.tiers.TIERS`, a typed table that a test parses from the tier table of `05-api-design.md` and compares (the table in 05 is authoritative). Counters are Redis sliding windows keyed by the tier's key rule (IP, identity, principal, key, provider); it sets `RateLimit-*` headers and raises `Throttled`. `probe` is exempt.
9. **Plain Django views outside DRF**: the provider webhook (raw body), the cookie endpoints, health, and the stream. They are the only views not using DRF.
   - **Cookie endpoints (D-46):** `POST /api/v1/auth/refresh` and `POST /api/v1/auth/logout` use the decorator `@cookie_csrf_guard`, which requires `Origin` equal to `settings.SITE_ORIGIN` and the header `X-Hub-CSRF: 1`, otherwise 403 `csrf_check_failed`. They read the refresh token only from the `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` cookie and set the rotated cookie in the response; the new access token is in the body. No other endpoint accepts a cookie; `CsrfViewMiddleware` and `SessionMiddleware` are not installed.
   - **SSE stream (async, ASGI):** `hub.analytics.api.stream.dashboard_stream` is an `async def` Django view returning `StreamingHttpResponse(async_generator, content_type="text/event-stream")` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`. It consumes the single-use stream ticket from Redis (`redis.asyncio`, `GETDEL`), subscribes to the dashboard pub/sub channels, sends a heartbeat comment every 15 seconds, re-checks role and `tv` on every heartbeat through `sync_to_async(thread_sensitive=False)` on a read-only function, ends after 10 minutes, and enforces the `stream` tier (3 concurrent per user) with a Redis counter. On Redis loss it falls back to polling the summary every 10 seconds. It is outside DRF because DRF is synchronous and wraps responses in renderers.
10. **Conformance test.** `tests/contract/test_openapi_conformance.py` starts the api application with `hub.settings.test` against a database loaded from `db/schema.sql` and `db/seed`, creates fixtures with `manage.py load_fixtures` (D-38; no test-only HTTP endpoint), then runs **schemathesis** (`schemathesis.from_path("api/openapi.yaml", base_url=...)`) with the checks `not_a_server_error`, `status_code_conformance`, `content_type_conformance`, `response_schema_conformance`, authenticated through a hook that mints tokens for each role in `x-roles`; stateful links are off; the examples in the file are used first. Failures block merge. A second test checks the enum equivalence of D-01 and the permissions equivalence of D-14.

---

## 8. Frontend structure (Next.js, `hub-web`)

Next.js (App Router) with TypeScript strict, Node standalone output, behind the same balancer and site as the API (pool `be_web`, D-44, ADR-0021). TanStack Query, next-intl, openapi-typescript and openapi-fetch. It is a deployable of its own; it holds no secrets and never reads the database.

```
frontend/src/
├── app/
│   ├── [locale]/                         # next-intl locale routing: /fr and /en (default fr)
│   │   ├── layout.tsx                    # server component: <html lang>, NextIntlClientProvider, CSP nonce passed to scripts
│   │   ├── (public)/                     # route group: public site, SERVER components
│   │   │   ├── page.tsx                  # landing and login shell
│   │   │   ├── help/[slug]/page.tsx      # public knowledge-base articles (revalidate 300 s)
│   │   │   ├── tariffs/page.tsx          # public tariffs
│   │   │   └── status/page.tsx           # public status
│   │   ├── (portal)/portal/              # route group: customer portal, authenticated, CLIENT components under a server shell
│   │   │   ├── layout.tsx                # server shell + <ClientAuthGate/> (client)
│   │   │   ├── topup/  tokens/  history/  support/  profile/
│   │   └── (staff)/staff/                # route group: staff app, authenticated, CLIENT components under a server shell
│   │       ├── layout.tsx                # server shell + <ClientAuthGate role-aware/> (client)
│   │       ├── dashboard/  customers/  meters/  ledger/  tickets/  approvals/
│   │       ├── reconciliation/  analytics/  kb/  admin/
│   ├── error.tsx, global-error.tsx, not-found.tsx   # error boundaries (client components)
│   └── api/                              # intentionally empty: no Next.js route handlers (the API is Django)
├── api/
│   ├── generated/                        # openapi-typescript output; generated in CI and on `make gen`, never edited
│   ├── client.ts                         # openapi-fetch client: base "/api/v1" (same origin), access token from memory, locale header, request id, refresh-once-on-401
│   ├── auth-store.ts                     # in-memory access token and principal (module-level, never localStorage, never cookies)
│   ├── problem.ts                        # problem+json parser to AppError {code, status, requestId, fieldErrors}
│   └── server.ts                         # server-only fetch helper for PUBLIC endpoints (no token), used by server components
├── features/                             # one folder per business capability (client components, hooks, schemas); mirrors backend modules
│   ├── auth/  customers/  payments/  ledger/  meters/  telemetry/  support/  analytics/  reconciliation/  approvals/  admin/
│   │   each: components/, hooks/ (TanStack Query hooks), schemas/ (zod form schemas), keys.ts (query-key factory), messages/ (namespace JSON per locale)
├── shared/
│   ├── ui/                               # design-system components, WCAG 2.2 AA tested
│   ├── hooks/                            # useSse, useDebounce, usePermission, useStepUp
│   ├── format/                           # Intl-based money and date formatting (minor units plus currency)
│   └── lib/
├── i18n/                                 # routing.ts, request.ts (next-intl), messages/en and messages/fr (common, errors, per feature)
└── tests/                                # Vitest utilities, MSW handlers from OpenAPI examples
```

**Server versus client components (D-44).** Rules, enforced by lint (`eslint-plugin-boundaries` plus a custom rule on `"use client"`):
1. Anything under `(public)` is a **server component**: it fetches only public endpoints through `api/server.ts` with `fetch(url, { next: { revalidate: 300 } })`, sends no credentials, and ships no TanStack Query.
2. Anything under `(portal)` and `(staff)` that reads or changes authenticated data is a **client component** (`"use client"`): the access token lives in memory only (D-17), so the server cannot make authenticated calls and must not try. The route `layout.tsx` and `loading.tsx` files stay server components and render static chrome; the page body is one client component per page that uses TanStack Query hooks from `features/*/hooks`.
3. Server components never import `api/client.ts`, `auth-store.ts` or anything from `features/*/hooks`; client components never import `api/server.ts`.
4. No secrets or database access in any component; no Next.js route handlers (`app/api` stays empty); **Server Actions are disabled**: `"use server"` is forbidden by ESLint and a CI grep, and `middleware.ts` returns 404 for any request carrying a `Next-Action` header.
5. Bundle budgets (D-44, to be confirmed by the early spike **[open]**): customer portal first load at most 130 KB gzipped, staff app at most 250 KB; CI fails above budget with `@next/bundle-analyzer` output; staff-only libraries (charts, SQL viewer) are dynamically imported.

**Locale routing.** `next-intl` with `locales = ["fr", "en"]`, `defaultLocale = "fr"`, `localePrefix = "always"`; `middleware.ts` composes the next-intl middleware, then sets the CSP header. Server components use `getTranslations`; client components use `useTranslations` with the namespaces they need. Message files are per locale and namespace; a script fails CI on missing keys (EN and FR parity, NFR-11). Error messages are rendered from `errors:<problem.code>`. `Intl.NumberFormat` and `DateTimeFormat` format money and dates with the active locale. The `Accept-Language` header sent to the API follows the active locale.

**Generated client.** `make gen` runs `openapi-typescript api/openapi.yaml -o src/api/generated/schema.d.ts`; CI regenerates and fails on any diff (`check-openapi-drift`). Feature hooks wrap `openapi-fetch` calls (`useTickets()`, `useApproveDraft()`); components never call `fetch` directly (ESLint `no-restricted-globals`), except `api/server.ts`.

**Client state and caching.** TanStack Query lives inside client components only, provided by `<QueryProvider>` in the portal and staff client shells (never in the root layout, so public pages ship no Query code). Key factories per feature (`ticketKeys.list(filters)`); stale times by data type (registry 60 s, dashboard 10 s with stream invalidation, ledger statements 0 s after a mutation); optimistic updates only for non-monetary actions (draft edit); money-affecting mutations (initiate top-up, adjustments, approvals) wait for the server. Local UI state is React state and URL search params (filters live in the URL); no global store library. Mutations that need an `Idempotency-Key` generate it per user intent and reuse it across retries.

**Stream hook.** `useSse` calls `POST /api/v1/dashboard/stream-tickets` for a single-use ticket (30 s, bound to the caller's IP), opens `GET /api/v1/dashboard/stream?ticket=...` with `EventSource`, renews the ticket before the 10-minute limit, writes into the query cache (`setQueryData` or invalidation), refetches on reconnect (the stream is lossy) and falls back to polling every 10 seconds when `EventSource` errors repeatedly. It is used only by the staff dashboard.

**Middleware (`middleware.ts`).** On every non-static request: (1) reject requests carrying the `Next-Action` header with 404; (2) run the next-intl locale routing; (3) generate a per-request nonce, set `Content-Security-Policy` (`default-src 'self'`; `script-src 'self' 'nonce-<n>' 'strict-dynamic'`; `style-src 'self' 'nonce-<n>'`; `img-src 'self' data:`; `connect-src 'self'`; `frame-ancestors 'none'`; `base-uri 'none'`; `form-action 'self'`; report URI from `HUB_CSP_REPORT_URI`) and pass the nonce to the layout through the `x-nonce` request header. The same-origin deployment means `connect-src 'self'` covers the API and the stream.

**Error boundaries.** `app/error.tsx` (route-level, offers retry and shows the request id), `app/global-error.tsx` (root crash page), `not-found.tsx`; one error boundary per dashboard widget so a failing panel does not blank the page; asynchronous errors show a toast with the problem `code` and `requestId`. 401 with `step_up_required` opens the step-up dialog and replays the mutation once; any other 401 attempts one refresh through `POST /api/v1/auth/refresh` (sending `X-Hub-CSRF: 1`; the browser sends the cookie) and otherwise redirects to login; 403 shows a not-authorised page (`self_approval_forbidden` and `conflict_of_interest` get their own messages).

**Next.js configuration (`next.config.ts`).** `output: "standalone"`; `images: { unoptimized: true }` (the image optimiser is disabled: no remote images, no server-side request forgery surface; static images are pre-sized assets); `poweredByHeader: false`; `reactStrictMode: true`; `typedRoutes: true`; no `rewrites` to the API (the balancer routes `/api/` to Django); security headers other than CSP (`X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`) set in `headers()`.

**Environment variables.** `NEXT_PUBLIC_*` values are public by definition and contain **no secrets**: `NEXT_PUBLIC_SITE_ORIGIN`, `NEXT_PUBLIC_API_BASE` (`/api/v1`), `NEXT_PUBLIC_DEFAULT_LOCALE`. Server-only values (no prefix): `HUB_API_INTERNAL_URL` (the in-cluster URL of the Django pool that `api/server.ts` calls for public pages), `HUB_CSP_REPORT_URI`. A CI check fails when a `NEXT_PUBLIC_` variable name contains `SECRET`, `KEY`, `TOKEN` or `PASSWORD`, and `.env.example` lists every variable.

**Accessibility and quality.** Components from `shared/ui` have axe checks in Vitest; Playwright with `@axe-core/playwright` runs on the portal and staff critical paths (WCAG 2.2 AA, keyboard-only paths for draft review and exceptions, NFR-10) in both locales and on a throttled low-end-phone profile for the portal; Playwright e2e for top-up, ticket approval, approvals and ask-the-data.

---

## 9. Coding standards and definition of done

**Python:** ruff (lint and format; rule sets E, F, I, B, UP, S, DJ, PL subset; custom bans: `datetime.now`, `uuid.uuid4`, `random` in `domain` and `application`), mypy `--strict` for `hub.*` with the `django-stubs` plugin (`mypy_django_plugin`, `DJANGO_SETTINGS_MODULE=hub.settings.test`; domain and application with no `Any` leakage), Python 3.12 typing syntax. Frozen dataclasses for value objects, DTOs and events; Pydantic only inside `ai_gateway`. Synchronous code except the stream pool. Naming: use cases are verbs in PascalCase (`ConfirmPayment`), repositories `<Aggregate>Repository`, ports named by capability, events `PaymentConfirmed` mapped to `payment.confirmed`, DRF views `<Resource>ViewSet`, serializers `<Resource>Serializer` and `<Resource>CreateSerializer`.

**TypeScript:** `strict`, ESLint (`next/core-web-vitals`, typescript-eslint, `jsx-a11y`, `eslint-plugin-boundaries`, custom `no-use-server` and `server-client-boundary` rules), Prettier, no `any` without a justified comment; feature folders do not import another feature's internals.

**Tooling summary.**

| Concern | Tool | Notes |
|---|---|---|
| Backend tests | pytest with pytest-django | `DJANGO_SETTINGS_MODULE=hub.settings.test`; the test database is created from `db/schema.sql` by a session fixture (no Django migrations at test time); `django_db(transaction=True)` for flows that assert commit behaviour; `--reuse-db` off |
| Property tests | hypothesis | money arithmetic and rounding, ledger posting balance, SQL validator, cursor round trip, idempotent replay of events |
| Architecture | import-linter plus `tests/architecture` | section 3.3 |
| Lint and types | ruff, mypy with django-stubs | section above |
| API conformance | schemathesis against `api/openapi.yaml` | section 7.7 item 10 |
| Frontend unit | Vitest, Testing Library, MSW | |
| Frontend e2e and accessibility | Playwright, `@axe-core/playwright` | both locales, portal on a low-end profile |
| Frontend lint | ESLint, tsc, bundle budget check | |
| Contract artefacts | `check-openapi-drift`, enum and permissions equivalence tests, model-versus-schema test | D-01, D-14, ADR-0020 |

**CI configuration checks (`tools/check_django_config.py`, run for every settings module).** Fails the build when any of these is true: `django.contrib.admin`, `auth`, `sessions`, `messages` or `contenttypes` appears in `INSTALLED_APPS`; `ATOMIC_REQUESTS` is true for any database alias; `CsrfViewMiddleware` or `SessionMiddleware` is configured; a model (found by AST scan of `infrastructure/models.py`) lacks `managed = False`; a model class is declared outside `infrastructure/models.py`; `makemigrations --check` would create a file; `celery` or `sqlalchemy` or `fastapi` appears in the lock file; the frontend contains `"use server"` or a `NEXT_PUBLIC_` secret-like name.

**Definition of done (every PR):**

1. Linters, formatters, mypy (django-stubs) and tsc pass; import-linter, the Django-configuration checks and the OpenAPI drift check pass; schemathesis conformance passes.
2. Tests: domain unit tests for new rules; use-case tests with fakes; integration tests for any SQL, migration or adapter; for money or idempotency paths a property or duplicate-delivery test (the same event twice yields one effect). Coverage floor 85% on `domain` and `application`, no floor drop overall.
3. Migration: a numbered SQL file in `db/migrations` and its `RunSQL` Django migration, tested on a dump of the previous schema, `db/schema.sql` regenerated, models updated and the alignment test green, backwards-compatible with the previous app version (expand, then contract).
4. API change: `api/openapi.yaml` updated first, examples included, generated client regenerated, problem codes listed.
5. Observability: spans and metrics for new use cases or adapters, no sensitive data in logs (checked by the log test).
6. Security: input validated, permissions declared and tested (a negative test per permission), secrets via settings.
7. Docs: ADR for any decision against this document or the shared context; runbook entry for any new alert; event catalogue updated; i18n keys in EN and FR.
8. Feature flag and rollback plan if user-visible or AI-related.
9. Review checklist for the reviewer: right package and layer? only `contracts`/`events` imported across modules? external effect behind a port? idempotent under redelivery? transaction boundary stated? any money arithmetic in integers? AI output isolated from the customer path? error mapped to a problem code? Does the log line leak data?

CI stages: lint/type and Django-configuration checks (2 min) -> unit -> integration (pytest-django, testcontainers) -> architecture -> schemathesis conformance -> frontend (lint, tsc, Vitest, bundle budget) -> build images (backend and hub-web) -> e2e on compose (Playwright and axe) -> security scan (dependency audit, container scan, secret scan). Dependabot/Renovate weekly (NFR-14).

---

## 10. Build order (vertical slices)

Each slice is releasable to a staging environment and ends with a demo.

| # | Slice | Modules touched | Demo-able outcome | FR delivered |
|---|---|---|---|---|
| 0 | **Walking skeleton**: repo, CI, compose (Postgres, Redis, HAProxy with the five pools, stub services); the Django project with the five settings modules, `wsgi_api`, `wsgi_ingest`, `asgi_stream`, health endpoints `/api/v1/health/live` and `/ready`, the `shared` app (env helpers, logging, `DjangoUnitOfWork`, outbox, clock, ids, errors, DRF helpers) and the `run_worker`, `run_relay`, `run_scheduler` commands as no-op loops; the `RunSQL` migration applying `db/schema.sql` with roles and `invariant_tests.sql` in CI; import-linter and `check_django_config.py` in CI; generated client pipeline; **the Next.js shell `hub-web`**: App Router with `[locale]` routing (fr, en), the `(public)` landing page as a server component, empty `(portal)` and `(staff)` groups with the client auth gate, CSP nonce middleware, disabled image optimiser and Server Actions, `be_web` in the balancer; one trivial vertical (`GET /me`) | shared, identity (skeleton), frontend shell | `make up` runs everything; an architecture or Django-config violation fails CI; the landing page renders in French and English with a nonce CSP; a trace spans balancer, api and database | NFR-14 groundwork, FR-15 (partial), FR-16 (shell) |
| 1 | **Identity, RBAC and approvals**: staff login (OIDC or local TOTP, WebAuthn path), EdDSA access and rotating refresh cookie, roles from `api/permissions.yaml`, `audit_append` with hash chain, step-up, `ApprovalService` with the executor registry and conflict check, locale in principal | identity, admin (audit view), frontend auth and approvals inbox | Staff log in with MFA, role-gated pages, a user-creation approval requires a second person, audit entries visible and chain verified | FR-15, FR-16 (shell) |
| 2 | **Registry**: customers, sites, plans, meters registry, assignments, CSV import | customers, meters (registry only), frontend registry | Finance/ops create and search customers and meters in EN/FR | FR-01, FR-16 |
| 3 | **Ledger core**: accounts, `post_ledger_transaction` definer function and role grants, balances, wallet locks, system accounts with snapshots, accounting periods, statement views, `ledger.adjustment` approval executor | ledger, identity (approval), finance UI view | An approved manual adjustment posts balanced entries; unbalanced and unapproved are rejected; concurrent posting test passes; application roles cannot write ledger tables | FR-04, NFR-05 |
| 4 | **Top-up and webhook exactly-once**: initiation with quote and 7-day idempotency, provider stub, synchronous webhook (signature, `provider_events`, payer check, pull verification, late credit, reversals), pending-unknown sweeper | payments, ledger, ingest entrypoint, provider_stub | Customer top-up via portal and stub; duplicated and reordered webhooks credit once; a late webhook credits with `late`; a reversal posts to `chargeback_receivable`; ack p95 measured | FR-02, FR-03 |
| 5 | **Outbox, relay and worker framework**: relay, consumers, inbox, retry and dead-letter, cron with advisory lock | shared.jobs, worker entrypoint | Kill the worker mid-flight and events still process exactly-once in effect; lag metric visible | NFR-01 enabler |
| 6 | **Token issuance**: meters consume `payment.confirmed`, re-check meter and snapshot, counter and expiry, posting-bound token, outbound gateway adapter, acknowledgement and heartbeat routes, retry schedule to 72 hours, `unknown` state | meters, ledger, meter_simulator | Paid top-up becomes a token acknowledged by the simulated gateway; failure modes (timeout, reject, stale counter) behave per D-07; manual retry keeps the token id | FR-05 |
| 7 | **Notifications**: requests, templates EN/FR, stub SMS/email, event mapping (receipts, token failure) | notifications | Customer receives (stub) receipt and failure messages, deduped | FR-19 |
| 8 | **Telemetry and alerts**: ingest endpoint, partitions, aggregates, heartbeat and offline, low balance, anomalies | telemetry, meters, ledger (low balance), notifications | Simulator sends 1,700 readings/s with duplicates and late data; offline alert fires once | FR-06, FR-07 |
| 9 | **Ops dashboard**: read models, Redis cache, SSE, replica routing | analytics, shared cache, frontend dashboard | Live site status, revenue today, alerts update within seconds; cached p95 under 500 ms | FR-13 |
| 10 | **Support desk**: tickets from portal, API and email stub, messages, attachments, assignment, KB management with versions | support, customers, ObjectStore, frontend inbox, KB editor | Agent handles a ticket manually, KB articles versioned | FR-08, FR-17 |
| 11 | **AI gateway and triage**: gateway (redaction, budgets, audit, Claude client), triage consumer, summaries | ai_gateway, support | New ticket gets category, urgency, language, PII flags; cost per feature visible; refusal path handled | FR-09, FR-11, NFR-07, NFR-08, NFR-13 |
| 12 | **Reply drafting with approval**: KB retrieval, cited `ai_draft` messages, approve/edit/reject with `ticket.ai_approve`, 30 s recall window, finance co-approval for money-affecting drafts, KB publish approval, send via notifications | support, ai_gateway, notifications | Agent approves a French draft with citations; nothing sends without approval; audit trail | FR-10, FR-16 (AI) |
| 13 | **Ask the data**: catalog per role, validator, `analytics_*` login roles and view schemas, replica executor, 3 s / 5 s / 8 s budgets, audit; no explain call over rows | analytics, ai_gateway, db roles | Finance asks a question and sees SQL and rows; a support agent cannot reach finance views even with a crafted prompt; the administrator is refused | FR-12 |
| 14 | **Reconciliation**: statement import, matching, exception queue, propose-resolution with maker-checker, provider evidence for `match_to_top_up`, `confirm_non_delivery` | reconciliation, payments, meters, ledger, identity | Stub statement with missing and mismatched lines produces items; a finance resolution posts only after a second approver | FR-14 |
| 15 | **Integrator and gateway access**: API keys (`gateway` and `integrator` kinds, HMAC with pepper, 180-day life, site binding, source range) through an approval, per-key limits from the tier table | identity, admin, ingest | Integrator calls scoped endpoints; limits and revocation work; a gateway key cannot call integrator routes | FR-18 |
| 16 | **Privacy and lifecycle**: export, retention sweeps, erasure participants, partition drops | admin, all modules (participants), ledger anonymisation | Erasure request removes personal data but ledger totals unchanged; export bundle delivered | FR-20, NFR-07 |
| 17 | **Hardening**: load tests at 10x payday and 5x telemetry, failover drills, SLO alerts, runbooks, security review fixes, accessibility audit, dependency automation, DR restore test | all | Load test report meets NFR-02 and NFR-03 targets; restore within RTO; WCAG audit passed | NFR-01 to NFR-04, NFR-06, NFR-09, NFR-10, NFR-14 |

Slices 7 and 9 can run in parallel with 8; 10 can start after 2 and in parallel with 6 to 9 by a second pair. The critical path is 0 -> 3 -> 4 -> 5 -> 6. FR coverage check: FR-01 (2), FR-02 and FR-03 (4), FR-04 (3), FR-05 (6), FR-06 and FR-07 (8), FR-08 (10), FR-09 (11), FR-10 (12), FR-11 (11), FR-12 (13), FR-13 (9), FR-14 (14), FR-15 (1), FR-16 (1, 2, 12), FR-17 (10), FR-18 (15), FR-19 (7), FR-20 (16).

---

## 11. Resolutions in revision 1

Each challenge raised in the first draft, and how the tech lead's decisions (section 7 of the shared context) or this revision settle it.

| # | Earlier challenge | Resolution | Where reflected |
|---|---|---|---|
| 1 | UUIDv7 on Python 3.12 has no standard library support | **Accepted.** One pinned UUIDv7 library behind `IdGenerator` (version and hash locked); the database function `hub.uuid_v7()` covers SQL-side defaults. ADR recorded by the tech lead. | 2.1, section 5 |
| 2 | Synchronous ledger call inside the payment transaction contradicts "events between modules" | **Accepted, as a deliberate and named exception** (consistent with D-09, which makes webhook processing synchronous in one transaction). `payments` calls `LedgerService.post` in-process in the same transaction because invariant 3 and NFR-05 need atomicity. It holds only while both live in one database; extracting `ledger` would require a saga with a pending-posting state. | 2.4, 6.1 |
| 3 | Ask-the-data views read across modules, breaching table encapsulation | **Accepted, as a published read contract** (D-22, D-24): the `analytics_support`, `analytics_finance` and `analytics_ops` view schemas are owned by `analytics` and the database engineer, reviewed like an API, with a test that lists exposed columns. | 2.10 |
| 4 | `notifications` depends on `customers` for contacts | **Accepted.** Contacts are fetched at send time, never copied into events, so erasure and contact changes take effect (D-29). | 2.11 |
| 5 | In-house worker framework instead of a third-party task library | **Accepted** as an explicit decision, justified by D-27 (outbox, Redis Streams, `consumer_inbox`); revisit triggers stated. | 7.5 |
| 6 | System ledger accounts without row locks | **Accepted** (D-22, D-23): balances derived from entries, materialised in `ledger_balance_snapshots`; only wallets are locked, inside `post_ledger_transaction`. The throughput target of 10 per second on paydays, bursts of 30 with retries, remains reachable. | 2.5, 7.4 |
| 7 | Model fallbacks and cost audit | **Resolved by D-33, D-34, D-37:** fallbacks pinned to an approved list and only on synchronous calls; `served_model`, `fallback_ran`, `stop_category` and `payload_ref` recorded in `ai_runs`; dollar budgets with token equivalents. Cost per ticket has a floor because thinking cannot be disabled, so the budget pool for triage is reserved. | 2.9 |
| 8 | Webhook and telemetry paths must be routed by path to the ingest pool | **Resolved by D-04:** paths `/api/v1/webhooks/providers/*`, `/api/v1/telemetry/*`, `/api/v1/gateway/*`; pools `be_webhooks`, `be_telemetry`, `be_stream`, `be_api`; `hub-ingest` is one deployable run as two Deployments. | 1.1 |

Further changes in this revision and their decision ids: name mapping to the schema (D-01, D-02, section 1.3); late confirmations credited and flagged (D-05); provider reversals and `chargeback_receivable` (D-06); token delivery schedule, counter, expiry, posting binding and reversal rules (D-07); tariff and meter snapshots with re-check (D-08); synchronous webhook with payer check, pull verification and invalid-signature handling (D-09); idempotency windows (D-12); principals and the platform administrator (D-13, D-14); maker-checker through `approvals` in `identity` (D-15); staff authentication and sessions (D-16, D-17); stream tickets (D-18); API keys and gateways (D-19, D-20); customer one-time codes (D-21); database roles and definer functions (D-22); telemetry limits (D-25); queue semantics, `consumer_inbox`, `dlq.<stream>` (D-27); AI vocabulary, drafts as `ticket_messages`, `ticket.ai_approve` (C-21, D-31 to D-37). The optional `ask_data_explain` call over result rows is removed (D-31).

### Stack change (revision 2: D-42 to D-46, ADR-0019 to ADR-0021)

The owner stated Django and Next.js as the preferred stack after the first revision. What changed in this document, and why:

| Area | Before | After | Reason |
|---|---|---|---|
| Backend framework | FastAPI, Pydantic API models, async services | Django 5 with DRF; synchronous services behind gunicorn; async only in the stream pool (uvicorn, ASGI) | ADR-0019: the load (about 30 webhooks per second, about 417 telemetry batches per second at 5x peak, about 200 staff users) does not need async throughput; maturity, security defaults and the owner's preference |
| Persistence | SQLAlchemy 2 async, Alembic | Unmanaged Django models over the schema; numbered SQL files applied by `RunSQL` migrations; definer-function calls for ledger, wallet and audit; raw SQL and `COPY` for telemetry | ADR-0020: partitions, deferred triggers, definer functions, RLS and grants cannot be expressed by Django migrations; one executable schema file stays the authority |
| Transactions | `async with uow` over a session | `DjangoUnitOfWork` wrapping `transaction.atomic`, `ATOMIC_REQUESTS` off | The webhook needs one precisely bounded transaction (D-09, D-42) |
| Deployables and settings | three entrypoints, one settings class per entrypoint | five settings modules (`base`, `api`, `ingest`, `worker`, `stream`) plus `migrate` and `test`, each with its own database role; WSGI for api and ingest, ASGI for the stream pool; `hub-web` as a fourth deployable | D-43, D-44; D-22 role separation |
| API layer | FastAPI routers, generated spec | DRF serializers and viewsets, permission class from `api/permissions.yaml`, cursor pagination, problem handler, idempotency decorator, tier throttles; schemathesis proves conformance to the frozen `api/openapi.yaml` | D-42, D-14, D-12, 05 |
| Frontend | React and Vite single-page app, React Router | Next.js App Router, route groups for public, portal and staff; server components for public pages only; client components with in-memory token for authenticated screens; next-intl; CSP nonces; image optimiser and Server Actions off | ADR-0021, D-44, D-46 |
| DTOs and config | Pydantic everywhere | Frozen dataclasses; Django settings with `django-environ`; Pydantic only in `ai_gateway` for model-output schemas | D-42 |

Two additions for the tech lead: a `hub.settings.migrate` module (database role `migrator`) and a `hub.settings.test` module exist beyond the four named in D-43 (they are not deployables); and the `admin` app uses the label `hub_admin` to stay clearly distinct from the uninstalled `django.contrib.admin`.

Open items carried from the decisions and not decided here: the pull-verification threshold (D-09), revenue recognition at issuance (D-10), retention sign-off (D-28), portal wording about AI-assisted replies (D-35). Two points this document adds for the tech lead's confirmation: the exact permission names in section 2.14 must match `api/permissions.yaml` once it exists, and the `draft.money_affecting` co-approval is modelled as an approvals action although D-15's list does not name it.
