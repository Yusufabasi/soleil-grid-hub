# Slice 15: Integrator and gateway API keys

- **Goal:** administrators create `gateway` and `integrator` API keys through a second-person approval; keys authenticate with a peppered HMAC, are limited by scope, bound sites, source range and per-key rate, can be revoked within 30 seconds and rotated with a 24-hour overlap.
- **Depends on:** slices 00 to 14, only through what `docs/03-code-architecture.md` section 10 says they produce: `shared` (UnitOfWork, Clock, Redis adapter, DRF helpers `PermissionsYamlPermission`, `@operation`, `TierThrottle`, `TIERS`, problem handler), `identity` (`Principal`, `ApprovalService`, `ApprovalExecutorRegistry`, `AuditLog`, step-up), the `hub-ingest` telemetry pool and slice 08's `ingestTelemetryBatch` for the end-to-end test. Slices 06 and 08 already authenticate gateway keys in a basic form; this slice replaces that with the hardened authenticator and does not change their routes.
- **Requirements served:** FR-18; supports NFR-06 (least privilege) and the T-07 and T-48 threats.
- **Size:** L, 7 days.
- **Companion files:** `frontend/fe-15-admin-keys-approvals.md` (key list, create form, approval inbox, one-time secret display, revoke and rotate). It uses only the operations below.
- **Spikes or open items:** [open] secret delivery: no operation in `api/openapi.yaml` returns the secret, so this plan returns it once inside `getApproval` as the optional `result` object (default, see plan note 1). [open] anomaly alert on a new source address or volume jump (T-07) is not built; default none. [open] an integrator key created without `site_ids` reaches all sites; default as stated, documented in the key screen. Pepper rotation means re-issuing keys (they live at most 180 days).

## Read first

- `docs/00-shared-context.md#D-19 API keys` (section 7.4), `#D-15 Maker-checker through one approvals resource`, `#D-22 Database roles and functions`
- `docs/adr/0007-maker-checker-approvals.md#Decision`, `docs/adr/0018-per-deployable-database-roles.md#Decision`
- `docs/03-code-architecture.md#2.2 identity (FR-15, FR-18, part of FR-16)` (Implementation notes, machine principals), `#2.14 Approvals (maker-checker) (D-15, D-16)`, `#7.7 API layer conventions (Django REST framework)` items 4 and 8
- `docs/04-data-model.md#10.7 Writes that never go through the ORM` (row "Authenticate a gateway or integrator key")
- `docs/05-api-design.md#9. API keys (D-19)` and `#10. Rate-limit tiers (authoritative for every document)`
- `docs/07-security-threat-model.md#4.5 API keys (D-19)` and threat rows T-07 and T-48 in `#3.3 Threat register`
- `docs/01-prd.md` acceptance criteria AC-FR-18-1 to AC-FR-18-5
- `docs/10-test-strategy.md#2.6 Authorization matrix (FR-15, FR-18, NFR-06)`
- `db/schema.sql` table `api_keys` (all constraints), `gateways`, `approvals`, function `verify_api_key`, the `hub_api`, `hub_ingest` and `hub_worker` grants

## Global constraints

- Key format `sgh_<env>_<keyid>_<secret>`; only an HMAC-SHA-256 with a server pepper from the secrets manager is stored (`key_hash`, 32 bytes); the secret is shown once.
- Maximum life 180 days, enforced by the database CHECK `ck_api_keys_expiry` and API validation (422 `validation_error`); expiry is required.
- Two kinds with disjoint scopes. `gateway`: `telemetry:write`, `tokens:acknowledge`, `gateway:heartbeat`. `integrator`: `customers:read`, `meters:read`, `topups:read`, `topups:write`, `tickets:read`, `tickets:write`, `tickets:inbound`.
- A gateway key needs `site_ids` and `allowed_cidrs` (mandatory); one key per physical gateway; a call from outside the range or for another site is 403.
- Creation and rotation go through the approval action `apikey.create` (second administrator, step-up within 300 seconds); `PATCH` never widens scopes, sites or expiry.
- Rate limits from the tier table of `docs/05` section 10: `gateway` 300/min per key; `integrator` standard 600/min and high 3,000/min per key; 429 carries `Retry-After`, every response carries `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`.
- Revocation takes effect within 30 seconds at most (OpenAPI); the PRD bound is 60 seconds.
- Rotation: old and new key both work for 24 hours unless the old one is revoked.
- Missing scope is 403 `insufficient_scope`; revoked, expired, unknown or wrong-secret keys are 401 with one indistinguishable body.
- The ingest role never reads `api_keys`; it calls the definer function. Secrets and HMACs never appear in logs, audit rows, outbox payloads or approval payloads.
- Only administrators manage keys (`api/permissions.yaml`: `createApiKey`, `getApiKey`, `listApiKeys`, `revokeApiKey`, `rotateApiKey`, `updateApiKey`).
- Header form `Authorization: ApiKey sgh_...`; cookies are never read on key routes.

