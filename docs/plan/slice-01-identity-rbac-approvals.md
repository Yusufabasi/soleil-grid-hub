# Slice 01: Identity, RBAC and approvals

- **Goal:** staff and customers can sign in (OIDC, break-glass TOTP, WebAuthn, customer one-time code), hold 10-minute EdDSA access tokens and rotating refresh cookies, every operation is gated by a permission class generated from `api/permissions.yaml`, state-changing privileged actions go through maker-checker `ApprovalService`, and every such event lands in the hash-chained audit log, which an administrator can read.
- **Depends on:** slice 00 (skeleton: `shared` app, `DjangoUnitOfWork`, `Clock`, `IdGenerator`, `call_function`, problem handler, `@operation`, `TierThrottle`, `@idempotent`, `load_fixtures`, Makefile, `hub.settings.test`).
- **Requirements served:** FR-15, FR-16 (locale in principal, `PATCH /me`), NFR-06 (MFA, session, CSRF and permission-class parts), NFR-11 (locale of problem details).
- **Size:** L, 12 days.
- **Companion files:** `docs/plan/frontend/fe-01-auth.md` (login screens, step-up dialog, approvals inbox, audit view; written by another author, not linked because it may not exist yet).
- **Spikes or open items:** (1) **[open] company identity provider details** (issuer, client id, `amr` values): default is a fake IdP in tests and settings `HUB_OIDC_ISSUER`, `HUB_OIDC_CLIENT_ID`, `HUB_OIDC_CLIENT_SECRET`, required `amr` contains `mfa`. (2) **[open] WebAuthn go-live date:** default `HUB_WEBAUTHN_ENFORCED=false` in `base`, `true` in `api` settings when `HUB_ENV=prod`; tests force it true. (3) **[open] proof-of-work vs CAPTCHA:** default proof-of-work, difficulty setting `HUB_POW_DIFFICULTY_BITS=18`. (4) **[open] allowed prefixes:** default `+243`, `+33`, `+1` in `HUB_OTP_ALLOWED_PREFIXES`. (5) The OIDC start and callback routes are absent from `api/openapi.yaml` (see Plan notes 1); Task 01.7 builds them behind a disabled setting.

## Read first

- `docs/00-shared-context.md#7.4 Authorization and authentication` (D-13 to D-21)
- `docs/00-shared-context.md#7.7 Stack and delivery model (added after the user stated their preferred stack)` (D-42, D-43, D-46, D-47)
- `docs/adr/0006-authentication-and-sessions.md`
- `docs/adr/0007-maker-checker-approvals.md`
- `docs/03-code-architecture.md#2.2 identity (FR-15, FR-18, part of FR-16)`
- `docs/03-code-architecture.md#2.14 Approvals (maker-checker) (D-15, D-16)`
- `docs/03-code-architecture.md#7.1 Error taxonomy` and `#7.7 API layer conventions (Django REST framework)`
- `docs/05-api-design.md#4 Authentication` (staff login, step-up, one-time codes paragraphs)
- `docs/07-security-threat-model.md` (threats for authentication, approvals, IDOR; find with Grep on `T-0`, `refresh`, `approval`)
- `db/schema.sql` sections 2, 3b and the audit block (`hub.users` to `hub.api_keys`, `hub.approvals`, `hub.audit_*`, `hub.audit_page`, `hub.user_has_permission`, `hub.site_visible`)
- `docs/10-test-strategy.md#2.6 Authorization matrix (FR-15, FR-18, NFR-06)` and `#2.10 Controls added in revision 1`

## Global constraints

- Access token: JWT, `alg` EdDSA only, 10 minutes (`exp - iat = 600`), claims `sub`, `sid`, `tv`, `kind`, `loc`, `auth_time`, `acr`, `aud=hub-api`, `iss=hub`, leeway 30 s; `tv` must equal `users.token_version` (Redis cache 60 s) (D-17).
- Refresh token: opaque, single use, rotating, reuse revokes the family, no grace window; cookie `sg_refresh` with `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`; never in a body unless `X-Token-Transport: body` (D-17).
- Idle and absolute limits: staff 30 min and 8 h; customer 5 min and 12 h (D-17).
- Only `POST /auth/refresh` and `POST /auth/logout` read a cookie; both require `Origin == settings.SITE_ORIGIN` and `X-Hub-CSRF: 1`, else 403 `csrf_check_failed`; Django CSRF middleware is not installed (D-46).
- OIDC code exchange runs in hub-api: confidential client, PKCE, `state` bound to the browser; hub-web holds no secret (D-47).
- Step-up age at most 300 seconds, re-checked server side from the token, never from client input (D-16).
- Approver differs from maker by user and by session; threshold zero; payload hash bound; executor receives the stored payload (D-15).
- Administrator: no money, ticket, customer-content or ask-the-data operation (D-14).
- Customer one-time codes: always 202, no SMS to unknown numbers, 5 wrong codes kill a challenge, prefix allow-list, proof step, re-verify after dormancy (D-21).
- Staff roles only `support_agent`, `finance_officer`, `operations_engineer`, `administrator`; attributes `finance_lead`, `dp_admin` (D-13).
- Django admin not installed; `ATOMIC_REQUESTS` off; models `managed = False`; audit written only via `hub.audit_append`; no endpoint outside `api/openapi.yaml`.

