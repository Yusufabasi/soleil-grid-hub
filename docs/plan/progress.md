# Progress: Soleil Grid Hub build

Executor: implementing agent. Guide: `docs/plan/00-executor-guide.md`. Slices complete: **00**
(`docs/plan/slice-00-walking-skeleton.md`) and **01**
(`docs/plan/slice-01-identity-rbac-approvals.md`). Update this file after every task (status, commit,
test command and its summary line) and paste the full `make verify SLICE=00` output at the end
of the slice.

## Working mode from slice 02 (owner's decision, 2026-10-26)

The per-task cadence of slices 00 and 01 cost about 25–45 minutes per task, most of it ceremony
rather than code. From slice 02 the mode is batched per slice. It deviates from executor guide
rule 3 ("one commit per task") deliberately, on the owner's instruction; everything the guide
requires for correctness stays.

- **Kept per task**: the slice document's named tests, written first; contract and schema
  fidelity; the `progress.md` row with the test command and its result; `make verify SLICE=NN`
  and the demo at slice end.
- **Batched to the slice**: one commit (`feat(<module>): slice NN ...` with the task list in the
  body), one full `make lint`, one `make verify`, one demo on the gunicorn stack.
- **During the slice**: ruff, mypy and the touched test file only, run in the venv (seconds), so a
  gate failure is found where it is made rather than after a whole-suite run; the docker image is
  rebuilt only when the slice's task actually changes it.
- **No verification task**: the slice's last task carries the verification and the evidence.

The demo still runs once per slice on the multi-worker stack on purpose: that is what exposed
the per-worker signing key and the missing `api/` in the image, which single-process runs hid.

## Task log — slice 00

| Task | Scope | Status | Commit | Test command and result |
|---|---|---|---|---|
| 00.1 | Repository scaffolding, tooling and Makefile | done | `60db793` | `python -m pytest tests/test_infra_skeleton.py -q` -> `2 passed`; `make -n lint` exit 0 |
| 00.2 | Django project, settings modules, entrypoints, config checker | done | `1980883` | `cd backend && python -m pytest tests/architecture -q` -> `41 passed`; `python tools/check_django_config.py` -> exit 0 (9.4 s, runs `check --deploy` per module + `makemigrations --check`); ruff, ruff format, mypy strict clean
| 00.3 | Clock, IdGenerator, config helpers, container | done | `57d66eb` | `cd backend && python -m pytest tests/unit/shared -q` -> `12 passed`; mypy strict, ruff and ruff format clean
| 00.4 | Structured logging, request context, metrics, tracing | done | `181110d` | `cd backend && python -m pytest tests/unit/shared -q` -> `30 passed` (11 new); `pytest tests/unit tests/architecture -q` -> `71 passed`; ruff, ruff format, mypy strict clean; `python tools/check_django_config.py` -> exit 0 |
| 00.5 | Error taxonomy and problem+json handler | done | `00d1cd4` | `cd backend && python -m pytest tests/unit/shared/test_problem.py -q` -> `7 passed`; `pytest tests/unit tests/architecture -q` -> `78 passed`; mypy strict, ruff, ruff format clean |
| 00.6 | Health endpoints and readiness registry | done | `ebe2d81` | `cd backend && python -m pytest tests/integration/shared/test_health.py -q -m integration` -> `17 passed` (4 URLConfs x live/route tests); `pytest tests/unit tests/architecture -q` -> `78 passed`; mypy strict, ruff, ruff format clean; `python tools/check_django_config.py` -> exit 0 |
| 00.7 | DjangoUnitOfWork, outbox writer, worker command stubs | done | `45ba2f6` | `cd backend && python -m pytest tests/integration/shared/test_uow.py tests/unit/shared/test_commands.py -q -m integration` -> `7 passed`, `pytest tests/unit/shared/test_commands.py -q` -> `6 passed`; `pytest tests/unit tests/architecture -q` -> `85 passed`; `pytest tests/integration -q -m integration` -> `24 passed`; ruff, ruff format, mypy strict clean; `python tools/check_django_config.py` -> exit 0; `manage.py run_worker/run_relay/run_scheduler --once` -> exit 0 with `loop.started` then `loop.stopped` JSON lines |
| 00.8 | SQL baseline migration mechanism (`hubdb`) | done | `da2649c` | `cd backend && python -m pytest tests/integration/hubdb -q -m integration` -> `7 passed` (baseline byte copy, pairing rules incl. three negative trees, migrate-from-empty, catalog fingerprint equality, invariants on the migrated database, single-statement RunSQL); `pytest tests/integration -q -m integration` -> `31 passed`; `pytest tests/unit tests/architecture -q` -> `85 passed`; root `pytest tests -q` -> `36 passed`; ruff, ruff format, mypy strict clean; checker exit 0; `makemigrations --check` -> no changes |
| 00.9 | Import-linter contracts and architecture tests | done | `e3122fe` | `cd backend && python -m pytest tests/architecture/test_importlinter.py -q` -> `11 passed` (skeleton clean, seven planted violations each caught, AST admin ban, generator current, module list matches packages); `make lint` -> exit 0 with `Contracts: 119 kept, 0 broken`; `pytest tests/unit tests/architecture -q` -> `96 passed`; `pytest tests/integration -q -m integration` -> `31 passed`; root `pytest tests -q` -> `36 passed` |
| 00.10 | Images, compose stack, PgBouncer, HAProxy, stubs | done | `ab0fb82` | `python -m pytest tests/test_infra_skeleton.py -q` -> `6 passed` (five pools and routing order, log format without `%HU`/`%HQ`/`%r`, compose service and security matrix, one role URL per deployable, stub placeholders, PgBouncer transaction mode); `make up` -> 14 containers healthy, `migrate` and `db-logins` `Exited (0)`; `python -m pytest tests/test_stack_up.py -q -m integration` -> `1 passed`; `make lint` -> exit 0; root `pytest tests -q` -> `40 passed, 1 deselected` |
| 00.11 | CI pipeline, security scans, verify script | done | `f027004` (contract fix `b33070f`) | `python -m pytest tests/test_infra_skeleton.py -q` -> `8 passed` (2 new: required jobs, SHA pins and read-only token, oasdiff behind a tag condition, frontend guarded by `hashFiles`, security scanners, verify plan in order); `actionlint 1.7.12 .github/workflows/ci.yml` -> exit 0; `gitleaks 8.30.1 detect --config .gitleaks.toml` -> `no leaks found` (17 commits); `npx @stoplight/spectral-cli@6 lint api/openapi.yaml` -> `0 errors, 118 warnings`, exit 0; root `python -m pytest tests -q` -> `42 passed, 1 deselected`; `make lint` -> exit 0 |
| 00.12 | End-to-end verification, demo and evidence | done | `3153167` (defects fixed in `daeef46`, `938f2b3`) | `python -m pytest tests/test_stack_up.py -q -m integration` -> `5 passed` (five pools routed, request id owned by the balancer, stats UP, one JSON line per request with its trace id, no credential in the logs); `make verify SLICE=00` -> `Slice 00 verification PASSED (11 commands)`, exit 0; four failures observed first with the stack down (connection refused, empty logs) |

## Task log — slice 01