## Review focus

1. **A revoked key still works from a pod with a warm cache.** Pinned by `test_revoked_key_rejected_within_30_seconds` in `tests/integration/identity/test_api_key_revocation.py`; Task 15.6.
2. **The secret leaks** into a log line, an audit row, the outbox, the approval payload or the database. Pinned by `test_secret_never_persisted_or_logged` in `tests/security/test_api_key_secret_hygiene.py`; Task 15.5.
3. **A gateway key from a foreign address or for another site's meter.** Pinned by `test_gateway_cidr_and_site_binding` in `tests/integration/identity/test_api_key_authentication.py`; Task 15.2.
4. **An approval executed days after the request** would push expiry past 180 days and violate the CHECK. Pinned by `test_lifetime_capped_at_180_days_when_approved_late` in `tests/integration/identity/test_api_key_creation.py`; Task 15.5.
5. **Rotation overlap edges** (23 hours both work, after 24 hours only the new one) and a second rotation proposed while one is pending. Pinned by `test_rotation_overlap_24h` and `test_second_rotation_while_pending_is_409` in `tests/integration/identity/test_api_key_rotation.py`; Task 15.6.

## Files

Create (under `backend/src/hub/`; tests under `backend/tests/`):

- `db/migrations/0150_api_key_verification.sql`: `hub.verify_api_key_v2`, `hub.expire_api_keys`, grants.
- `shared/migrations/0150_api_key_verification.py`: `RunSQL` applying that file.
- `identity/domain/api_key_format.py`: generate, parse and HMAC functions (pure).
- `identity/domain/api_key_rules.py`: creation, narrowing and CIDR validation (pure).
- `identity/application/api_key_verifier.py`: `KeyVerifier` with the 30-second cache and revocation markers.
- `identity/application/api_keys.py`: `ApiKeyService` (propose create, rotate, revoke, update, list, get).
- `identity/application/api_key_executor.py`: `ApiKeyCreateExecutor` for action `apikey.create`.
- `identity/application/jobs.py`: hourly expiry job (modify if slice 01 created the file).
- `identity/infrastructure/api_key_repository.py`: raw SQL reads and writes of `api_keys` and `gateways` links.
- `identity/infrastructure/api_key_db_verifier.py`: calls `hub.verify_api_key_v2`.
- `identity/infrastructure/reveal_store.py`: sealed one-time secret storage in Redis.
- `identity/infrastructure/key_rate_limiter.py`: sliding-window limiter per key.
- `identity/api/api_key_serializers.py`: `ApiKeySerializer`, `ApiKeyCreateSerializer`, `ApiKeyUpdateSerializer`, `ApiKeyRevealSerializer`.
- `identity/api/api_key_views.py`: `ApiKeyViewSet` (list, create, get, patch, revoke), `ApiKeyRotateView`.
- Tests: `tests/unit/identity/test_api_key_format.py`, `test_api_key_rules.py`; `tests/integration/identity/test_verify_api_key_function.py`, `test_api_key_authentication.py`, `test_api_key_scopes.py`, `test_api_key_rate_limit.py`, `test_api_key_creation.py`, `test_api_key_revocation.py`, `test_api_key_rotation.py`, `test_api_key_end_to_end.py`; `tests/security/test_api_key_secret_hygiene.py`.

Modify:

- `identity/api/authentication.py`: replace the basic `ApiKeyAuthentication` and `GatewayKeyAuthentication` with the versions below.
- `shared/api/permissions.py`: add the scope and kind check to `PermissionsYamlPermission`.
- `shared/api/throttles.py`: principals of kind gateway or integrator use `KeyRateLimiter` instead of the tier's per-user rule.
- `identity/api/approval_views.py`: `getApproval` adds the optional `result` object.
- `identity/application/wiring.py`: register `ApiKeyCreateExecutor`.
- `hub/settings/base.py`, `hub/settings/test.py`: `HUB_ENV_SHORT`, `HUB_API_KEY_PEPPER`, `HUB_REVEAL_KEY`, `HUB_API_KEY_MAX_DAYS = 180`, `HUB_KEY_CACHE_TTL_S = 30`.
- `Makefile`, `tools/verify/slice_15.sh`: verify target.