## Review focus

1. **Refresh token replayed once, one second after rotation** (the browser-tab race): revokes the family, 401 `refresh_token_reused`. Pinned by `test_replay_after_one_second_revokes_family`; owned by Task 01.4.
2. **Approver is the maker in another browser** (same user, different session) and **the same session after a user switch**: both refused with 403 `self_approval_forbidden`. Pinned by `test_maker_second_session_refused`; Task 01.9.
3. **Payload edited between proposal and approval** (client sends a different `payload_hash`, or an executor receives a client body): 409 `approval_payload_mismatch`. Pinned by `test_payload_hash_mismatch_409`; Task 01.9.
4. **Step-up exactly on the boundary** (299 s ok, 301 s refused) and a step-up token minted for another user's session. Pinned by `test_step_up_boundary_with_fake_clock`; Task 01.8.
5. **Unknown and known phone must look identical** in status and body shape. Pinned by `test_unknown_and_known_phone_indistinguishable`; Task 01.11.

## Files

All paths under `backend/` unless stated. `tests/` paths are under `backend/tests/`.

- `src/hub/identity/apps.py`, `models.py` (shim): app registration (label `identity`).
- `src/hub/identity/infrastructure/models.py`: unmanaged `User`, `Role`, `Permission`, `RolePermission`, `UserRole`, `UserSite`, `RefreshToken`, `CustomerOtp`, `MfaRecoveryCode`, `WebauthnCredential`, `Approval`, `StaffCustomerLink`, `AuditLogRow`.
- `src/hub/identity/infrastructure/repositories.py`: `UserRepository`, `SessionRepository`, `ApprovalRepository`, `OtpRepository`.
- `src/hub/identity/infrastructure/audit.py`: `HubAuditLog` calling `hub.audit_append` and `hub.audit_page`.
- `src/hub/identity/domain/principal.py`: `Principal` (frozen dataclass of docs/03 section 2.2).
- `src/hub/identity/application/tokens.py`: `AccessTokenService`.
- `src/hub/identity/application/sessions.py`: `SessionService` (issue, rotate, revoke, idle and absolute limits).
- `src/hub/identity/application/local_login.py`: `LocalLogin`, `MfaService` (TOTP, recovery codes, lockout).
- `src/hub/identity/application/webauthn.py`: `WebauthnService`.
- `src/hub/identity/application/oidc.py`: `OidcLogin`.
- `src/hub/identity/application/stepup.py`: `StepUpService`.
- `src/hub/identity/application/customer_otp.py`: `CustomerOtpService`, `ProofService`.
- `src/hub/identity/application/approvals.py`: `ApprovalServiceImpl`, conflict check.
- `src/hub/identity/application/user_admin.py`: `UserAdmin` and executors `UserCreateExecutor`, `RoleGrantExecutor`, `MfaResetExecutor`.
- `src/hub/identity/application/wiring.py`: registers executors and services; `infrastructure/crypto.py` holds key, HMAC, Argon2id helpers; `tests/support/idp.py` is the fake IdP.
- `src/hub/identity/contracts.py`: public Protocols and DTOs (Interfaces section).
- `src/hub/identity/api/authentication.py`: `BearerJwtAuthentication`.
- `src/hub/identity/api/cookie_views.py`: plain Django `refresh_view`, `logout_view` with `@cookie_csrf_guard`.
- `src/hub/identity/api/oidc_views.py`: `oidc_start_view`, `oidc_callback_view` (not routed unless `HUB_OIDC_ROUTES_ENABLED`).
- `src/hub/identity/api/serializers.py`, `views.py`, `urls.py`: DRF resources (`/auth/*`, `/me`, `/users`, `/roles`, `/approvals`).
- `src/hub/admin/api/views.py`, `serializers.py`, `urls.py`: `listAuditLog` (app label `hub_admin`).
- `src/hub/shared/api/permissions.py` (modify): `PermissionsYamlPermission`, `@operation` uses it.
- `src/hub/shared/contracts.py` (modify): `ApprovalExecutor`, `ApprovalExecutorRegistry`.
- `src/hub/urls/api.py` (modify): mount `identity.api.urls` and `admin.api.urls`.
- `src/hub/shared/management/commands/load_fixtures.py` (modify): seed staff users per role and a customer.
- `tests/unit/identity/` and `tests/integration/identity/`, `tests/integration/admin/`, `tests/architecture/test_permissions_yaml.py`, `tests/contract/test_auth_matrix.py`: test files named in each task.

## Interfaces