| Task | Scope | Status | Commit | Test command and result |
|---|---|---|---|---|
| 01.1 | Unmanaged identity models and the permission class | done | `9bb28a3` | `cd backend && python -m pytest tests/architecture/test_permissions_yaml.py tests/architecture/test_identity_models.py -q` -> `10 passed`; `pytest tests/unit tests/architecture -q` -> `108 passed`; `pytest tests/integration -q -m integration` -> `31 passed`; root `pytest tests -q` -> `42 passed, 5 deselected`; `make lint` -> exit 0 (ruff, format, mypy strict, import-linter 119 kept, Django config checks) |
| 01.2 | Audit adapter and audit view | done | `1326c43` (prerequisite fix `50e8ac9`, refactor `d9b9275`) | `cd backend && python -m pytest tests/integration/identity/test_audit.py tests/integration/admin/test_audit_view.py -q` -> `14 passed` (chained seq and prev_hash, actor and address stored without personal details, deny-list refused, outcome lifted out of details, `hub_api` cannot UPDATE or DELETE, a tampered row reported at its exact seq, an anchor that no longer matches the head reported, administrator page plus its own `audit.read`, support agent 403 writing nothing, cursor paging, tampered cursor 400, missing stream 400, action prefix); `pytest -q` -> `153 passed, 0 skipped`; root `pytest tests -q` -> `42 passed, 5 deselected`; `make lint` -> exit 0 (ruff, format, mypy strict 176 files, import-linter 119 kept, Django config checks); `check_traceability.py` -> 0 errors; `openapi.yaml` valid |
| 01.3 | EdDSA access tokens and bearer authentication | done | see below | `cd backend && python -m pytest tests/unit/identity/test_tokens.py tests/integration/identity/test_bearer.py -q` -> `18 passed` (600 s lifetime and claim set, no roles claim, `alg: none` refused, HS256 key confusion refused, leeway boundary on a frozen clock, edited payload refused, foreign issuer and over-long lifetime refused; live token authenticates, missing/garbage/non-bearer 401, stale `tv` 401 `token_revoked` with a warm and a cold cache, suspended and soft-deleted users refused, authority read from the database after a grant); `pytest -q` -> `171 passed`; `make lint` -> exit 0 (ruff, format, mypy strict 183 files, import-linter 119 kept, Django config checks) |
| 01.4 | Refresh sessions, cookie views and CSRF guard | done | see below | `cd backend && python -m pytest tests/integration/identity/test_refresh.py tests/integration/identity/test_csrf.py -q` -> `22 passed` (exact cookie attributes and no refresh token in JSON, rotation, replay one second later revokes the family, idle boundary at 1799/1800+ and absolute at the eighth hour, body transport only with the header, logout revokes and clears, refreshed principal keeps the login `auth_time`, a grant appears in the refreshed principal; four CSRF failures x two endpoints, a cookie alone does not authenticate a bearer operation); `pytest -q` -> `193 passed`; `make lint` -> exit 0 |
| 01.5 | Local break-glass login, TOTP, recovery codes | done | see below | `cd backend && python -m pytest tests/integration/identity/test_local_login.py -q` -> `11 passed` (wrong password and unknown address identical in status, code, detail and time; fifth failure closes the window with `Retry-After`, right password included; login never returns a token; verify returns a token pair and a cookie; TOTP replay refused; one step of drift accepted, two refused; recovery code single use and case/space insensitive; regeneration returns ten and deletes the old set; enrolment stores an encrypted secret and ten codes and signs in); `pytest -q` -> `204 passed`; `make lint` -> exit 0 |
| 01.6 | WebAuthn registration, assertion, enforcement | done | `abe7685` | `cd backend && python -m pytest tests/integration/identity/test_webauthn.py -q` -> `7 passed` (a real P-256 key registers and the next login asserts into a token pair; a challenge is single use; an assertion without the user-verification bit is refused; a rewound `sign_count` is refused; registration needs a bearer; with `HUB_WEBAUTHN_ENFORCED` a finance officer's TOTP is 403 `webauthn_required` and the challenge offers `["webauthn"]` with `enrollment_required: true` while a support agent still signs in with TOTP); `pytest -q` -> `211 passed`; `make lint` -> exit 0 (ruff, format, mypy strict 168 files, import-linter, Django config checks) |
| 01.7 | OIDC code exchange in hub-api | done | `3b2811a` | `cd backend && python -m pytest tests/integration/identity/test_oidc.py -q` -> `6 passed` (the start redirect carries `S256` PKCE, state, nonce and a `HttpOnly; Secure; SameSite=Lax` cookie scoped to `/api/v1/auth/oidc` for 600 s; a wrong state is 400 `invalid_request` and spends the flow so the right one cannot follow; an ID token without `mfa` in `amr` is 401 `mfa_required`; an unknown subject is 403 `account_not_provisioned` and creates no user; a known one gets a 302 to the stored `return_to` and a real rotating refresh session with `acr=mfa`; the client secret appears in neither the redirect nor the response); `pytest -q` -> `217 passed`; `make lint` -> exit 0 (ruff, format, mypy strict 170 files, import-linter 119 kept, Django config checks) |
| 01.8 | Step-up, `/me`, locale in the principal | done | `c8f9d8b` | `cd backend && python -m pytest tests/integration/identity/test_step_up.py tests/integration/identity/test_me.py -q` -> `39 passed` (299 s accepted and 301 s refused with 401 `step_up_required`, `step_up.max_age: 300` and `WWW-Authenticate: Bearer error="insufficient_user_authentication", max_age=300`; a TOTP step-up returns a token whose principal carries the fresh `auth_time`, leaves the cookie alone and then passes the guarded operation; the same code cannot be replayed; a token minted by a step-up dies with its session when the token version is bumped; every guarded rule refuses a stale principal and accepts a fresh one; `/me` returns roles, attributes, a real site id, locale, `mfa_enrolled`, `acr` and `auth_time`; `PATCH /me` stores `fr` and stores `de` as `en`; a 422 detail follows the caller's stored language with no `Accept-Language` header; `/me` without a token is a 401 with a bearer challenge); `pytest -q` -> `256 passed`; `make lint` -> exit 0 (ruff, format, mypy strict 172 files, import-linter 119 kept, Django config checks) |
| 01.9 | ApprovalService, executor registry, conflict check | done | `fbcbcf3` | `cd backend && python -m pytest tests/integration/identity/test_approvals.py -q` -> `22 passed` (the maker refused from a second session as well as the first surface; a presented `payload_hash` that differs from the stored one is 409 `approval_payload_mismatch` and the executor never runs; the executor receives the stored payload, not the decision body; a raised executor rolls the decision back and the row stays `pending`; an approver linked to the subject customer is 403 `conflict_of_interest`; a proposal past its TTL is 409 `approval_not_pending` and reads as `expired`; an approver without the executor's permission or attribute is 403 while one with it succeeds; a list shows only the kinds the caller may decide; all eleven schema actions refuse their own maker in a parametrised sweep; a rejected proposal cannot then be approved; a contract kind with no database action is 422 and a kind this caller may not propose is 403 `forbidden`); `pytest tests/integration/identity tests/integration/admin -q` -> `140 passed`; `mypy src` -> 178 files clean; `lint-imports` -> `119 kept, 0 broken` |
| 01.10 | User administration approvals and MFA reset | done | `fbcbcf3` | `cd backend && python -m pytest tests/integration/identity/test_user_admin.py -q` -> `10 passed` (`POST /users` answers 202 with a `user_creation` approval, a `Location` and no account in `GET /users` until a second administrator approves; the maker is 403 `self_approval_forbidden`; a taken address is 409 `duplicate_resource`; a role grant is `role.grant` and only changes roles, attributes and the token version on approval, after which the target's earlier access token is 401 `token_revoked`; a site change is a proposal too and shows up in the user document only afterwards; an MFA reset leaves the factor enrolled, the secret and the unused recovery code in place and the session live until approval, then clears the secret, deletes the codes, drops the credential and revokes the session; a second administrator is required for the reset as well; `PATCH /users/{id}` writes name and locale directly and suspending kills the target's token; the last administrator cannot be demoted and a fourth cannot be granted, both 409; `GET /roles` lists the four roles with their permissions and `GET /users?role=` filters); `mypy src` -> clean; `ruff check` -> clean; `lint-imports` -> `119 kept, 0 broken` |
| 01.11 | Customer one-time-code login with abuse controls | done | `87e4e28` (style prerequisite `648ac4a`) | `cd backend && python -m pytest tests/integration/identity/test_customer_otp.py -q` -> `9 passed` (a known and an unknown number both answer 202 with the same keys, a different `challenge_id` and the same limits, and the unknown one writes a decoy row with `max_attempts = 0` and sends nothing while the known one sends exactly one code in the requested locale; an unsolved and an already-spent proof are both 422 `proof_required`, the second proving the challenge is popped before it is checked; a prefix outside `HUB_OTP_ALLOWED_PREFIXES` is 422 `prefix_not_allowed` and writes no row; the three hourly ceilings each answer 429 with `Retry-After: 3600` at the fourth, eleventh and thirty-first request; five wrong codes kill the challenge and the sixth attempt is 401 `otp_invalid` even with the right code; a customer dormant 91 days gets a `reverify` challenge and signs in, while a `login` challenge requested before dormancy is 401 once the last sign-in is 91 days old; the session is a customer one with a 600 s access token, `type: customer` and a refresh cookie that still works at 299 s and is 401 `session_expired` at 300 s); `pytest tests/integration/identity tests/integration/admin tests/architecture -q` -> `216 passed`; `ruff check`/`ruff format --check` -> clean; `mypy` -> 219 files clean; `lint-imports` -> `119 kept, 0 broken` |
| 01.12 | Authorization matrix, RLS context and fixtures | done | see below | `cd backend && python -m pytest tests/contract/test_auth_matrix.py -q` -> `38 passed` (every one of the 33 operations `hub.urls.api` actually mounts, called with a real EdDSA token for each of the four staff roles: explicitly allowed cells never answer 401/403 and every other cell does; a public row asserts that a staff token leaves the anonymous answer unchanged, so the twelve public operations are exercised rather than skipped; anonymous GETs are 401 everywhere except the public ones; a refusal writes `authz.denied` to the `auth` stream with the actor, the operation and `reason: role`, `outcome: denied` inside the hash; a customer token is still 401 `token_revoked`, and machine-only operations are 401 without a key); `pytest tests/integration/identity/test_rls_context.py -q` -> `5 passed` (a unit of work sets `app.user_id`/`app.customer_id` for exactly its transaction and the next one sees no leftover claim; two interleaved transactions on reused `hub_api` connections each see only their own customer and the same connection without the claim sees zero rows; a site-bound support agent sees only their site and an `all_sites` user sees both; a customer claim cannot read or update another customer's row); `pytest tests/integration/identity/test_fixtures.py -q` -> `5 passed` (`load_fixtures` seeds an active account per role with the published password and TOTP secret, the `finance_lead` attribute, two administrators and one active `fr` customer, is idempotent, and refuses `HUB_ENV=production` with exit code 3 without writing); `pytest tests/unit/identity tests/integration/identity tests/integration/admin tests/architecture -q` -> `230 passed`; `make lint` equivalents -> ruff, format, mypy 223 files, import-linter 119 kept, Django config checks all clean; `make contracts` equivalents -> 34 passed, traceability 0 errors, `openapi.yaml` valid |

## Task log — slice 02

| Task | Scope | Status | Commit | Test command and result |
|---|---|---|---|---|
| 02.1 | Unmanaged registry models and repositories | done | this commit | `cd backend && python -m pytest tests/architecture/test_registry_models.py tests/integration/customers/test_repositories.py tests/unit/customers/test_phone.py -q` -> `22 passed` (both registry model sets align with `db/schema.sql` in both directions and stay unmanaged; a customer insert then an update shows `version` 1 -> 2 from the trigger, and the spent expected version then raises `VersionConflict`; `uq_customers_phone` surfaces as `DuplicatePhone` while `uq_sites_code`/`uq_plans_code` surface as 409 `duplicate_resource`; a scoped list with `site_ids=[]` matches nothing; phone normalisation turns eight typed shapes into one E.164 value and refuses six that are not numbers) |
| 02.2 | Sites and plans | done | this commit | `cd backend && python -m pytest tests/integration/customers/test_sites_plans.py -q` -> `8 passed` (operations creates a site and reads it back with `Location`, `health` and a computed `meter_count`; support is 403 on `createSite` and the administrator 403 on `listSites`/`getSite`; a duplicate code is 409 `duplicate_resource`; a support agent bound to site A sees only A and gets 404 for B while an `all_sites` agent sees both; the cursor pages without overlapping ids; finance creates a plan whose amounts come back as `Money`; inverted amounts and a currency mismatch are 422; the limits of an activated plan are 422 `plan_locked` and a retired plan cannot be revived (409)) |

## Assumptions

- **01.1 `Principal` carries one instant, two names**: the slice asks for the docs/03 2.2
  fields *plus* `acr` and `auth_time`. `auth_time` is the field (it is the JWT claim) and
  `stepped_up_at` is a property over it, so a step-up and a login cannot disagree about when
  identity was last proven. `acr` defaults to `pwd`; docs/05 requires at least `mfa`.
- **01.1 the kernel does not import `identity`**: `PermissionsYamlPermission` needs the caller,
  and `hub.shared` is forbidden from importing any business module (import-linter contract
  "hub.shared imports no business module"). `shared/api/permissions.py` therefore declares a
  `PrincipalLike` Protocol with the four fields it reads and duck-types `request.principal`;
  `identity.contracts.Principal` satisfies it structurally.
- **01.1 two Django lint rules are suppressed with a reason**: `DJ001` (no `null=True` on text
  fields) on the four columns `db/schema.sql` makes nullable — `users.password_hash`,
  `users.idp_subject`, `refresh_tokens.user_agent`, `audit_log.request_id` — because `""` is a
  value the schema's checks would have to accept; `DJ008` is satisfied once by an abstract
  `UnmanagedShim` base that defines `__str__`.
- **01.1 composite primary keys**: `role_permissions`, `user_roles`, `user_sites` and
  `staff_customer_link` have two-column keys Django cannot express, so the first column is the
  model's key and the repositories always address the row by the pair. Foreign keys that cross
  a module boundary (`customer_id`, `site_id`) are plain uuid columns: importing another
  module's models is forbidden.
- **01.1 contract path and user model are settings**: `HUB_CONTRACT_DIR` (derived from
  `HUB_REPO_ROOT`) is where the permission class reads `permissions.yaml` and `openapi.yaml`,
  and `AUTH_USER_MODEL = "identity.User"` names the shim for any `get_user_model()` call.
- **01.1 defect fixed in 01.2 — `db_table` was `hub.users`**: Django quotes `db_table` as one
  identifier, so every identity model addressed a table literally named `hub.users` and the
  first ORM insert failed. The schema resolves tables through each role's
  `search_path = hub, pg_catalog` (D-43), so the models now name the bare table
  (`50e8ac9`); the test alias sets the same `search_path` as a connection option, and
  `check_db_alignment` refuses a dotted `db_table`.
- **01.2 `outcome` is carried in `details`**: `hub.audit_log` has no column for it, and the
  contract's `AuditEntry` requires it. `record` stores it as `details["outcome"]` (hashed with
  the row, so it is tamper-evident) and the read path lifts it back out, leaving `details` with
  what the caller supplied. A schema column would need a migration; this does not.
- **01.2 `stream` is required, other audits are not filtered out**: `hub.audit_page` takes one
  stream, and `hub_api` has no SELECT on `hub.audit_log`, so the application cannot even list
  the streams that exist. A request without `stream` is refused with 400 `invalid_request`
  rather than silently answering one stream; proposed as an amendment in
  `docs/plan/contract-amendments.md` (`stream` becomes required on `listAuditLog`).
- **01.2 the audit read path is forward-only**: `hub.audit_page` returns `seq > after_seq`,
  oldest first, so `sort=-occurred_at` (the only other value the contract allows) is refused
  with 400 `invalid_request` until a `before_seq` variant exists. The slice's own demo reads
  the stream in increasing `seq`, which is what this gives.
- **01.2 the cursor is a position, not a signed token**: it is base64url of `{"s": seq}`, and
  anything that does not decode is 400 `invalid_cursor`. Signing belongs to the generic
  `CursorEnvelopePagination` of docs/03 7.7.5, which needs the cursor key setting; the value
  here reveals nothing a client cannot already read.
- **01.2 reading `admin` appends to `admin`**: the `audit.read` row goes to the stream it read,
  as the plan says, so the stream grows while it is paged. A page boundary can therefore be
  followed by the previous request's own read row; the paging test states this rather than
  hiding it.
- **01.2 wiring modules instead of `application/wiring.py`**: the plan lists the wiring under
  `application`, but the import-linter contract "Application does not depend on outer layers"
  forbids it (wiring names `infrastructure`), so `hub.identity.wiring` and `hub.admin.wiring`
  sit at package root, outside the layered contracts, like `bootstrap` does.
- **01.2 test-time authentication is patched onto the view**: DRF reads
  `DEFAULT_AUTHENTICATION_CLASSES` once, when `APIView` is imported, so a `REST_FRAMEWORK`
  override only worked while the view module happened to be unimported — it passed alone and
  returned 401 in the suite. The tests patch the view's `authentication_classes` with
  `support.principals.SeededPrincipalAuthentication` instead (Task 01.3 replaces it).
- **01.2 jsonb arrives as text**: Django's psycopg backend registers a text loader for `jsonb`
  (so its own JSONField can apply a custom decoder), so the adapter parses `details` and
  `record` still dumps with `Jsonb`.
  Neither adds an environment variable.
- **01.3 an ephemeral signing key pair in local and test, a hard failure elsewhere**:
  `HUB_JWT_PRIVATE_KEY`/`HUB_JWT_PUBLIC_KEY` are the only signing authority, and
  `settings/base` raises `ImproperlyConfigured` when they are missing outside `local`/`test`.
  In those two environments it mints a fresh Ed25519 pair at import so `make up` needs no
  secret in the repository; that is why a token is only valid within the process that issued
  it (fine for a single `hub-api` container locally, and the reason a real deployment sets
  both variables). `tools/check_django_config.py` carries a throwaway pair in its fallback
  environment, like its throwaway `HUB_SECRET_KEY`, so `--deploy` checks can load the module.
- **01.3 time is judged against the injected clock, not PyJWT's**: `jwt.decode` is told not to
  verify `exp`/`iat`/`nbf`; `AccessTokenService.verify` compares them with `Clock.now()` and the
  configured 30-second leeway. PyJWT would otherwise use the wall clock, which makes the
  leeway boundary untestable and makes the verifier disagree with the rest of the codebase
  about what "now" is.
- **01.3 the principal carries its permissions**: `Principal.permissions` is filled from
  `role_permissions` on every authentication, alongside roles, attributes and sites. One
  query more, no cache to invalidate, and the audit adapter's `audit.read` check stays the
  second line of defence it already was.
- **01.4 the session keeps its auth instant in Redis**: `hub.refresh_tokens` has no `auth_time`
  or `acr` column, and a refreshed token must not reset the step-up clock (D-16). `issue`
  stores `{"auth_time", "acr"}` under `session_auth:{session_id}` with the family's absolute
  TTL, rotation copies it to the successor row, and a lost entry falls back to the family's
  first `issued_at` with `acr="pwd"` — the conservative direction, because an older instant
  can only make a step-up more likely.
- **01.4 limits are injected, not read from settings**: the application layer may not import
  Django (import-linter), so `SessionService` takes `SessionLimits` for staff and customer and
  wiring passes the four settings. `limits_for(kind)` is what the boundary test pins.
- **01.4 the cookie views are plain Django views**: DRF is built around a body and these two
  read a cookie, so `refresh_view`/`logout_view` use `@require_POST` plus `@cookie_csrf_guard`
  and let `DomainErrorMiddleware` render the problem document. Body transport reads
  `refresh_token` from JSON only when `X-Token-Transport: body`; there is no serializer in the
  plain-view path because a DRF `ValidationError` would not be converted to problem+json there.
- **01.4 logout is idempotent and needs the cookie**: a logout without a cookie answers 204 and
  clears it, because there is nothing to revoke and surfacing a 401 would tell an attacker
  whether the cookie was still live. The bearer half of the operation's security block is not
  used yet; it arrives with `/me` in Task 01.8.
- **01.4 `Principal` gained `display_name` and `mfa_enrolled`**: `api/openapi.yaml` requires
  `display_name` in every `Principal` document, and the account facts belong to the caller's
  DTO rather than to a serializer that would have to fetch them again. They grant nothing.
- **01.5 TOTP time comes from the clock, not from pyotp**: `pyotp.TOTP.verify` compares
  against the wall clock, which a frozen test cannot move. Both the login and the enrolment
  confirm try the three candidate steps around `Clock.now()` explicitly and compare with
  `pyotp.utils.strings_equal`, so the ±1-step window is the same in production and in a test,
  and the replay guard keys on the counter of the step that matched.
- **01.5 the replay guard is a Redis key**: `hub.users` has no "last accepted counter" column
  (Plan note 3), so `totp:used:{user}:{counter}` with a 90-second TTL is the record. Losing
  Redis loses replay protection for at most one step, not the factor itself.
- **01.5 a wrong confirming code burns the pending enrolment secret**: the secret is only in
  Redis for 300 seconds, and `confirm` pops it before checking the code, so a wrong code means
  the client starts enrolment again. The alternative — retrying the same pending secret —
  would let an attacker keep guessing against one secret for the whole window.
- **01.5 `wiring.use_clock` is a test seam**: idle limits, the absolute limit and the step-up
  boundary are seconds apart, so proving them needs a frozen clock rather than a sleeping
  test. It resets the adapters that captured the previous clock and is documented as such.
- **01.6 an ES256 assertion is signed in ASN.1 DER**: `webauthn` 3.0.1 hands the signature
  straight to `cryptography`'s ECDSA primitive, which takes DER, and CTAP2 authenticators
  return exactly that (raw `r || s` is the internal CTAP message form, not the RP one). The
  software authenticator therefore sends the DER signature its key produced; the first draft
  sent raw halves and the server correctly refused it.
- **01.6 `enrollment_required` covers a key-only role without a key**: for a role in
  `WEBAUTHN_ENFORCED_ROLES` the challenge can only offer `webauthn`, so a user who holds no
  credential would have nothing to answer. The flag is now true for that case as well as for
  a missing TOTP secret, which is what sends them to the enrolment window.
- **01.6 the challenge store is Redis, keyed by an opaque id**: the browser gets a
  `challenge_id` and the bytes stay server-side for 300 seconds, so a replayed or forged
  challenge has nothing to match. `_consume` pops before checking and binds purpose and user
  id, which makes a challenge from another flow useless.
- **01.7 Authlib drives the flow, PyJWT verifies the ID token**: `OAuth2Session` builds the
  authorization URL (PKCE `S256` included) and performs the code exchange with the client
  secret in the `Authorization` header. `authlib.jose` is deprecated in authlib 1.9 in favour
  of `joserfc`, which the project does not declare, so the token itself is verified with the
  PyJWT that already signs access tokens: key by `kid` from the JWKS, then `iss`, `aud`, `exp`
  and the `nonce` compare.
- **01.7 `return_to` travels in Redis, not through the provider**: the callback only receives
  `code` and `state`, so the redirect target is stored beside the verifier and nonce when the
  flow starts. Only a same-site absolute path is accepted there (`//host` and absolute URLs
  fall back to `/`), which removes the one place an open redirect could start.
- **01.7 the provider identity is never provisioned on the fly**: an unknown `sub` is a 403
  `account_not_provisioned` and an audit row with `outcome=denied`; the administrator creates
  the user and records its subject first (docs/05). A provider outage is 503
  `provider_unavailable`, distinct from a token the provider refused, which is 401.
- **01.7 the routes are mounted only when `HUB_OIDC_ROUTES_ENABLED`**: no environment has an
  issuer yet (Plan notes 1), so `auth/oidc/start` and `auth/oidc/callback` are absent from the
  URLConf by default and the tests call the views through `RequestFactory` instead. Metadata
  and JWKS are cached for the process and refetched once when a signature fails, which covers
  a provider key rotation without a restart.
- **01.8 the step-up boundary is judged on the kernel's clock**: the permission class runs
  before any use case, so it cannot receive the identity module's clock. `hub.shared.clock`
  gained `current()` and a `use_clock()` test seam, and `wiring.use_clock()` moves both, which
  is what makes the 299/301-second boundary provable instead of a sleeping test.
- **01.8 a step-up refusal is a 401 with a challenge, not a 403**: the caller is allowed to act
  and only the authentication is too old, so `PermissionsYamlPermission` raises
  `Unauthenticated(code="step_up_required", details={"step_up": {"max_age": n}})`; both
  problem renderers add `WWW-Authenticate: Bearer error="insufficient_user_authentication"`
  and every other 401 gets the plain `Bearer realm="hub-api"` challenge it used to inherit
  from DRF. docs/05's `step_up.methods` is not emitted: the enrolled factors are already known
  from `/me/security`, and inventing the list in the kernel would contradict the account.
- **01.8 a step-up keeps its session and rotates nothing**: the response is a fresh access
  token and no `Set-Cookie`. The session's remembered `auth_time` and `acr` are updated with
  `SessionService.remember_authentication`, so a later refresh carries the step-up instead of
  silently falling back to the login instant.
- **01.8 WebAuthn step-up reuses `webauthnAssertionOptions` without an `mfa_token`**: the
  operation's request body makes `mfa_token` optional, and a caller who is already signed in
  gets options bound to a `step_up` challenge instead of a login one. The alternative was a
  new operation, and the contract files are frozen; `POST /auth/step-up` then takes
  `{method: webauthn, assertion: {challenge_id, credential}}`. The response schema of both
  operations is unchanged.
- **01.8 the TOTP window and replay guard moved into `application/totp.py`**: login and step-up
  must not disagree about which digits are acceptable or be able to spend the same counter
  twice, so one function answers both and the audit row for a replay is written there.
- **01.1 tests beyond the plan's three**: the plan names the completeness test, the
  role-denial test and the undeclared-view test. Two more pin the other two outcomes of the
  same class (the listed role is allowed, no principal is 401), so the 401/403/200 triangle is
  covered rather than inferred.

- **00.4 `SERVICE_NAME`**: the log lines and `http_requests_total{service,...}` series need a
  per-deployable label (docs/09 uses `service="hub-ingest"`), so each settings module sets a
  plain `SERVICE_NAME` constant (`hub-api`, `hub-ingest`, `hub-worker`, `hub-stream`,
  `hub-migrate`, `hub-test`; base defaults to `hub`). It is not an environment variable, so
  the Interfaces list of slice 00 is unchanged.
- **00.4 framework 4xx/5xx lines carry no `request_id`**: Django logs `status_code >= 400` in
  `BaseHandler.get_response`, *after* the middleware chain returns, and the context must be
  cleared before the response returns (`test_context_cleared_after_request`). Application log
  lines inside the request and the `X-Request-Id` response header both carry the id; those
  framework lines are correlatable by the header alone.
- **00.2 test made hermetic (fixed in 00.4)**: `test_ingest_requires_a_role` failed whenever a
  local `.env` exists, because `env.load()` re-populates the variable it deletes from the
  environment — and `make env`/`make up` always create that file. The test now stubs the
  `.env` loader so it proves the environment check itself.
- **00.5 problem `type` base URL (slice `[open]`)**: the default
  `https://hub.soleilgrid.example/problems/` is used (from `HUB_PROBLEM_BASE_URL` in
  `settings/base`, `.env.example` documents it). A problem `type` is
  `<base><code>`, e.g. `https://hub.soleilgrid.example/problems/not_found`.
- **00.5 `DomainError.__init__` signature**: the slice shows `DomainError(code, message_key,
  details)`; implemented as `__init__(code=None, *, message_key=None, details=None)` with
  class-level defaults per subclass, so `NotFound()` alone carries its catalogue code
  (`not_found`) and a module can pass a specific code (`customer_not_found`) plus an
  optional `details["errors"]` list that becomes the problem body's `errors[]`.
- **00.5 `Retry-After` on 503**: docs/05 requires `Retry-After` on 429 and 503; any 429/503
  problem response carries it (error-provided hint, else `retry_after_seconds` on
  `RateLimited`, else 30 s).
- **00.6 test infrastructure**: `backend/tests/conftest.py` starts one `postgres:16`
  container per session, applies `db/schema.sql` once as the superuser it expects, gives the
  `hub_api` role a dev-only LOGIN, and overrides pytest-django's `django_db_setup` instead of
  running `migrate` (slice 00 has no models, ADR-0020). docs/10 6.3 also says "cloned per test
  module"; with a single integration module the clone step would add a schema load per
  module for no isolation gain, so it is deferred to the module that needs it. Redis runs in
  its own container; the database alias is mutated in place because Django treats `DATABASES`
  as a complex override and a replaced dict leaves the connection handler on the old one.
- **00.6 readiness probes**: Postgres is probed with its own short-lived psycopg connection
  (`connect_timeout=2`, `statement_timeout=2000`) rather than Django's request connection, so
  a probe cannot inherit a poisoned connection or queue behind one, and the check is bounded
  as docs/09 1.4 requires. Redis is probed with `PING` (`socket_timeout=1`) and is not
  critical: an outage reports `degraded` and 200, per docs/09 8.2.
- **00.6 `service_unavailable` and 405**: docs/03 7.1 has no class for the readiness 503, so
  the view raises `ExternalUnavailable(code="service_unavailable",
  message_key="service_unavailable")` and `problem.py` carries the en/fr sentence;
  `MethodNotAllowed` (405) lives in `problem.py` because a plain view has no DRF machinery to
  turn `HttpResponseNotAllowed` into a problem document. Both go through `to_problem`, the
  single converter.

- **00.7 logging extras (found and fixed here)**: Task 00.4's `ProcessorFormatter` setup dropped
  every `extra={...}` field, because structlog builds a foreign record's event dict from the
  message alone. `structlog.stdlib.ExtraAdder()` now runs before the redaction processor, so a
  `top_up_id` reaches the line and a secret in an extra is still masked (commit `a6a4b56`).
- **00.7 test database role (superseded by 00.8)**: the harness first connected as
  `migrator`, then 00.8's invariant test proved that is wrong — `db/invariant_tests.sql`
  pins every application role as NOLOGIN, and roles are cluster-wide, so adding LOGIN to
  `migrator` for the harness broke a designed invariant. The harness now connects as the
  container's own superuser; a test that exercises a specific role creates its own login.
  `test_principal_context_is_transaction_local` remains the module's one `transaction=True`
  test: a principal set with `set_config(..., true)` only reverts at a real transaction
  boundary, and a savepoint release would keep it.
- **00.7 command shape**: `run_worker.py` holds `LoopCommand`, and `run_relay`/`run_scheduler`
  subclass it, so one implementation serves all three no-op loops without adding a kernel
  module the slice does not list. `handle()` returns `None` (Django's contract, so the process
  exits 0), `--once` runs a single iteration, `--group` is accepted and ignored by
  `run_worker`, and the loop stops through the command's own flag because a SIGTERM handler
  can only be installed from the main thread (a test runs the loop in a thread).

- **00.8 repo root for SQL files**: `HUB_REPO_ROOT` (already documented, empty in
  `.env.example`) names the directory holding `db/`; empty means "derive it from
  `settings/base.py`'s location", which is right for a checkout, and the image sets `/app`.
  `sql_text()` accepts only `^[0-9]{4}_[a-z0-9_]+$` stems, so a stem can never escape
  `db/baseline` or `db/migrations`.
- **00.8 catalog fingerprint**: compares relations, columns, constraints, indexes, triggers
  (`tgenabled`), functions (owner, `prosecdef`, `proconfig`), policies, ACLs expanded per
  grantee, `pg_db_role_setting` and `pg_auth_members`. Three normalisations are deliberate:
  `django_migrations` and its objects are excluded (it exists only in the migrated database),
  system namespaces (`pg_%`, `information_schema`) are excluded because TOAST relation names
  embed per-database OIDs, and the current database name inside `pg_db_role_setting` is
  rewritten to `{db}` because the two databases have different names.
- **00.8 migrate in tests runs as a subprocess**: the test invokes
  `manage.py migrate hubdb 0000 --settings=hub.settings.migrate` with an explicit environment
  (never the developer's `.env`), pointed at a superuser login, which is the documented
  first-run path in `hub.settings.migrate`.

- **00.9 layer syntax**: inside a `layers` contract with `containers`, the layer names are
  *relative* to each container, so the file says `api / application / domain` rather than
  `hub.*.api`; the doc's literal snippet makes import-linter look for
  `hub.<module>.hub.*.api`. Same intent, working syntax.
- **00.9 no-django-admin**: import-linter rejects subpackages of external packages
  (`django.contrib.admin`) while `include_external_packages = True` is required for the SDK
  contracts, so the ban is enforced by an AST scan in the architecture test instead — which
  also catches an import that never reaches the graph. The generated file records why.
- **00.9 SDK ignores**: `unmatched_ignore_imports_alerting = warn` is set on the two SDK
  contracts because the `anthropic_client` adapter and the pydantic models land in later
  slices; the warning stays visible in `make lint` output without failing the build.

- **00.10 two image tags in docs/09 no longer exist**: `edoburu/pgbouncer:1.23.1` was never
  published (that repository uses `v1.23.1-pN`) and `minio/minio` no longer resolves at all
  (upstream withdrew its Docker Hub images), so the stack uses `pgbouncer/pgbouncer:1.23.1`
  and MinIO's own binary from Bitnami's archived catalogue, pinned to a dated release tag as
  the plan asks (`bitnamilegacy/minio:2025.7.23-debian-12-r5`). Both keep the documented
  version and the service name; the substitutions are in the compose comments.
- **00.10 hub-web stub runs unprivileged**: the stack requires every `hub-*` container to be
  read-only, `cap_drop: [ALL]` and `no-new-privileges`, and the root `nginx:1.27-alpine` needs
  `CAP_SETUID`/`CAP_SETGID` to drop to its worker user, so the stub uses
  `nginxinc/nginx-unprivileged:1.27-alpine` and listens on 3000, the port the real Next.js
  server uses. Nothing about the balancer or the health check changes when `WEB=real` swaps it.
- **00.10 HAProxy: three fixes to make the sketch load**: `http-request allow-l7-retry` is not
  a directive in HAProxy 3.0, so the two ingest pools get `option http-buffer-request` (the
  actual precondition for retrying a POST) beside `retry-on conn-failure empty-response 503`;
  `http-response` rules cannot evaluate `path_beg` ACLs, so the pool decision and the static
  flag are captured in `txn.pool`/`txn.static` while the request is parsed and the response
  rules read those; and the admin socket lives in `/var/lib/haproxy` instead of `/var/run`,
  because the image runs as uid 99 and `/var/run` is a root-owned symlink, with a named volume
  so it survives a restart.
- **00.10 gunicorn control socket**: gunicorn 26 opens `$HOME/.gunicorn/gunicorn.ctl` by
  default, which is a read-only path in the image and unreachable from outside a container
  anyway; `control_socket_disable = True` is set because draining is driven by SIGTERM through
  `deploy/entrypoint-drain.sh`. The setting requires gunicorn >= 26, which `uv.lock` pins.
- **00.10 uv installed from PyPI**: the builder stage installs `uv==0.13.5` from the index the
  application dependencies already come from, instead of copying it out of `ghcr.io`; a build
  then depends on one registry, not two (the first real build failed on a ghcr.io lookup).
  uv >= 0.10 writes hashes into `uv export` by default, so the task's `--require-hashes`
  intent holds: pip installs in hash-checking mode from that file.
- **00.10 `logins.sql` lives outside `pg-init/`**: everything in
  `/docker-entrypoint-initdb.d` also runs at first boot, so a `logins.sql` there executed
  without its psql variables and aborted initialisation (the first `make up` failed on it).
  Only `00-create-db.sh` is an init script; the logins step is the separate `db-logins`
  service that runs after `migrate` completed.
- **00.10 `userlist.txt` is 0644, not 0600**: PgBouncer reads the file as uid 70 inside its
  container while the host user that runs `gen_dev_userlist.sh` is a different uid, so a
  private file is simply unreadable there (the first authentication attempt failed with `no
  such user: hub_api`). The values are the `dev` passwords of a throwaway cluster, the file is
  git-ignored, and a real environment takes its userlist from the secrets manager.
- **00.10 local host names**: `HUB_ALLOWED_HOSTS` gained `127.0.0.1` (the compose probes) and
  `hub` (the Host the balancer's health checks send, docs/09 1.4). `.env.example` also gained
  `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` (both `dev`), because the MinIO container needs
  credentials and the task 00.1 rule is that every documented variable is in that file.
- **00.10 `migrate` runs as the superuser locally**: `0000_baseline.sql` creates the roles,
  ownership and grants, so the compose service overrides `HUB_MIGRATE_URL` with the cluster
  superuser and says so in a comment; `.env.example` keeps the `migrator` value a real
  environment uses after that first step (docs/04 10.5).
- **00.10 default `make up` and the two stubs**: `provider-stub` and `meter-sim` sit on the
  `stubs` profile, so a plain `make up` starts 16 containers (14 running, `migrate` and
  `db-logins` exited 0) and stays quiet until slice-T1/T2 replace them. `make up` builds with
  `--build` so an edited Dockerfile is always picked up; the layer cache keeps a warm start
  to a few seconds.
- **00.10 Prometheus has a scrape file but no server**: the plan adds the Prometheus, Tempo,
  Loki and Grafana services with the dashboards (plan note 10), so `prometheus.yml` is the
  contract that slice mounts and the collector keeps the `debug` exporter in the meantime.

- **00.11 spectral needs a ruleset file**: the CLI does not default to the OpenAPI ruleset —
  without a `.spectral.yaml` it exits with "No ruleset has been found", and `--ruleset
  spectral:oas` is read as a path. A two-line `.spectral.yaml` (`extends: [spectral:oas]`) is
  the documented way, so the gate is `spectral lint api/openapi.yaml` exactly as the plan
  words it.
- **00.11 the OpenAPI gate found four real contract defects** (commit `b33070f`): the media
  examples of staff login, MFA verify, customer OTP request and the telemetry batch were the
  bare payload while their schema requires `{data: ...}`. Fixed in the specification rather
  than by lowering the rule: an example that contradicts its own schema is what a consumer
  copies. The document still reports 118 warnings (`operation-description`, unused
  components), which spectral does not fail on.
- **00.11 a seventh job: `stack`**: TC-NFR-14-1 needs "one command brings a clean clone to a
  healthy environment" proved, and docs/09 section 6 says the CI carries it; the job runs
  `make up` and `tests/test_stack_up.py` on the runner's Docker, dumping the stack's state and
  logs when it fails. It is independent of `images` (both build the same image, in parallel).
- **00.11 a skip fails the verification**: `verify_slice.sh` fails when a suite reports
  `N skipped`, because docs/10 section 3 forbids a green run built on skipped requirements —
  the integration suite must reach real Postgres and Redis. This is enforced by the script's
  runner, not by an extra command, so `--print` stays the plan's own list.
- **00.11 step 8 is two commands**: `make up` then the stack test, so a failing `make up` is
  reported on its own line instead of inside one long `&&` chain.
- **00.11 gitleaks allowlist**: `.gitleaks.toml` extends the default ruleset and allowlists
  four paths, each documented in the file — `.env.example` (throwaway `dev` values),
  `api/openapi.yaml` and `docs/` (fabricated examples that match the entropy and JWT rules by
  design) and `tests/` (the literals the redaction tests prove are masked). History is
  scanned whole; nothing was excluded by commit.
- **00.11 the frontend guard is per step, not per job**: a job-level `if` is evaluated before
  the checkout step, so `hashFiles('frontend/package.json')` is always empty there and the
  job would never run even after slice fe-00 lands. Every npm step carries the condition
  instead, with one step saying why the job did nothing.
- **00.11 action SHAs were taken from the GitHub API** on 2026-10-25 (checkout `v7.0.1`,
  setup-python and setup-node `v7.1.0`, setup-uv `v10.3.2`, buildx `v4.4.1`, build-push
  `v7.4.0`, trivy-action `v0.36.0`), and each line keeps its tag as a comment. Renovate
  proposes the next pin.
- **00.11 oasdiff and gitleaks are release binaries, not actions**: `gitleaks/gitleaks-action`
  needs a licence for organisations and `oasdiff` publishes no action, so both are downloaded
  from a pinned release tag into `$RUNNER_TEMP` and run directly, with no `curl | sh` step.
  The oasdiff comparison is skipped until a `v*` tag exists, as the task requires.

- **00.12 the stream pool runs one uvicorn process per container** (`daeef46`): with
  `--workers 2` the supervisor killed the first worker mid-startup at the cold start, because
  it pings a worker every 0.5 s and kills it after a 5 s silence, and the two processes also
  raced for the metrics port — the loser died with `EADDRINUSE` on the read-only root. One
  process per container, scaled by replicas (`server-template stream 2`), is the same shape as
  the rest of the stack and has no supervisor to lose a race against.
- **00.12 the metrics port is bound once, and losing it is not fatal**: `start_metrics_server`
  logs and returns on `EADDRINUSE` (regression test binds a socket first), and the compose
  environment now sets `PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus`, which `metrics.py` already
  promised, so the process that holds the port serves every worker's series.
- **00.12 healthchecks have a 45 s `start_period`**: a cold start imports Django in eight
  containers at once, and without the grace a slow first import counted as a failed check, so
  `up --wait` gave up on a stream container that was already coming up (the first cold start
  failed on exactly that).
- **00.12 MinIO needs an 8-character root password**: `.env.example` had `MINIO_ROOT_PASSWORD=dev`,
  which MinIO refuses; the entrypoint retried for a minute and exited 1, so `make up` failed on
  a container that used to be healthy because it predated the variable. The example and the
  compose default are now `dev-dev-dev`, and the local `.env` gained both MinIO lines.
- **00.12 one access line per request**: demo step 5 needs a JSON line carrying `request_id`,
  `trace_id` and `service`, and docs/09 8 assumes an application access log. `RequestContextMiddleware`
  now writes `request.completed` (method, path, status, route, duration_ms) while the context is
  still bound, and `request.path` never carries a query string (D-18). Health probes are logged
  at INFO too, which is ~2 lines/s on this stack; a later slice can sample them.
- **00.12 gunicorn's own lines are JSON as well**: gunicorn attaches its own handler and stops
  propagation, so `configure_logging` re-points `gunicorn.error`/`gunicorn.access` at the root
  structlog handler once the application is loaded (docs/03 7.3: "one format").
- **00.12 the tracing middleware must be outermost**: `middleware_position` moved 1 -> 0.
  Inside the application middleware the span is not current when the access line is written and
  `trace_id` was null; verified end to end — the line's trace id appears once in the collector.
- **00.12 a fresh pool answers 503 for a few seconds**: `docker compose up --wait` returns as
  soon as the container's own healthcheck passes, but HAProxy needs `rise 3` (~6 s) before it
  routes, so the stack tests poll for up to 20 s before asserting instead of reading the first
  response as the verdict.
- **00.12 the second `server-template` slot is `MAINT (resolution)`**: two slots are reserved
  for scale-out and only one container runs, so the stats test requires at least one `UP`
  server per pool and accepts a template slot with no container behind it as a placeholder.
- **00.12 the verify script calls the checker instead of four bare checks**: `manage.py check
  --deploy` loads the checkout's `.env` (`HUB_ENV=local`) and fails on DEBUG for every module,
  which says nothing about the deployable; `tools/check_django_config.py` runs the same check
  for all six modules with the fallback deployment environment.
- **00.12 demo step 8 as observed**: adding `django.contrib.admin` makes `make lint` fail inside
  mypy's Django plugin before the AST rule prints, so the rule is demonstrated with
  `python tools/check_django_config.py`, which names it (`D-42: never installed`); both gates
  are part of `make lint`.
- **01.11 dormancy is read from the session table**: `hub.customers` has no `last_login_at`
  column, so `CustomerDirectory` derives a customer's last sign-in from
  `max(refresh_tokens.issued_at)` for that customer. A completed login always writes that row,
  the value survives a revoked session, and there is no second copy to keep in step. The
  alternative — a migration adding a column — is what a later slice can do if the query ever
  needs its own index.
- **01.11 the SMS notifier is a recording adapter**: slice 07 owns the gateway integration, and
  the only behaviour the contract pins here is that an unknown number sends nothing. The
  adapter records what it would send and logs the event with the locale and the last two digits
  of the number, never the code; `CustomerOtpService` depends on the `Notifier` protocol, so
  wiring a real gateway changes one factory.
- **01.11 refresh builds a customer principal too**: the cookie flow is the same for both
  account kinds, and `_principal_for` previously refused a session with no `user_id`. It now
  asks `CustomerOtpService.principal_for`, which re-reads the customer (an erased or suspended
  customer is refused), so a customer's 12-hour absolute session can be rotated.
- **01.12 the matrix is bounded by what is mounted**: `api/openapi.yaml` declares 166
  operations for all sixteen slices and this build routes 33 of them. Asserting the other 133
  would only assert that Django answers 404, so the test resolves each path first and skips the
  ones with no route; the 12 further skips are the public operations, whose behaviour a staff
  token cannot prove and whose own suites (login, one-time code, WebAuthn, health) do prove it.
- **01.12 customer, integrator and gateway rows are pinned, not exercised**: hub-api has no
  customer handler beyond `/me` and no API-key authentication until slice 15, so there is no
  credential to present for those kinds yet. `test_customer_token_is_not_a_hub_api_credential`
  fixes today's answer (401 `token_revoked`) and `test_machine_operations_are_not_open_without_a_key`
  fixes that a machine-only operation is never open; both are expected to fail — loudly — when
  slice 02 and slice 15 land, at which point those rows join the matrix instead.
- **01.12 the object-level cell is proven at the row, not behind a route**: the "customer A
  reads customer B's wallet" cell needs a customer-scoped resource endpoint, and slice 01 mounts
  none. What is proven instead is the mechanism it will rely on: the forced RLS policies make a
  foreign customer's row invisible and immutable for `hub_api` under a customer claim, a
  site-bound staff claim sees only its sites, and every permission refusal now writes an
  `authz.denied` row. The 404-versus-403 policy for other customers' objects is slice 02's
  `require_site`/ownership work.
- **01.12 `load_fixtures` lives in `hub.identity`**: the plan's Files block puts it in
  `hub.shared.management.commands`, but `hub.shared` may not import a business module and the
  command needs `identity.infrastructure.crypto.encrypt_secret` to store a TOTP secret. A
  command in `hub.identity` is discovered by the same `manage.py` call. Slice T3 owns the full
  fixture package and can move it back once the secret-writing part is registered through a
  seam like the denial recorder.
- **01.12 the denied-access hook is a registration seam**: `hub.shared.api.permissions` cannot
  import the audit adapter (`hub.shared` imports no business module), so it exposes
  `set_denial_recorder` and `IdentityConfig.ready` installs `record_access_denied`. The hook
  writes to the `auth` stream, action `authz.denied`, with `operation`, `reason` and
  `outcome: denied` inside `details`; a failure to write is logged and never changes the 401/403
  the caller receives.
- **01.12 verification step 6 could not run**: `backend/tests/contract/test_openapi_conformance.py`
  (schemathesis) does not exist in this tree, so the `-k "Auth or Approvals or Admin"` selection
  collects nothing. The contract side that does exist — `tests/contract/test_auth_matrix.py`,
  the schema-in-postgres suite, traceability and the OpenAPI validator — is green. The
  conformance harness belongs to the verification-tooling track (T4) and is listed below.
- **01.12 the public rows of the matrix are asserted, not skipped**: the first version skipped
  the twelve public operations, which made `verify_slice.sh` fail the slice on its own
  "a skip is unproven" rule — correctly, since a matrix with a hole is not a matrix. A public
  row now presents a staff token and asserts the answer is *the same as the anonymous one*: a
  credential the operation does not need must neither help nor hurt, which is a real claim
  about the permission class and holds for the cookie routes too (an absent cookie still
  answers 401 with or without a token). `python -m pytest tests/contract/test_auth_matrix.py -q`
  -> `38 passed`, no skips.
- **slice 01 demo, defect 1: the local signing key was per process**: with the gunicorn stack
  in `infra/docker/compose.yaml`, each worker called `Ed25519PrivateKey.generate()` at import,
  so a token minted by one worker was unverifiable by the next and the demo saw an apparently
  random 401 (health checks hit every worker, and a 4-worker pool makes the failure look
  intermittent). Single-process test runs could never reproduce it. Local and test now derive
  the pair from `LOCAL_JWT_KEY_SEED` — deterministic, therefore public, and guarded by the
  existing `HUB_ENV not in ("local", "test")` start-up failure — so every worker of the local
  stack signs and verifies with the same key while a real deployment still refuses to boot
  without `HUB_JWT_PRIVATE_KEY`/`HUB_JWT_PUBLIC_KEY`.
- **02.1 `Principal` carries `all_sites`**: every site-scoped slice needs the flag, and asking identity for it per request would put a second query behind the same decision. It is a *scope* fact, loaded from `hub.users` with the roles in `load_principal` and `build_principal`, never a claim and never a grant: it widens which rows a caller sees, not what they may do.
- **02.1 the registry repositories catch the constraint by name**: `django.db.IntegrityError.__cause__.diag.constraint_name` is what the server reported, so `uq_customers_phone` becomes `DuplicatePhone` (D-11) and `uq_sites_code`/`uq_plans_code` a plain `Duplicate`, while any other integrity error keeps propagating instead of being disguised as a 409.
- **02.2 the meter count crosses modules through a registration seam**: the import-linter contract lets `hub.customers` see only `hub.meters.contracts` and `.events`, so `customers.wiring.use_meter_counter()` is filled by `MetersConfig.ready()`. `customers.reset()` deliberately keeps the registration — it is composition, not cached state.
- **02.2 audit details use `site_ref`/`plan_ref`, not `code`**: the audit adapter's deny-list refuses any key containing `code` (it is there to keep personal data and credentials out of the chain), so a site or plan code is recorded under a neutral name.
- **02.2 contract fields without columns are null**: `Site.latitude`, `longitude`, `capacity_kw` have no schema column and `health`/`meter_count` are computed (Plan notes 3); they are accepted on input and returned as `null`/`operational`/a count, never invented or stored.
- **slice 01 demo, defect 2: the API image lacked `api/`**: the permission class reads
  `api/permissions.yaml` and `api/openapi.yaml` from `HUB_CONTRACT_DIR`, and `backend/Dockerfile`
  copied `backend/` and `db/` only. Every gated operation answered 500 while the health routes
  (which need no contract rule) stayed green, so `make up` looked healthy. The image now copies
  `api/`, which is what `tools/check_django_config.py` and the architecture tests already assumed.

## Open change requests

None.

## Evidence

### `make verify SLICE=00`, 2026-10-25 (tree of `3153167`)

`tools/scripts/verify_slice.sh 00` ran eleven commands in order and printed
`Slice 00 verification PASSED (11 commands).` with exit 0. Transcript below, one result line per
command; the docker build progress of `make up` (about 430 lines of layer output) is elided and
its result kept.

```
$ make verify SLICE=00
==> make lint
All checks passed!                                       # ruff check
160 files already formatted                              # ruff format --check
Success: no issues found in 158 source files             # mypy strict
Contracts: 119 kept, 0 broken.                           # import-linter
Django configuration checks passed
==> cd backend && python -m pytest tests/unit tests/architecture -q
98 passed in 18.67s
==> cd backend && python -m pytest tests/integration -q -m integration -p no:cacheprovider --strict-markers -rs
31 passed in 37.24s
==> python -m pytest tests -q
42 passed, 5 deselected in 112.10s (0:01:52)
==> python tools/check_traceability.py
0 error(s), 0 warning(s)
==> python tools/check_django_config.py
Django configuration checks passed
==> cd backend && python manage.py makemigrations --check --dry-run --settings=hub.settings.api
No changes detected
==> make up
    ... 14 containers Healthy, migrate and db-logins Exited (0)
==> python -m pytest tests/test_stack_up.py -q -m integration
5 passed in 6.01s
==> docker compose --env-file .env -f infra/docker/compose.yaml config -q
    (no output, exit 0)
==> if command -v actionlint >/dev/null 2>&1; then actionlint; else ... fi
actionlint is not installed: workflow lint skipped
Slice 00 verification PASSED (11 commands).
```

Two differences from the slice's Verification list, both recorded as assumptions above: the
four per-module `manage.py check --deploy` commands are executed by `tools/check_django_config.py`
with its deployment fallback environment, and `actionlint` is optional in `make lint` — it is not
installed on this machine, so it was run by hand (`actionlint 1.7.12` -> exit 0).

### Demo script, as observed

| # | Command | Observed |
|---|---|---|
| 1 | `make reset && make up` (empty volume) | exit 0 in 67 s with the image already built (the 00.10 build adds about two minutes); 14 containers `Healthy`, `migrate` and `db-logins` `Exited (0)` — TC-NFR-14-1 well inside ten minutes |
| 2 | `curl -si localhost:8080/api/v1/health/live` | `HTTP/1.1 200 OK`, `x-request-id: 2eed5a0c-a9d3-46f1-a7a7-c2ba5200308b`, `{"data":{"status":"ok","version":"dev",...}}` |
| 3 | `curl -si localhost:8080/api/v1/health/ready` | `200`, `{"data":{"status":"ok",...,"checks":[{"name":"postgres","status":"ok","latency_ms":96},{"name":"redis",...,13}]}}` |
| 4 | `curl -si localhost:8080/` | `200` from the `hub-web` stub |
| 5 | `docker compose logs hub-api \| tail -1` | one JSON line: `{"method":"GET","path":"/api/v1/health/live","status":200,"route":"api/v1/health/live","duration_ms":0.683,"event":"request.completed","service":"hub-api","version":"dev","env":"local","level":"info","ts":"2026-10-25T02:38:37.528361Z","request_id":"2eed5a0c-...","correlation_id":"2eed5a0c-...","entrypoint":"hub-api","trace_id":"ae6d1045675b4a0fb6f35023ee0050af"}` |
| 6 | `docker compose logs otel-collector \| grep ae6d1045...` | one span: the trace id in the log line is the trace id the collector received |
| 7 | `docker compose stop hub-ingest-webhooks`, 9 s, stats; then `start`, 33 s | `be_webhooks DOWN` while `be_api UP` and `be_telemetry UP`; after the restart `be_webhooks UP` again and a POST through the balancer reaches the pool |
| 8 | `django.contrib.admin` in `INSTALLED_APPS`; `import anthropic` in `hub.payments.domain` | `python tools/check_django_config.py`: `INSTALLED_APPS installs django.contrib.admin (D-42: never installed)`; `lint-imports`: `hub is not allowed to import anthropic ... Only the ai_gateway adapter imports the AI SDK`. Both reverted; `git status` clean before the next run |

### Task 00.10: the stack, as observed

```
$ make up
... Container soleil-lb-1 Healthy
... 13 further containers Healthy (hub-api, both ingest pools, hub-stream, hub-web,
    hub-worker/relay/scheduler, pg-primary, pgbouncer, redis, minio, otel-collector)
... soleil-migrate-1 Exited (0)      soleil-db-logins-1 Exited (0)

$ curl -si localhost:8080/api/v1/health/live
HTTP/1.1 200 OK
x-request-id: 607bb2bd-6af9-4c2d-88fe-dad73ba2c3c3
content-security-policy: default-src 'none'; frame-ancestors 'none'

$ curl -s localhost:8080/api/v1/health/ready
{"data":{"status":"ok","version":"dev","time":"2026-10-25T01:36:06.517227+00:00",
 "checks":[{"name":"postgres","status":"ok","latency_ms":22},
           {"name":"redis","status":"ok","latency_ms":3}]}}

$ curl -si localhost:8080/
HTTP/1.1 200 OK
content-type: text/html

$ python -m pytest tests/test_stack_up.py -q -m integration
1 passed in 0.10s
```

The 503 that the first start produced was real and instructive: the readiness view answered
`service_unavailable` because PgBouncer could not read its userlist (see the assumption above),
which is exactly the failure the `postgres: ok` check is there to surface.

### `make verify SLICE=01`, 2026-10-26

`tools/scripts/verify_slice.sh 01` ran six commands in order and printed
`Slice 01 verification PASSED (6 commands).` with exit 0, in 3 m 55 s. The list is the slice
document's, with two deviations: the `uv run` spellings became `python -m` like slice 00 (the
project runs from `.venv`), and step 6 is guarded because the schemathesis harness it selects
does not exist in this tree (assumption above, track T4).

```
$ make verify SLICE=01
==> make lint
All checks passed!                                       # ruff check
227 files already formatted                              # ruff format --check
Success: no issues found in 225 source files             # mypy strict
Contracts: 119 kept, 0 broken.                           # import-linter
Django configuration checks passed                       # tools/check_django_config.py
==> make contracts
34 passed in 97.43s (0:01:37)                            # alignment, schema-in-postgres, traceability
0 error(s), 0 warning(s)
==> cd backend && python -m pytest tests/unit/identity tests/integration/identity tests/integration/admin tests/architecture -q
231 passed in 71.98s (0:01:11)
==> cd backend && python -m pytest tests/contract/test_auth_matrix.py -q
38 passed in 8.36s
==> cd backend && python ../tools/check_django_config.py
Django configuration checks passed
==> if [ -f backend/tests/contract/test_openapi_conformance.py ]; then ... else echo ...; fi
openapi conformance harness is not in this tree (track T4): see docs/plan/progress.md

Slice 01 verification PASSED (6 commands).
```

Two failures were observed first and fixed rather than worked around: `make lint` failed on an
import order in `hub/settings/base.py`, and the matrix step reported `26 passed, 12 skipped`,
which the script refuses (a skip is an unproven requirement — see the 01.12 assumption).

### Demo script, as observed (slice 01), 2026-10-26

Driver: `tools/demo_slice01.py`, reading every identity from `db/seed/test_users.json`. Run on an
empty volume (`make reset && make up && make seed`), it printed `All ten demo steps passed.`

| # | Step | Observed |
|---|---|---|
| 1 | `GET /api/v1/health/ready` after `make up && make seed` | `200`, `status: ok`, `checks: ['postgres', 'redis']`; `make up` left 14 containers healthy with `migrate` and `db-logins` `Exited (0)` |
| 2 | `POST /auth/staff/login` (`finance.lead@example.test`) | `200`, `mfa_required: true`, body keys `enrollment_required, methods, mfa_required, mfa_token, mfa_token_expires_in`, and no `access_token` |
| 3 | `POST /auth/staff/mfa/verify` with the fixture TOTP | `200`, `expires_in: 600`, `acr: mfa`, `refresh token in body: False`; `Set-Cookie: sg_refresh=<redacted>; expires=Tue, 27 Oct 2026 00:28:40 GMT; HttpOnly; Max-Age=28800; Path=/api/v1/auth; SameSite=Strict; Secure` |
| 4 | `POST /auth/refresh` with `Origin` and `X-Hub-CSRF: 1`, then the spent cookie, then its successor | `rotate -> 200 new cookie: True`; `replay -> 401 refresh_token_reused`; `successor after reuse -> 401 refresh_token_reused` — the family dies with the replay |
| 5 | `POST /auth/refresh` with the cookie and no `X-Hub-CSRF` | `403 csrf_check_failed`, and no rotation |
| 6 | Both administrators: staff login + MFA, then `POST /auth/step-up` on the next TOTP step | both `200`; the demo waits for the next 30 s step so the login does not spend the code the step-up needs |
| 7 | `POST /users` for `demo.support@example.test` as a stepped-up administrator | `202`, `Location: /api/v1/approvals/01a1790b-4a75-72f3-a584-74d32d8e86a6`, and `GET /users?q=demo.support` returns 0 rows |
| 8 | `POST /approvals/{id}/approve` as the maker, then as the second administrator | `403 self_approval_forbidden`; then `200 executed`, and `GET /users?q=demo.support` returns exactly `['demo.support@example.test']` |
| 9 | `GET /audit-log?stream=admin` | `200`, three entries with linked hashes: `1 approval.requested -> 43f79df5`, `2 user.create 43f79df5 -> 5ce0bba5`, `3 approval.granted 5ce0bba5 -> 5a210b58` (plus `4 audit.read`, written by this read) |
| 10 | `GET /auth/proof-challenge` then `POST /auth/customer/otp/request` for `+243819999999` (unknown) and `+243810000123` (the seeded customer) | both `202` with the same keys `['challenge_id', 'expires_in', 'resend_after']` |

Chain check on the same database:

```
$ docker compose exec -T pg-primary psql -U postgres -d hub -Atc \
    "select (hub.verify_audit_chain('admin') is null)::text, (hub.verify_audit_chain('auth') is null)::text"
true|true
```

The first run of this demo against the gunicorn stack failed at various points with a 401
`unauthenticated` on a token that had just been issued and, earlier, with 500 on every gated
operation. Both were real defects of the delivered image, not of the demo (per-worker signing
keys, and `api/` missing from the image); they are written up as assumptions above and fixed in
`backend/src/hub/settings/base.py` and `backend/Dockerfile`.