## Interfaces

**Consumes:**

```python
# identity.contracts (slice 01)
class ApprovalService(Protocol):
    def propose(self, *, action: str, payload: Mapping[str, Any], subject: Reference, subject_customer_id: UUID | None,
                maker: Principal, uow: UnitOfWork) -> ApprovalView: ...
class ApprovalExecutor(Protocol):           # shared.contracts
    action: str; required_permission: str
    def execute(self, payload: Mapping[str, Any], approval: ApprovalView, *, uow: UnitOfWork) -> None: ...
class AuditLog(Protocol):
    def record(self, *, actor: Principal | None, action: str, target_type: str, target_id: str,
               detail: Mapping[str, Any], uow: UnitOfWork) -> None: ...
# shared: Clock, IdGenerator, UnitOfWork, RedisAdapter, TIERS (tier table parsed from docs/05), @operation, required scopes from x-api-key-scopes
```

**Produces:**

```python
# identity/domain/api_key_format.py
KEY_PATTERN = r"^sgh_(dev|stg|prod)_[0-9a-f]{12}_[a-z2-7]{52}$"
@dataclass(frozen=True, slots=True)
class ParsedKey: env: str; key_id: str; secret: str
def generate_key(env: str, *, rng: Callable[[int], bytes] = secrets.token_bytes) -> tuple[str, ParsedKey]: ...   # full key, parts
def parse_key(raw: str) -> ParsedKey | None: ...
def hmac_key(raw: str, pepper: bytes) -> bytes: ...                  # HMAC-SHA-256(pepper, raw), 32 bytes

# identity/contracts.py (additions)
@dataclass(frozen=True, slots=True)
class VerifiedKey:
    api_key_id: UUID; kind: Literal["gateway", "integrator"]; scopes: frozenset[str]
    bound_site_ids: frozenset[UUID]; rate_limit_per_min: int
class KeyVerifier(Protocol):
    def verify(self, raw_key: str, *, source_ip: str) -> VerifiedKey: ...   # raises InvalidApiKey (401) or SourceNotAllowed (403)
    def revoke_marker(self, key_id: str) -> None: ...
class ApiKeyService(Protocol):
    def propose_create(self, cmd: ApiKeyCreateCommand, *, maker: Principal, uow: UnitOfWork) -> ApprovalView: ...
    def propose_rotate(self, api_key_id: UUID, *, maker: Principal, uow: UnitOfWork) -> ApprovalView: ...
    def revoke(self, api_key_id: UUID, *, actor: Principal, uow: UnitOfWork) -> None: ...
    def update(self, api_key_id: UUID, cmd: ApiKeyUpdateCommand, *, actor: Principal, uow: UnitOfWork) -> ApiKeyView: ...
    def reveal(self, approval_id: UUID, *, viewer: Principal) -> str | None: ...   # once; maker only
class KeyRateLimiter(Protocol):
    def hit(self, api_key_id: UUID, *, limit_per_min: int) -> RateDecision: ...     # allowed, remaining, reset_s, retry_after_s
```

**Database function** (migration `0150`): `hub.verify_api_key_v2(p_key_id text, p_key_hash bytea, p_ip inet) RETURNS TABLE (outcome text, api_key_id uuid, kind text, scopes text[], bound_site_ids uuid[], rate_limit_per_min integer)` with `outcome` one of `ok`, `invalid`, `source_not_allowed`; `hub.expire_api_keys() RETURNS integer`.

**Principal produced** for a key: `kind` gateway or integrator, `subject_id = api_key_id`, `roles = frozenset({kind})`, `scopes`, `site_ids = bound_site_ids`, attribute `all_sites` for an integrator key with no bound sites.

**Events:** `api_key.revoked` with `api_key_id, kind, revoked_by` (already in the `docs/03` catalogue; add the producer here).

**Serializer names:** `ApiKeySerializer` (never contains `secret` or `key_hash`), `ApiKeyCreateSerializer`, `ApiKeyUpdateSerializer`.

## Tasks

### Task 15.1: Key format, HMAC and the verification function