**Consumes (slice 00):** `Clock.now()`, `IdGenerator.new_id()`, `UnitOfWork` (`with uow:`, `uow.outbox.add`, `uow.on_commit`), `shared.db.call_function(name, *args)`, `DomainError` family, `@operation("<operationId>")`, `@idempotent(scope, window)`, `TierThrottle`, `CursorEnvelopePagination`, `EnvelopeRenderer`, `env.secret`, Redis client factory `get_redis()`.

**Produces:**

```python
# identity/contracts.py
@dataclass(frozen=True, slots=True)
class Principal: ...  # fields exactly as docs/03 section 2.2, plus acr: str, auth_time: datetime | None

class Authenticator(Protocol):
    def authenticate_bearer(self, token: str) -> Principal: ...
class Authorizer(Protocol):
    def require(self, principal: Principal, permission: str) -> None: ...
    def require_site(self, principal: Principal, site_id: UUID) -> None: ...   # raises NotFound (404) outside scope
class AuditLog(Protocol):
    def record(self, *, stream: str, actor: Principal | None, action: str, entity_type: str,
               entity_id: UUID | None, site_id: UUID | None, details: Mapping[str, Any],
               outcome: Literal["success", "denied", "error"] = "success") -> UUID: ...
class ApprovalService(Protocol):
    def propose(self, *, action: str, payload: Mapping[str, Any], subject_type: str, subject_id: UUID | None,
                subject_customer_id: UUID | None, reason: str, maker: Principal, uow: UnitOfWork) -> ApprovalView: ...
    def approve(self, approval_id: UUID, *, payload_hash: str | None, approver: Principal, uow: UnitOfWork) -> ApprovalView: ...
    def reject(self, approval_id: UUID, *, approver: Principal, reason: str, uow: UnitOfWork) -> ApprovalView: ...
```

`ApprovalExecutor` gains `required_attribute: str | None`. `ApprovalExecutorRegistry.register/get`. Registry keys are the database `approvals.action` values (`tariff.change`, `user.create`, ...).

Other produced names: DRF class `hub.shared.api.permissions.PermissionsYamlPermission`; `hub.shared.api.permissions.require_step_up(principal, max_age=300)`; authentication class `hub.identity.api.authentication.BearerJwtAuthentication`; settings `HUB_JWT_PRIVATE_KEY`, `HUB_JWT_PUBLIC_KEY`, `HUB_ACCESS_TTL_SECONDS=600`, `HUB_STAFF_IDLE_SECONDS=1800`, `HUB_STAFF_ABSOLUTE_SECONDS=28800`, `HUB_CUSTOMER_IDLE_SECONDS=300`, `HUB_CUSTOMER_ABSOLUTE_SECONDS=43200`, `HUB_STEP_UP_MAX_AGE_SECONDS=300`, `HUB_APPROVAL_TTL_HOURS=72`, `SITE_ORIGIN`, `HUB_PHONE_HMAC_KEY`; audit streams `auth`, `admin`, `money`, `registry`; permission names are the schema's (`customer.read`, `user.admin`, `audit.read`, `config.manage`, ...); URL names equal the operationIds (for example `approveApproval`).

## Tasks

### Task 01.1: Unmanaged identity models and the permission class

- [ ] Write failing tests. `tests/architecture/test_permissions_yaml.py`: `test_every_openapi_operation_is_in_permissions_yaml` (iterate `api/openapi.yaml` operationIds, assert each key in `permissions.yaml` operations), `test_permission_class_denies_unlisted_role` (parametrised over all five roles and a dummy view with `@operation("getUser")`; assert 403 for all except `administrator`), `test_view_without_operation_is_refused` (assert 403 `forbidden`). `tests/architecture/test_identity_models.py`: `test_models_match_database` calls `check_db_alignment` for identity tables. Covers TC-FR-15-1, TC-FR-15-18.
- [ ] Run `cd backend && uv run pytest tests/architecture/test_permissions_yaml.py tests/architecture/test_identity_models.py -q`; expect ImportError on `PermissionsYamlPermission`.
- [ ] Implement the models (columns exactly as `db/schema.sql`) and `PermissionsYamlPermission`. Approach: at start load `api/permissions.yaml` (operation to role list) and from `api/openapi.yaml` the `x-step-up`, `x-site-scoped`, `x-ownership`, `x-requires-attribute` extensions into one `OperationRule` map; `has_permission` reads `view.operation_id`, maps the principal to the role set (`customer`, staff roles, API-key kind, `public`), returns 401 `unauthenticated` when no principal and the rule is not `public`, else 403 `forbidden`; step-up and attribute checks are exposed as helpers used by Tasks 01.8 and 01.9. Deny when the operation is missing.
- [ ] Run until green.
- [ ] Commit: `feat(identity): unmanaged identity models and permissions.yaml permission class`

### Task 01.2: Audit adapter and audit view