- [ ] **Step 1: failing tests.** `tests/unit/identity/test_api_key_format.py`: `test_generated_key_matches_pattern`, `test_two_keys_differ_and_key_ids_are_12_hex`, `test_parse_rejects_wrong_env_prefix_and_extra_segments`, `test_hmac_is_32_bytes_and_depends_on_pepper` (same key, two peppers, different digests), `test_secret_alphabet_has_no_underscore`. `tests/unit/identity/test_api_key_rules.py`: `test_gateway_requires_sites_and_cidrs`, `test_gateway_rejects_integrator_scopes_and_vice_versa` (TC-FR-18-5), `test_mixed_scopes_rejected`, `test_expiry_over_180_days_rejected_and_180_accepted` (TC-FR-18-6), `test_expiry_in_past_rejected`, `test_world_open_cidr_rejected` (`0.0.0.0/0`, `::/0`, and prefixes shorter than /8 for IPv4 for gateways), `test_tier_must_match_kind` (gateway: `gateway` only; integrator: `standard` or `high`), `test_narrowing_cidrs_only_subsets`, `test_tier_can_only_go_down`. `tests/integration/identity/test_verify_api_key_function.py`: `test_ok_returns_scopes_and_sites`, `test_wrong_hash_is_invalid`, `test_revoked_expired_unknown_all_invalid_with_same_shape`, `test_ip_outside_cidr_is_source_not_allowed_only_after_hash_matches` (wrong hash plus wrong IP is `invalid`), `test_last_used_updated_at_most_once_a_minute`, `test_hub_ingest_cannot_select_api_keys_but_can_execute_function`, `test_expire_api_keys_marks_only_active_expired_rows`, `test_function_owner_and_search_path_pinned`.
- [ ] **Step 2: run.** `cd backend && uv run pytest tests/unit/identity/test_api_key_format.py -q`. Expected: ImportError for `hub.identity.domain.api_key_format`.
- [ ] **Step 3: implement.** `generate_key`: env from settings `HUB_ENV_SHORT` in `dev, stg, prod`; `key_id = token_hex(6)`; `secret = base32(token_bytes(32)).lower().rstrip("=")` (52 characters). `hmac_key` uses `hmac.new(pepper, raw.encode(), sha256).digest()`; compare digests with `hmac.compare_digest` anywhere they are compared in Python. `api_key_rules.py` holds the two scope sets as constants compared with the schema CHECK by `tests/test_alignment.py`-style assertion in `test_api_key_rules.py::test_scope_sets_match_schema_check`. Write the migration: `verify_api_key_v2` is `SECURITY DEFINER SET search_path = pg_catalog, hub, pg_temp`, selects by `key_id`, returns `invalid` when no row, hash differs (`k.key_hash <> p_key_hash`), status is not `active` or `expires_at <= now()`; then `source_not_allowed` when `cardinality(allowed_cidrs) > 0` and no CIDR contains `p_ip`; else updates `last_used_at` at most once a minute and returns `ok` with the columns. `expire_api_keys` sets `status = 'expired'` where `status = 'active' AND expires_at <= now()` and returns the count. `GRANT EXECUTE` of `verify_api_key_v2` to `hub_api` and `hub_ingest`, of `expire_api_keys` to `hub_worker`; no PUBLIC grant. Keep the old `verify_api_key` untouched for slices 06 and 08 until Task 15.2 switches them.
- [ ] **Step 4: run until green.** `uv run pytest tests/unit/identity tests/integration/identity/test_verify_api_key_function.py -q`; then `make contracts`.
- [ ] **Step 5: commit.** `feat(identity): api key format, peppered hmac and verification function`

### Task 15.2: Authentication classes, cache and source binding

- [ ] **Step 1: failing tests.** `tests/integration/identity/test_api_key_authentication.py`: `test_valid_key_builds_principal` (kind, scopes, site ids, `roles == {kind}`), `test_unknown_revoked_expired_and_bad_secret_give_identical_401_body`, `test_wrong_env_prefix_is_401`, `test_gateway_cidr_and_site_binding` (call from outside the range: 403 `source_not_allowed`; telemetry for a meter of another site: that reading rejected per reading and the other readings accepted; the key cannot call `acknowledgeToken` for a token of another site: 403), `test_integrator_with_no_sites_has_all_sites_attribute`, `test_header_must_be_apikey_scheme` (Bearer value with an `sgh_` string is 401), `test_cookie_ignored_on_key_routes`, `test_positive_cache_hits_function_once_in_30_seconds`, `test_negative_cache_limits_repeated_bad_keys` (5 seconds), `test_last_hop_x_forwarded_for_is_the_source_ip`.
- [ ] **Step 2: run.** `uv run pytest tests/integration/identity/test_api_key_authentication.py -q`. Expected: failures because the basic authenticator from earlier slices lacks these behaviours.
- [ ] **Step 3: implement.** `KeyVerifier.verify`: parse, compare env, compute the HMAC, look up the in-process cache keyed by `sha256(key_id + hmac)` (TTL `HUB_KEY_CACHE_TTL_S` = 30; negative entries 5 s), check the Redis marker `apikey:revoked:{key_id}` (50 ms timeout; Redis down means skip), else call `hub.verify_api_key_v2(%s, %s, %s)` through `api_key_db_verifier` with the connection of the current deployable's role (`hub_ingest` on telemetry, `hub_api` elsewhere), map `invalid` to `InvalidApiKey` (401 `invalid_api_key`, identical body) and `source_not_allowed` to `SourceNotAllowed` (403). The cache stores the verified result together with the source IP it was verified for, so a different IP always re-verifies. `ApiKeyAuthentication` (DRF) reads `Authorization: ApiKey ...`, returns `(None, principal)`; `GatewayKeyAuthentication` additionally requires kind `gateway`. The `@operation` metadata sets `authentication_classes` from the operation's `security` list in `api/openapi.yaml`: operations without `apiKey` get only the bearer class. Add a settings switch removing the old `verify_api_key` call sites; grep proves none remain.
- [ ] **Step 4: run until green.** same file plus `uv run pytest tests/integration/telemetry/test_ingest_api.py -q` (slice 08 must stay green).
- [ ] **Step 5: commit.** `feat(identity): hardened api key authentication with source binding and cache`

### Task 15.3: Scope and kind checks

- [ ] **Step 1: failing tests.** `tests/integration/identity/test_api_key_scopes.py`: `test_scope_matrix_from_openapi` (for every operation with `x-api-key-scopes`, parametrised: a key of the allowed kind holding exactly that scope passes the permission layer; the same kind without it is 403 `insufficient_scope`; TC-FR-18-1), `test_gateway_key_cannot_call_integrator_operations` and `test_integrator_key_cannot_call_gateway_operations` (403 `forbidden`; TC-FR-18-5), `test_api_key_on_bearer_only_operation_is_401` (for example `listUsers`), `test_key_cannot_call_staff_only_operation_even_with_all_scopes`, `test_denial_is_audited_once_per_minute_per_key_and_scope` (two denials inside a minute, one audit row `api_key.scope_denied`, no secret in `detail`), `test_scope_sets_are_disjoint` (intersection empty).
- [ ] **Step 2: run.** `uv run pytest tests/integration/identity/test_api_key_scopes.py -q`. Expected: `test_scope_matrix_from_openapi` fails with 200 where 403 is expected.
- [ ] **Step 3: implement.** In `PermissionsYamlPermission.has_permission`, after the principal-kind check against `permissions.yaml`, when `principal.kind in {"gateway", "integrator"}` compute `required = openapi_meta.required_scopes(view.operation_id)`; an empty requirement for a key principal is a deny (deny by default); `required - principal.scopes` non-empty raises `PermissionDenied(code="insufficient_scope")`. Audit through `AuditLog.record(actor=None, action="api_key.scope_denied", target_type="api_key", target_id=str(api_key_id), detail={"operation": id, "missing": [...]})`, rate limited with a Redis `SET NX EX 60` key per `(api_key_id, scope)`.
- [ ] **Step 4: run until green.** same file, plus `uv run pytest tests/contract -k "gateway or inbound" -q`.
- [ ] **Step 5: commit.** `feat(identity): enforce api key scopes and kind separation`

### Task 15.4: Per-key rate limits

- [ ] **Step 1: failing tests.** `tests/integration/identity/test_api_key_rate_limit.py`: `test_tier_to_limit_matches_docs_05` (gateway 300, standard 600, high 3000, read from `TIERS`), `test_429_with_retry_after_when_exhausted` (key row with `rate_limit_per_min = 5`: sixth call in a minute gives 429, `Retry-After` between 1 and 60, body unprocessed: no row written; TC-FR-18-3), `test_recovers_after_window` (fake clock plus 61 seconds: 200), `test_headers_on_every_response` (`RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`), `test_two_keys_have_independent_counters`, `test_redis_down_falls_back_to_local_bucket` (limit divided by pod count, never zero), `test_limit_checked_before_processing` (a 429 call does not reach the view), `test_probe_routes_exempt`.
- [ ] **Step 2: run.** `uv run pytest tests/integration/identity/test_api_key_rate_limit.py -q`. Expected: ImportError for `key_rate_limiter`.
- [ ] **Step 3: implement.** `RedisKeyRateLimiter.hit`: one Lua script over a sorted set `rl:key:{api_key_id}` with 60-second sliding window, returns allowed, remaining, `reset_s` and `retry_after_s` (seconds until the oldest hit leaves the window, minimum 1). Fallback: per-process token bucket of `max(1, limit // pod_count)`, `HUB_POD_COUNT` setting. `TierThrottle.allow_request` delegates to the limiter for key principals using `principal.rate_limit_per_min` (the column, which equals the tier default at creation) and sets the headers through the shared response hook. The throttle runs before the view and after authentication.
- [ ] **Step 4: run until green.** same file.
- [ ] **Step 5: commit.** `feat(identity): per-key sliding-window rate limits with retry-after`