- [ ] Write failing tests. `tests/integration/identity/test_audit.py`: `test_record_appends_chained_rows` (two `record` calls; assert `seq` 1 then 2 and `prev_hash == first.hash`), `test_app_role_cannot_update_or_delete_audit` (raw `UPDATE`/`DELETE` as `hub_api` raise `InsufficientPrivilege`), `test_tamper_detected_by_verifier` (as `migrator`-owner scratch DB alter details; `SELECT hub.verify_audit_chain('auth')` returns the altered seq), `test_anchor_mismatch_detected`. `tests/integration/admin/test_audit_view.py`: `test_administrator_reads_page_and_read_is_audited`, `test_support_agent_gets_403`. Covers TC-FR-15-5, TC-FR-15-14.
- [ ] Run `uv run pytest tests/integration/identity/test_audit.py tests/integration/admin/test_audit_view.py -q`; expect missing `HubAuditLog`.
- [ ] Implement `HubAuditLog.record` calling `call_function("audit_append", stream, actor_type, actor_id, action, entity_type, entity_id, site_id, request_id, details_json, ip, subject_customer)`; it must be the last call in a unit of work (the advisory lock lives to commit). `details` must hold no key from a deny-list (`password`, `code`, `token`, `phone`, `email`). `listAuditLog` calls `audit_page(stream, after_seq, limit)`, maps to `AuditEntry` (hex hashes), supports `stream`, `action` prefix with `*`, `actor_id`, `entity_*`, `outcome` filters in Python over the page, and records an `audit.read` event on stream `admin`. Roles other than administrator get 403 (Plan notes 2).
- [ ] Run until green.
- [ ] Commit: `feat(identity): hash-chained audit adapter and audit log view`

### Task 01.3: EdDSA access tokens and bearer authentication

- [ ] Write failing tests. `tests/unit/identity/test_tokens.py`: `test_token_lifetime_is_600_seconds`, `test_alg_none_rejected`, `test_hs256_with_public_key_as_secret_rejected` (key confusion), `test_expired_token_rejected_after_leeway`, `test_tampered_role_claim_rejected`. `tests/integration/identity/test_bearer.py`: `test_stale_tv_rejected` (bump `users.token_version`, old token gets 401 `token_revoked` within 60 s because the bump also deletes the Redis cache key), `test_suspended_user_rejected`. Covers TC-FR-15-4 (token part).
- [ ] Run `uv run pytest tests/unit/identity/test_tokens.py tests/integration/identity/test_bearer.py -q`; expect ImportError.
- [ ] Implement `AccessTokenService.issue(principal, session_id, auth_time, acr) -> str` and `verify(token) -> Principal` with PyJWT `algorithms=["EdDSA"]`, `require=["exp","iat","sub","sid","tv"]`. Roles and permissions are never read from the token: they are loaded from the database through `UserRepository.load_principal(user_id)` (the token carries only identity), so claim tampering has no effect. `BearerJwtAuthentication` returns `(user, principal)` and sets `request.principal`. `token_version` cache key `tv:{user_id}`, TTL 60 s; `UserRepository.bump_token_version` deletes it.
- [ ] Run until green.
- [ ] Commit: `feat(identity): EdDSA access tokens with tv revocation`

### Task 01.4: Refresh sessions, cookie views and CSRF guard

- [ ] Write failing tests. `tests/integration/identity/test_refresh.py`: `test_set_cookie_attributes_exact` (assert `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/api/v1/auth`, name `sg_refresh`, and no refresh token string in the JSON body), `test_rotation_returns_new_cookie`, `test_replay_after_one_second_revokes_family` (rotate, advance `FakeClock` 1 s, replay old token: 401 `refresh_token_reused`; the newer token now also 401), `test_idle_and_absolute_expiry_staff_and_customer` (1800/28800 and 300/43200 boundaries), `test_body_transport_only_with_header`. `test_csrf.py`: `test_refresh_and_logout_reject_missing_foreign_origin_and_missing_header` (parametrised: no Origin, foreign Origin, no `X-Hub-CSRF`, value `0`; each 403 `csrf_check_failed`), `test_bearer_endpoint_ignores_cookie` (call `getMe` with only the cookie: 401). Covers TC-FR-15-12, TC-FR-15-13, TC-FR-15-17.
- [ ] Run `uv run pytest tests/integration/identity/test_refresh.py tests/integration/identity/test_csrf.py -q`; expect 404 on `/api/v1/auth/refresh`.
- [ ] Implement `SessionService.issue(subject, kind, ip, ua) -> (token, row)`, `rotate(presented) -> SessionTokens`, `revoke_family(family_id)`, `logout(presented)`. Store `sha256(token)` in `refresh_tokens.token_hash`; token is 32 random bytes base64url. `rotate` runs in one transaction with `SELECT ... FOR UPDATE` on the row found by hash: if `rotated_at` or `revoked_at` is set, revoke every row of the family, audit `auth.refresh.reuse`, raise `RefreshTokenReused`; limits are measured from `issued_at` of the newest row (idle) and the family's first row (absolute). `refresh_view` and `logout_view` are plain Django views using `@cookie_csrf_guard`; logout returns 204 and clears the cookie with the same attributes and `Max-Age=0`.
- [ ] Run until green.
- [ ] Commit: `feat(identity): rotating refresh cookie with reuse detection and CSRF guard`