### Task 15.5: Creation through an approval, secret shown once

- [ ] **Step 1: failing tests.** `tests/integration/identity/test_api_key_creation.py`: `test_create_returns_202_with_approval_and_no_key_row` (`createApiKey` response is an `Approval` with `kind = api_key_creation`; `api_keys` count unchanged), `test_create_requires_step_up_and_admin` (401 `step_up_required` after 301 seconds; support agent 403), `test_validation_errors_are_422` (parametrised: gateway without CIDR, mixed scopes, expiry over 180 days, unknown site id, world-open CIDR), `test_second_administrator_approval_executes` (key row exists, `key_hash` length 32, `owner_user_id` is the maker, audit row `api_key.created`), `test_maker_cannot_approve_own_key` (403 `self_approval_forbidden`), `test_approved_payload_is_what_executes` (a changed payload yields 409 `approval_payload_mismatch` and no key), `test_lifetime_capped_at_180_days_when_approved_late` (approve 7 days after proposing a 178-day key: `expires_at <= created_at + 180 days`), `test_secret_shown_once_to_maker` (`getApproval` by the maker returns `result.secret` once; the second read has no `result`; another user never sees it; after 15 minutes gone), `test_stored_value_is_hmac_with_pepper` (recompute from the revealed secret with the test pepper equals `key_hash`; TC-FR-18-2, TC-FR-18-6), `test_list_and_get_never_expose_secret_or_hash` (`ApiKey` schema only), `test_reveal_store_down_aborts_execution_and_keeps_approval_pending`. `tests/security/test_api_key_secret_hygiene.py::test_secret_never_persisted_or_logged`: after create, approve and reveal, scan captured logs, `audit_log` details, `outbox.payload`, `approvals.payload` and the whole database dump text for the secret and for the HMAC hex: no match.
- [ ] **Step 2: run.** `uv run pytest tests/integration/identity/test_api_key_creation.py -q`. Expected: 404 or 405 on `POST /api/v1/api-keys`.
- [ ] **Step 3: implement.** `ApiKeyViewSet.create` (`@operation("createApiKey")`, `@idempotent(scope="POST /api-keys", window=24h)`): `ApiKeyCreateSerializer` then `api_key_rules.validate_create`, then `ApprovalService.propose(action="apikey.create", payload={name, kind, scopes, site_ids, allowed_cidrs, rate_limit_tier, expires_at, reason}, subject=Reference("api_key", None), ...)` and `AuditLog.record("api_key.proposed")`. `ApiKeyCreateExecutor.execute`: re-validate with the current clock; `expires_at = min(payload.expires_at, now + 180 days)`; generate the key; seal and store `RevealStore.put(approval.id, raw_key, ttl=900)` first (failure raises `ServiceUnavailable` so the transaction rolls back); insert the row with raw SQL; for a gateway key link `gateways.api_key_id` when exactly one gateway of a bound site has `name == key name` and no key (otherwise leave the link null; telemetry resolves by name and site); audit `api_key.created`. `RevealStore` encrypts with Fernet using `HUB_REVEAL_KEY`, key `apikey:reveal:{approval_id}`, `GETDEL` on read. `getApproval` calls `ApiKeyService.reveal` only for approvals of kind `api_key_creation` whose viewer is the maker and status is `executed`, and adds `result: {"api_key_id", "secret"}`. `listApiKeys` and `getApiKey` read columns allowed by the `hub_api` grant (never `key_hash`).
- [ ] **Step 4: run until green.** `uv run pytest tests/integration/identity/test_api_key_creation.py tests/security/test_api_key_secret_hygiene.py tests/contract -k "api_key or apikey" -q`.
- [ ] **Step 5: commit.** `feat(identity): api key creation through approval with one-time secret reveal`

### Task 15.6: Revocation, narrowing updates, rotation and expiry

- [ ] **Step 1: failing tests.** `tests/integration/identity/test_api_key_revocation.py`: `test_revoked_key_rejected_within_30_seconds` (warm the cache with one call, revoke, with Redis available the next call is 401 at once; with Redis stopped, still 200 at 29 s on the fake clock and 401 at 31 s; TC-FR-18-1), `test_revoke_needs_step_up_and_is_audited`, `test_revoke_twice_is_idempotent_204`, `test_revoke_emits_api_key_revoked_event`, `test_unknown_key_is_404`, `test_expiry_job_marks_expired_and_calls_fail_with_401`, `test_key_at_exactly_expires_at_is_refused`. `tests/integration/identity/test_api_key_rotation.py`: `test_rotation_overlap_24h` (both keys 200 at 23 hours; at 24 hours plus 1 second only the new one), `test_rotation_copies_scopes_sites_cidrs_and_tier`, `test_rotation_needs_approval_and_secret_shown_once`, `test_second_rotation_while_pending_is_409`, `test_rotating_revoked_or_expired_key_is_409`, `test_revoking_old_key_ends_overlap_early`, `test_gateway_link_moves_to_successor`. `tests/integration/identity/test_api_key_update.py`: `test_patch_narrows_cidrs_only`, `test_patch_cannot_raise_tier`, `test_patch_cannot_touch_scopes_sites_or_expiry` (422 for unknown fields), `test_gateway_cidr_list_never_empty`.
- [ ] **Step 2: run.** `uv run pytest tests/integration/identity/test_api_key_revocation.py -q`. Expected: 405 on `DELETE /api/v1/api-keys/{id}`.
- [ ] **Step 3: implement.** `revoke`: inside `with uow:` `UPDATE api_keys SET status='revoked', revoked_at=now() WHERE id=%s AND status='active'` (zero rows on an already revoked key is still 204), audit `api_key.revoked`, stage the event, `on_commit` set Redis `apikey:revoked:{key_id}` with 120 s expiry and `PUBLISH apikey:revoked` so every pod drops its cache entry; the cache TTL of 30 s is the bound when Redis is down. `propose_rotate`: 409 if the key is not `active` or if a pending approval has `payload->>'rotate_of' = id`; payload carries `rotate_of` and copied attributes. The executor, when `rotate_of` is present, creates the successor with expiry `now + 180 days`, sets the old key `expires_at = LEAST(expires_at, now + interval '24 hours')`, moves `gateways.api_key_id`, audits `api_key.rotated`. `update` applies the narrowing rules from `api_key_rules`, audits `api_key.updated`, and invalidates the cache like a revoke. Register `identity.expire_api_keys` hourly through `JobRegistry.register_cron` calling `hub.expire_api_keys()`.
- [ ] **Step 4: run until green.** `uv run pytest tests/integration/identity -k "api_key" -q`.
- [ ] **Step 5: commit.** `feat(identity): api key revocation, narrowing update, rotation overlap and expiry`

### Task 15.7: End to end and verification script

- [ ] **Step 1: failing test.** `tests/integration/identity/test_api_key_end_to_end.py::test_create_scoped_gateway_key_and_use_it_for_telemetry` (TC-FR-18-4): an administrator proposes a gateway key (`telemetry:write`, one site, CIDR `127.0.0.0/8`), a second administrator approves, the maker reads the secret from `getApproval`, the test posts the example batch to `/api/v1/telemetry/batches` with that key and gets 202 with `accepted = 2`, then revokes and sees 401 within 30 seconds. Also `test_integrator_key_calls_scoped_endpoint_and_not_others` (`tickets:read` lists tickets; `topups:write` missing gives 403 on `createTopUp`), `test_no_sgh_key_in_openapi_examples_or_repository` (regex `sgh_(dev|stg|prod)_[0-9a-f]{12}_[a-z2-7]{52}` finds nothing under `api/` and `docs/`).
- [ ] **Step 2: run.** `uv run pytest tests/integration/identity/test_api_key_end_to_end.py -q`. Expected: fails only if an earlier task left a gap; fix the gap in the owning module, never the test.
- [ ] **Step 3: implement.** Wire `tools/verify/slice_15.sh` and the Makefile target, add the checks of the Verification section, and update `docs/plan/progress.md` with the demo output.
- [ ] **Step 4: run until green.** `make verify SLICE=15`.
- [ ] **Step 5: commit.** `test(identity): api key end-to-end flow and slice 15 verification`

## Verification