### Task 01.5: Local break-glass login, TOTP, recovery codes

- [ ] Write failing tests. `tests/integration/identity/test_local_login.py`: `test_wrong_password_and_unknown_email_identical` (status, body, within 50 ms timing ratio using a dummy Argon2 verify), `test_progressive_lockout_per_account_and_ip` (5 failures then 429 `rate_limited` with `Retry-After`), `test_login_never_returns_access_token`, `test_totp_replay_refused` (same code twice: second 401 `invalid_code`), `test_totp_drift_one_step_accepted_two_refused`, `test_recovery_code_single_use`, `test_regenerate_codes_invalidates_old_and_returns_ten`, `test_enrollment_confirm_stores_encrypted_secret`. Covers TC-NFR-06-2 and part of TC-FR-15-4.
- [ ] Run `uv run pytest tests/integration/identity/test_local_login.py -q`; expect 404 on login.
- [ ] Implement `LocalLogin.start(email, password, ip) -> MfaChallenge` (Argon2id verify through `argon2-cffi`; always verify against a dummy hash for unknown emails; the `mfa_token` is a random id stored in Redis for 300 s with the user id) and `MfaService.verify(mfa_token, totp_code | recovery_code)`; pyotp, 30 s step, `valid_window=1`; replay blocked with Redis key `totp:used:{user_id}:{counter}` TTL 90 s (no column exists, Plan notes 3); lockout counters in Redis. Recovery codes: ten 10-character codes, stored as `sha256` in `mfa_recovery_codes`, shown once. TOTP secret encrypted into `users.mfa_secret_enc` with the key from settings. Successful verify calls `SessionService.issue` and `AccessTokenService.issue` (`acr="mfa"`), updates `last_login_at`, audits `auth.login`.
- [ ] Run until green.
- [ ] Commit: `feat(identity): break-glass login with TOTP and recovery codes`

### Task 01.6: WebAuthn registration, assertion and enforcement

- [ ] Write failing tests. `tests/integration/identity/test_webauthn.py` using `webauthn`'s test helpers or a software authenticator in `tests/support/authenticator.py`: `test_register_then_assert_logs_in`, `test_sign_count_regression_refused`, `test_user_verification_required`, `test_finance_officer_totp_refused_when_enforced` (setting `HUB_WEBAUTHN_ENFORCED=True`: `staffMfaVerify` with a valid TOTP returns 403 `webauthn_required` for `finance_officer` and `administrator`, still 200 for `support_agent`), `test_registration_requires_step_up`. Covers TC-FR-15-10.
- [ ] Run `uv run pytest tests/integration/identity/test_webauthn.py -q`; expect 404.
- [ ] Implement `WebauthnService.registration_options/register/assertion_options/assert_` with py_webauthn (`user_verification="required"`, RP id from `SITE_ORIGIN`); challenges stored in Redis 300 s single use; credentials in `webauthn_credentials`. A completed assertion issues the session exactly like TOTP verify (`acr="mfa"`, `amr` includes `hwk`). Enforcement lives in `MfaService.verify`: after password step, when enforced and the user holds `finance_officer` or `administrator` and has no credential, the challenge `methods` lists `webauthn` only and `enrollment_required` is true.
- [ ] Run until green.
- [ ] Commit: `feat(identity): WebAuthn credentials and enforcement for finance and administrator`

### Task 01.7: OIDC code exchange in hub-api

- [ ] Write failing tests. `tests/integration/identity/test_oidc.py` with `tests/support/idp.py`: `test_start_redirect_has_pkce_and_state_cookie`, `test_callback_with_wrong_state_refused`, `test_callback_without_mfa_amr_refused`, `test_unknown_subject_refused_not_provisioned`, `test_known_subject_gets_session_and_cookie`, `test_client_secret_never_in_redirect_or_response`. Views are called through `RequestFactory`, routes are not mounted.
- [ ] Run `uv run pytest tests/integration/identity/test_oidc.py -q`; expect ImportError.
- [ ] Implement `OidcLogin.authorization_url(browser_binding) -> (url, state_cookie)` and `complete(code, state, binding) -> SessionTokens` using Authlib `OAuth2Session`, PKCE `S256`, `state` and `code_verifier` kept in Redis keyed by a random browser-binding cookie (`sg_oidc`, `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth/oidc`, 600 s). The ID token is validated (signature by JWKS, `iss`, `aud`, `nonce`, `amr` contains `mfa`); the user is looked up by `users.idp_subject`; unknown subjects get 403 `account_not_provisioned`. Add `urls` entries only when `settings.HUB_OIDC_ROUTES_ENABLED` is true (default false, Plan notes 1).
- [ ] Run until green.
- [ ] Commit: `feat(identity): OIDC authorization-code login in hub-api behind a disabled route flag`

### Task 01.8: Step-up, `/me`, locale in the principal

- [ ] Write failing tests. `tests/integration/identity/test_step_up.py`: `test_step_up_boundary_with_fake_clock` (299 s accepted, 301 s gives 401 `step_up_required` with `WWW-Authenticate: Bearer error="insufficient_user_authentication", max_age=300`), `test_step_up_with_totp_returns_new_token_with_fresh_auth_time`, `test_step_up_token_of_other_session_refused`, `test_stale_step_up_blocks_approve_role_grant_key_export_erasure_operations` (parametrised over every operation with `x-step-up`; each returns 401 `step_up_required` with a fresh login token aged 301 s). `test_me.py`: `test_get_me_returns_locale_roles_attributes_sites`, `test_patch_me_locale_fr_persists_and_unsupported_locale_falls_back_to_en` (TC-FR-16-3 backend part), `test_problem_detail_localised_by_principal_locale`. Covers TC-FR-15-9.
- [ ] Run `uv run pytest tests/integration/identity/test_step_up.py tests/integration/identity/test_me.py -q`; expect 404 on `/auth/step-up`.
- [ ] Implement `StepUpService.step_up(principal, StepUpProof) -> str` (verifies TOTP with replay guard or WebAuthn assertion, issues a token with `auth_time = clock.now()`); `require_step_up` compares `clock.now() - auth_time <= 300` and is applied by `PermissionsYamlPermission` for any operation with `x-step-up`. `getMe` and `updateMe` return the `Principal` schema; an unsupported locale on update becomes `en` (stored). The problem handler uses `principal.locale`, else `Accept-Language`, else `en`.
- [ ] Run until green.
- [ ] Commit: `feat(identity): step-up authentication, /me and locale in the principal`

### Task 01.9: ApprovalService, executor registry, conflict check

- [ ] Write failing tests. `tests/integration/identity/test_approvals.py`: `test_maker_cannot_approve_own` (403 `self_approval_forbidden`), `test_maker_second_session_refused`, `test_payload_hash_mismatch_409`, `test_executor_receives_stored_payload_not_client_body`, `test_failed_execution_leaves_approval_pending` (executor raises; row stays `pending`, no side effect), `test_conflict_of_interest_blocks_approver_linked_to_customer` (403 `conflict_of_interest`), `test_expired_approval_cannot_be_approved`, `test_approver_without_required_permission_or_attribute_403`, `test_list_shows_only_kinds_caller_may_decide`, parametrised `test_every_action_requires_second_person` over all eleven schema actions with a fake executor. Covers TC-FR-15-7, TC-FR-15-8.
- [ ] Run `uv run pytest tests/integration/identity/test_approvals.py -q`; expect missing `ApprovalServiceImpl`.
- [ ] Implement `ApprovalServiceImpl` and the five `/approvals` operations. `propose` inserts into `approvals` (the trigger sets `payload_hash`; `expires_at = now + HUB_APPROVAL_TTL_HOURS`), checks the maker holds the proposing permission for the kind, audits `approval.requested`. `approve` locks the row, then checks in order: status pending and not expired, user and session differ, executor `required_permission` and `required_attribute`, step-up, `payload_hash` equals the stored hash when supplied, conflict check against `staff_customer_link` (maker and approver, via `subject_customer_id`), then sets `approved`, runs `registry.get(action).execute(stored_payload, view, uow=uow)`, sets `consumed`/`consumed_at`; any exception rolls everything back. Map API `kind` to database `action` with a constant table (Plan notes 4) and database `consumed` to API `executed`. Emit `approval.requested|granted|rejected` via the outbox.
- [ ] Run until green.
- [ ] Commit: `feat(identity): maker-checker ApprovalService with executor registry and conflict check`

### Task 01.10: User administration approvals and MFA reset

- [ ] Write failing tests. `tests/integration/identity/test_user_admin.py`: `test_create_user_returns_202_and_no_user_until_second_admin_approves`, `test_role_grant_executes_on_approval_and_bumps_token_version` (old access token then 401 `token_revoked`), `test_replace_user_sites_requires_approval`, `test_mfa_reset_has_no_effect_until_second_administrator_approves` (TC-FR-15-11), `test_mfa_reset_revokes_sessions_clears_secret_and_codes`, `test_administrator_refused_every_money_ticket_customer_askdata_operation` (parametrise over operationIds that exclude `administrator` in `permissions.yaml`, each 403; TC-FR-15-15), `test_list_roles_and_users`.
- [ ] Run `uv run pytest tests/integration/identity/test_user_admin.py -q`; expect 404 on `/users`.
- [ ] Implement `UserAdmin` (propose operations) and executors `user.create` (insert user, roles, sites, attributes), `role.grant` (replace roles and sites in `user_roles`/`user_sites`, bump `token_version`), `mfa.reset` (null `mfa_secret_enc`, delete recovery codes and credentials, revoke families, bump version); all require permission `user.admin`; the approver must be a different administrator. `updateUser` changes display name, status and locale directly and audits; suspending bumps `token_version`.
- [ ] Run until green.
- [ ] Commit: `feat(identity): user, role and MFA-reset approvals`

### Task 01.11: Customer one-time-code login with abuse controls