`make verify SLICE=15` runs from the repository root:

1. `make lint`: clean; `grep -rn "verify_api_key(" backend/src` returns only the definition comment (old function no longer called).
2. `cd backend && uv run pytest tests/unit/identity -q`: all pass.
3. `cd backend && uv run pytest tests/integration/identity -k "api_key or verify_api_key" -q`: all pass.
4. `cd backend && uv run pytest tests/security/test_api_key_secret_hygiene.py -q`: pass.
5. `cd backend && uv run pytest tests/integration/telemetry/test_ingest_api.py tests/integration/meters -q`: slices 06 and 08 stay green with the new authenticator.
6. `cd backend && uv run pytest tests/contract -k "api_key or apikey or telemetry" -q`: schemathesis conformance for the key operations.
7. `make contracts`: alignment, permissions equivalence, schema and traceability checks green.

## Demo script

1. `make up && make seed`. Log in as administrator A with step-up.
2. `POST /api/v1/api-keys` with the example body of `createApiKey`. Observe 202 and an approval in `GET /api/v1/approvals`; `select count(*) from hub.api_keys where name = 'Meter gateway Kasenga'` is 0.
3. Log in as administrator B, step up, approve. Observe the approval `executed` and one new `api_keys` row with a 32-byte `key_hash`.
4. As A call `GET /api/v1/approvals/{id}`. Observe `result.secret` once; call again and observe it is gone.
5. Post a telemetry batch with the key from the allowed address; observe 202. Post from another address (`--interface`) and observe 403.
6. Call `POST /api/v1/gateway/heartbeat` with the key and a ticket route needing `tickets:read`; observe 200 and 403 `insufficient_scope`.
7. Hammer the key with `hey -n 400 -c 20` and observe 429 with `Retry-After`, then 200 after the window.
8. `DELETE /api/v1/api-keys/{id}` and observe the next call returns 401 within 30 seconds.
9. Rotate a key; observe both keys work, then (with `make clock-skip HOURS=25`) only the new one.

## Done criteria

Requirements covered: FR-18. Test cases implemented and passing: TC-FR-18-1, TC-FR-18-2, TC-FR-18-3, TC-FR-18-4, TC-FR-18-5, TC-FR-18-6.

## Plan notes for the tech lead

1. **Secret delivery gap.** `api/openapi.yaml` defines `ApiKeyCreated` but no operation returns it; `createApiKey` returns an `Approval` and `approveApproval` returns the approver an `Approval`. The plan adds an optional `result` object to `getApproval` for the maker, which JSON Schema allows because `Approval` does not forbid extra properties. If the tech lead prefers an explicit schema, a change request to the OpenAPI file is needed.
2. **CIDR failure status.** PRD AC-FR-18-5 says outside the range is 403; frozen `hub.verify_api_key` returns no row, which would be 401. A new function `verify_api_key_v2` (migration `0150`) distinguishes the two, and only after the hash matched. `db/schema.sql` is not edited; the old function is left in place and unused.
3. **Revocation bound.** PRD AC-FR-18-4 says 60 seconds; `docs/05` section 9 and OpenAPI say 30 seconds. Followed 30 (satisfies both).
4. **Approval action names.** `db/schema.sql` allows `apikey.create`; `docs/03` section 2.14 says `api_key.create` and OpenAPI says `api_key_creation`. Followed the schema for the stored action and OpenAPI for the API `kind`.
5. **Gateway to key link.** `ApiKeyCreate` has no gateway field, so `gateways.api_key_id` cannot be set reliably; the plan links by name equality when unambiguous and slice 08 resolves gateways by `(name, bound site)`. One key per physical gateway is therefore only weakly enforced.
6. **Scopes outside `permissions.yaml`.** The file lists no scopes; required scopes come from `x-api-key-scopes` in OpenAPI, which `docs/03` 7.7 item 4 also expects the permission class to read. Scope names differ across documents (`tokens:ack` in the PRD, `tokens:acknowledge` in OpenAPI and schema); followed the schema.
7. **Rate limit source.** Key operations carry the `staff` or `integrator` tier in `x-rate-limit-tier`, but the tier table keys `staff` by user. For key principals the per-key column value overrides the operation tier. `docs/09` lists key lifetime "12 months" for integrators; D-19 and the CHECK say 180 days, followed.
8. **Integrator site binding.** `docs/07` 4.5 says integrator keys are bound to sites or customers; the schema and OpenAPI make `site_ids` optional for them. Followed the contract (no sites means all sites, via an `all_sites` attribute).