- [ ] Write failing tests. `tests/integration/identity/test_customer_otp.py`: `test_unknown_and_known_phone_indistinguishable` (both 202, same keys), `test_no_sms_for_unknown_number` (fake notifier port records zero sends), `test_proof_required_and_replay_refused`, `test_prefix_not_in_allow_list_blocked`, `test_burst_per_phone_per_ip_per_network_429` (limits 3 per phone per hour, 10 per IP per hour, 30 per /24 per hour, settings), `test_five_wrong_codes_kill_challenge` (6th with the right code gives 401 `otp_invalid`), `test_dormant_customer_requires_reverify` (no login for 90 days: success only with a `reverify` challenge), `test_customer_session_idle_300s`. Covers TC-FR-15-16.
- [ ] Run `uv run pytest tests/integration/identity/test_customer_otp.py -q`; expect 404.
- [ ] Implement `ProofService.challenge()/verify(proof_token)` (hashcash style, single use, 120 s) and `CustomerOtpService.request(phone, locale, proof, ip)`: reject bad proof or prefix with 422/429 before touching the database; compute `phone_hmac = HMAC(HUB_PHONE_HMAC_KEY, e164)`; always insert a `customer_otp` row (dummy `code_hash` for unknown numbers, flagged by `max_attempts=0`) and return 202 `OtpChallenge`; send via the `Notifier` port only when the number belongs to an active customer (lookup `customers.phone_e164`, read through a `CustomerLookup` Protocol that slice 02 binds; this slice supplies a fixture-backed implementation). `verify` counts attempts per challenge, issues a customer session (`kind=customer`, `acr="otp"`).
- [ ] Run until green.
- [ ] Commit: `feat(identity): customer one-time-code login with abuse controls`

### Task 01.12: Authorization matrix, RLS context and fixtures

- [ ] Write failing tests. `tests/contract/test_auth_matrix.py`: `test_matrix_every_role_by_every_operation` (for each operationId and each of the five principals plus `integrator`, `gateway`, `public`, call with a real token; allowed roles are not 403/401, others 403 or 401), `test_object_level_denied_and_audited` (customer A reading customer B's resource returns 404 and an `outcome='denied'` audit row). `tests/integration/identity/test_rls_context.py`: `test_principal_context_set_local_per_request` (two interleaved requests on one pooled connection never see each other's `app.user_id`), `test_missing_claim_returns_zero_rows`. Covers TC-FR-15-2, TC-FR-15-3 (object part), TC-NFR-06-4, TC-NFR-06-5.
- [ ] Run `uv run pytest tests/contract/test_auth_matrix.py tests/integration/identity/test_rls_context.py -q`; expect failures for missing fixtures and context.
- [ ] Implement: the unit of work sets `SET LOCAL app.user_id` / `app.customer_id` from `request.principal`; `load_fixtures` creates one active user per staff role with known test TOTP secrets in `db/seed/test_users.json` (test values only), a `finance_lead` finance officer, a second administrator, and one customer; add the denied-access audit hook in the permission class (`outcome="denied"`).
- [ ] Run until green, then the whole identity suite `uv run pytest tests -k "identity or admin or auth" -q`.
- [ ] Commit: `test(identity): authorization matrix, RLS context and fixtures`

## Verification

`make verify SLICE=01` runs, from the repository root:

1. `make lint` passes with zero findings (ruff, mypy, import-linter; pass: exit 0).
2. `make contracts` (pass: exit 0; alignment, schema-in-postgres and traceability tests green).
3. `cd backend && uv run pytest tests/unit/identity tests/integration/identity tests/integration/admin tests/architecture -q` (pass: 0 failed, 0 skipped).
4. `cd backend && uv run pytest tests/contract/test_auth_matrix.py -q` (pass: 0 failed).
5. `cd backend && uv run python ../tools/check_django_config.py` (pass: exit 0; no admin, `ATOMIC_REQUESTS` off, no managed models).
6. `cd backend && uv run pytest tests/contract/test_openapi_conformance.py -q -k "Auth or Approvals or Admin"` (pass: schemathesis checks green for those tags).

## Demo script

1. `make up && make seed`; `GET /api/v1/health/ready` returns 200.
2. `POST /auth/staff/login` for the fixture finance officer (password from `db/seed/test_users.json`): observe 200 with `mfa_required` and no `access_token`.
3. `POST /auth/staff/mfa/verify` with the current TOTP: observe an access token in the body and a `Set-Cookie: sg_refresh` with the four attributes and no refresh token in the body.
4. `POST /auth/refresh` with `Origin` and `X-Hub-CSRF: 1`: 200 and a new cookie; repeat with the old cookie: 401 `refresh_token_reused`, then the new cookie also fails.
5. As the administrator, step up, `POST /users` for a new support agent: 202 and an approval id; `GET /users` shows no new user.
6. Approve it with the same administrator: 403 `self_approval_forbidden`; with the second administrator after step-up: 200, the user now exists and the approvals list shows `executed`.
7. `GET /audit-log?stream=admin` as administrator: observe entries `approval.requested`, `approval.granted`, `user.create` with increasing `seq` and matching `prev_hash`; run `SELECT hub.verify_audit_chain('admin')` and observe NULL.
8. `GET /auth/proof-challenge` then `POST /auth/customer/otp/request` with an unknown and a known number: both 202 with the same body shape.

## Done criteria

Requirements covered: FR-15 (except API keys, slice 15), FR-16 (locale in principal), NFR-06 (MFA, session, CSRF, permission class).

Test cases implemented and passing: TC-FR-15-1, TC-FR-15-2, TC-FR-15-3 (object-level auth part; site-scope part in slice 02), TC-FR-15-4, TC-FR-15-5, TC-FR-15-7, TC-FR-15-8, TC-FR-15-9, TC-FR-15-10, TC-FR-15-11, TC-FR-15-12, TC-FR-15-13, TC-FR-15-14, TC-FR-15-15, TC-FR-15-16, TC-FR-15-17, TC-FR-15-18, TC-NFR-06-2, TC-NFR-06-4, TC-NFR-06-5, TC-FR-16-3 (backend half). TC-FR-15-6 (RLS sweep) is completed table by table in slice 02 and later slices.

## Plan notes for the tech lead

1. **OIDC routes missing.** D-47 and `docs/03#2.2` require an OIDC callback on the API side; `api/openapi.yaml` has no start or callback path, and `docs/05#4` says the IdP "sits outside this API". Executor guide forbids endpoints not in the OpenAPI file. I followed D-47 for behaviour and the OpenAPI file for routing: the code exists and is tested, routes are mounted only when `HUB_OIDC_ROUTES_ENABLED` is true. Needs a change request adding `GET /auth/oidc/start` and `GET /auth/oidc/callback`.
2. **Audit read roles.** `listAuditLog` allows administrator, finance_officer and operations_engineer, but `hub.audit_page` requires `audit.read`, which the schema seed gives only to administrator, and `hub_api` has no SELECT on `audit_log`. I followed the schema: other roles get 403 until a role-permission seed change.
3. **TOTP replay storage.** `docs/03#2.2` says the last accepted counter is stored; `hub.users` has no such column. I used a Redis key with a 90 s TTL.
4. **Approval vocabulary differs in three places.** Database `approvals.action` (`recon.resolve`, `apikey.create`, `data.export`), OpenAPI `ApprovalCreate.kind` (`reconciliation_resolution`, `api_key_creation`, `data_export`) and `docs/03#2.14` (`reconciliation.resolve_money`, `api_key.create`). Database wins (D-01); a constant mapping table converts API kinds. OpenAPI `draft_co_approval` and D-50 `draft.money_affecting` have no value in `ck_approvals_action`; also API status `cancelled` and `failed` do not exist in the database (`consumed` maps to `executed`). Slice 12 needs a schema migration.
5. **Approver permissions.** `docs/03#2.14` names `user:approve`, `tariff:approve` and others; none exists in the schema's permission seed. I used `user.admin` for user actions and `config.manage` plus attribute `finance_lead` for tariffs (the attribute excludes administrators, who also hold `config.manage`).
6. **Step-up claim names.** `docs/03` uses a `su` claim; OpenAPI and `docs/05` use `auth_time` and `acr`. I followed OpenAPI.
7. **`permissions.yaml` content.** `docs/03#7.7` says it holds step-up and site flags; it holds roles only, so the permission class reads the `x-*` extensions of `api/openapi.yaml` too.
8. **`listAuditLog` needs a stream.** `api/openapi.yaml` marks `stream` optional, but `hub.audit_page(stream, after_seq, limit)` reads one stream and `hub_api` has no SELECT on `hub.audit_log`, so the application cannot even list which streams exist. A request without `stream` answers 400 `invalid_request`. Needs a change request making `stream` required (or a cross-stream read function).
9. **The audit read path is forward-only.** `hub.audit_page` returns `seq > after_seq` oldest first, so `sort=-occurred_at`, the only other value the contract allows, answers 400 `invalid_request`. Newest-first needs a `before_seq` variant in a numbered migration; until then the audit screen pages from the oldest row, which is also what this slice's demo step 7 asks for.
10. **`AuditEntry.actor_type` omits `customer`.** `hub.audit_log` allows `customer` in `ck_audit_log_actor` and D-21 audits customer actions, while the contract's enum is `user, api_key, system, ai`. Rows written for a customer actor will not validate against the frozen schema; either the enum gains `customer` or customer actions are recorded as `user`. No such row is written yet.
11. **No `outcome` column.** The contract's `AuditEntry.outcome` has no column in `hub.audit_log`; it is stored as `details["outcome"]` and lifted by the read path, so it is inside the hash. A column would be a numbered migration (slice 16 owns retention work on this table).
12. **`AuditLog.record` takes `ip` and `subject_customer_id`.** The Interfaces block lists neither, but `hub.audit_append` does and a login audit without an address is not the record docs/07 S-8 asks for. Both are keyword-only with a `None` default, so the documented signature still calls it.
