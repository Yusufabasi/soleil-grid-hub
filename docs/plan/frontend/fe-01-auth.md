# Frontend slice 01: Authentication, sessions, step-up, role-aware navigation

- **Goal:** customers sign in with a phone one-time code and staff with password plus second factor; the access token lives in memory and is recovered by the refresh cookie; step-up prompts replay guarded actions; route guards and the staff and portal navigation follow `api/permissions.yaml`; staff manage their own security settings.
- **Depends on:** backend slice 1 (identity, RBAC, approvals); frontend slice fe-00.
- **Requirements served:** FR-15, FR-16, NFR-06, NFR-10.
- **Size:** L, 8 days.
- **Spikes or open items:** company identity provider sign-in (D-47): `api/openapi.yaml` has no operation that starts the OIDC redirect, so the button is built behind `NEXT_PUBLIC_OIDC_ENABLED` (default `false`, hidden) and the redirect target is `/api/v1/auth/oidc/start` **[open: confirm path with the backend author]**. Default: local accounts only.

## Read first

- `docs/08-frontend-ux.md#3.12 Step-up prompt (D-16)`
- `docs/08-frontend-ux.md#3.13 Security settings: WebAuthn, TOTP, backup codes, reset (/me/security)`
- `docs/08-frontend-ux.md#5.7 Routing, error boundaries and role guards`
- `docs/08-frontend-ux.md#5.9 Access token in memory and recovery after reload (D-17, D-44)`
- `docs/08-frontend-ux.md#2.3 Staff app sitemap and navigation per role`
- `docs/08-frontend-ux.md#7.1 Checklist mapped to success criteria` (SC 2.2.1, 3.3.8)
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)` (Error boundaries, Client state paragraphs)
- `docs/00-shared-context.md` section 7: D-16, D-17, D-21, D-47
- OperationIds: `getProofChallenge`, `requestCustomerOtp`, `verifyCustomerOtp`, `staffLogin`, `staffMfaVerify`, `startMfaEnrollment`, `confirmMfaEnrollment`, `webauthnAssertionOptions`, `verifyWebauthnAssertion`, `webauthnRegistrationOptions`, `registerWebauthnCredential`, `regenerateRecoveryCodes`, `stepUp`, `refreshToken`, `logout`, `getMe`, `updateMe`.

## Global constraints

- Server Actions are banned; all writes are client calls through the generated client.
- No secret in `NEXT_PUBLIC_*` (`NEXT_PUBLIC_OIDC_ENABLED` is a boolean flag, not a secret).
- Image optimiser off; CSP nonce on every page (D-44, D-48).
- Access token only in memory in `src/api/auth-store.ts`; the refresh token never reaches JavaScript (HttpOnly cookie); nothing sensitive in `localStorage` or `sessionStorage`.
- Cookie endpoints `refreshToken` and `logout` are the only calls with `credentials: 'include'`, and both send `X-Hub-CSRF: 1` (D-46); every other call sends the bearer header only.
- Budgets: portal first load at most 130 KB, staff at most 250 KB; WebAuthn and QR code are dynamically imported.
- next-intl `/fr` and `/en`; French written first; no hard-coded text.
- WCAG 2.2 AA: OTP and TOTP fields allow paste and use `autocomplete="one-time-code"` (SC 3.3.8); error summary takes focus after a failed submit.
- Generated client only; no handwritten API types.
- Idle and absolute session limits are enforced by the API; the UI only warns (staff idle 30 min and absolute 8 h; customer idle 5 min and absolute 12 h, D-17).
- Navigation and guards derive from `permissions.generated.ts`; the administrator never sees tickets, money, customer content, ask-the-data or the dashboard.
- Do not edit frozen files.

## Review focus

1. Two tabs refresh at once and trip refresh-token reuse detection, signing the user out: `e2e/session.spec.ts` "two tabs reload without being signed out" (Task FE-01.2).
2. A step-up prompt loses the user's work or replays a money action twice: `step-up.test.tsx` "replays the original request exactly once" (Task FE-01.4).
3. The administrator sees a nav entry or can open a URL for an operation the role lacks: `nav.test.tsx` and `e2e/guards.spec.ts` (Task FE-01.5).
4. The OTP request leaks whether a phone exists, or the proof-of-work blocks a low-end phone: `customer-login.test.tsx` "always shows the same confirmation" (Task FE-01.3).
5. Idle warning appears too late for a screen reader user, so the session ends silently: `idle-warning.test.tsx` (Task FE-01.2).

## Files

- `frontend/src/api/auth-store.ts` (modify from fe-00): session state machine, token holder, `BroadcastChannel('hub-auth')`.
- `frontend/src/features/auth/session.ts`: `recoverSession()`, `scheduleRefresh()`, `signOut()`, cross-tab lock.
- `frontend/src/features/auth/hooks/useSession.ts`, `usePermission.ts` (re-exported in `src/shared/hooks/usePermission.ts`), `useIdleWarning.ts`.
- `frontend/src/features/auth/components/ClientAuthGate.tsx`, `RequireAuth.tsx`, `RequireRole.tsx`, `RequirePermission.tsx`, `IdleWarningDialog.tsx`.
- `frontend/src/features/auth/components/CustomerLoginForm.tsx`, `StaffLoginForm.tsx`, `MfaStep.tsx`, `MfaEnrollment.tsx`, `ProofOfWork.ts` (worker wrapper).
- `frontend/src/shared/hooks/useStepUp.ts`, `frontend/src/features/auth/components/StepUpDialog.tsx`.
- `frontend/src/features/auth/components/SecuritySettings.tsx`, `WebAuthnRegister.tsx`, `BackupCodes.tsx`.
- `frontend/src/features/auth/nav.ts`: `STAFF_NAV`, `PORTAL_NAV` (operationId per item).
- `frontend/src/features/auth/components/StaffShell.tsx`, `PortalShell.tsx` (nav rendering).
- `frontend/src/app/[locale]/(public)/login/page.tsx` (customer), `login/code/page.tsx`, `staff-login/page.tsx` (server shells with client form islands).
- `frontend/src/app/[locale]/(portal)/portal/layout.tsx`, `(staff)/staff/layout.tsx` (modify: mount `ClientAuthGate` and the shells).
- `frontend/src/app/[locale]/(staff)/staff/me/security/page.tsx`, `(portal)/portal/profile/page.tsx` (language preference via `updateMe`).
- `frontend/src/i18n/messages/{fr,en}/auth.json`.
- Tests: `frontend/src/features/auth/*.test.tsx`, `frontend/e2e/session.spec.ts`, `guards.spec.ts`, `staff-login.spec.ts`.

## Interfaces

**Routes:** `/login`, `/login/code` (customer); `/staff-login`; `/portal/profile`; `/staff/me/security`. Authenticated areas redirect to `/login?next=...` (customer) or `/staff-login?next=...` (staff); `next` accepts only a relative path starting with `/` and not `//`.

**Message namespace `auth` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `auth.customer.title` | Connexion à votre espace client | Sign in to your customer area |
| `auth.customer.phone` | Numéro de téléphone | Phone number |
| `auth.customer.sendCode` | Recevoir un code | Send me a code |
| `auth.customer.sent` | Si ce numéro est enregistré, un code vient d'être envoyé par SMS. | If this number is registered, a code has just been sent by SMS. |
| `auth.customer.code` | Code à 6 chiffres | 6-digit code |
| `auth.customer.resendIn` | Nouveau code possible dans {seconds} s | You can ask for a new code in {seconds} s |
| `auth.customer.invalidCode` | Ce code n'est pas valide. Vérifiez-le et réessayez. | This code is not valid. Check it and try again. |
| `auth.staff.title` | Connexion du personnel | Staff sign-in |
| `auth.staff.email` | Adresse e-mail professionnelle | Work email address |
| `auth.staff.password` | Mot de passe | Password |
| `auth.staff.badCredentials` | Identifiants incorrects. | Incorrect credentials. |
| `auth.mfa.title` | Deuxième vérification | Second verification |
| `auth.mfa.totp` | Code de l'application d'authentification | Authenticator app code |
| `auth.mfa.webauthn` | Utiliser une clé de sécurité | Use a security key |
| `auth.mfa.recovery` | Utiliser un code de secours | Use a backup code |
| `auth.stepUp.title` | Confirmez que c'est bien vous | Confirm it is you |
| `auth.stepUp.protects` | Pour effectuer cette action : {action} | To perform this action: {action} |
| `auth.idle.title` | Votre session va expirer | Your session is about to expire |
| `auth.idle.stay` | Rester connecté | Stay signed in |
| `auth.security.backupCodes` | Codes de secours (affichés une seule fois) | Backup codes (shown only once) |
| `auth.noAccess.title` | Accès non autorisé | You do not have access |
| `auth.noAccess.body` | Votre rôle ne permet pas d'ouvrir cette page. | Your role does not allow this page. |

**Hooks and functions:**
`useSession(): {status:'recovering'|'signedIn'|'signedOut'; principal?: Principal}`; `usePermission(operationId: OperationId): boolean`; `useStepUp(): {withStepUp<T>(action: string, run: () => Promise<T>): Promise<T>}` (opens `StepUpDialog` when `run` throws an `ApiError` with `code === 'step_up_required'`, then replays `run` once after `stepUp` succeeds); `recoverSession(): Promise<void>` single-flight using `navigator.locks.request('hub-refresh', ...)`; `signOut(reason?: 'idle'|'reuse'|'user')`.

**Nav tables (derived, do not hand-edit lists elsewhere):** each entry `{id, href, labelKey, operationId}`. Staff entries: `dashboard`→`getDashboardSummary`; `customers`→`listCustomers`; `sites`→`listSites`; `meters`→`listMeters`; `ledger`→`listLedgerAccounts`; `tickets`→`listTickets` (staff roles that also pass `listIncidents` show Incidents); `ask`→`askTheData`; `reconciliation`→`listReconBatches`; `approvals`→`listApprovals`; `kb`→`listKbArticles`; `audit`→`listAuditLog`; `alerts`→`listAlerts`; `notifications`→`listNotifications`; `ai-spend`→`getAiUsage`; `admin-users`→`listUsers`; `admin-keys`→`listApiKeys`; `privacy`→`listErasureRequests` and visible only with attribute `dp_admin`. Portal entries: `home`, `topup`→`createTopUp`, `history`→`listTopUps`, `support`→`listTickets`, `profile`→`getMe`.

**Operations consumed:** the 17 operationIds under Read first.

## Tasks

### Task FE-01.1: Session store, recovery and cross-tab refresh

- [ ] **Step 1: failing test** `frontend/src/features/auth/session.test.ts` (MSW):
  ```ts
  test('recovers session from cookie on load', async () => {
    server.use(http.post('*/api/v1/auth/refresh', ({request}) => {
      expect(request.headers.get('x-hub-csrf')).toBe('1');
      return HttpResponse.json({data:{access_token:'a1',token_type:'Bearer',expires_in:600,principal:P}});
    }));
    await recoverSession(); expect(getToken()).toBe('a1'); expect(getSessionStatus()).toBe('signedIn');
  });
  test('401 on recovery means signedOut', ...);
  test('single flight: two concurrent recoveries make one request', async () => {
    await Promise.all([recoverSession(), recoverSession()]); expect(refreshCalls).toBe(1);
  });
  test('refresh_token_reused clears token and broadcasts sign-out', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- src/features/auth/session.test.ts`. Expected: fails (module missing).
- [ ] **Step 3: implement** `session.ts`: `recoverSession` posts `refreshToken` with body `{}` (the operation requires a body), `credentials: 'include'`, header `X-Hub-CSRF: 1`; success sets token and principal and schedules a refresh 60 s before expiry only while the user was active in the last 5 minutes; failure with 401 sets `signedOut`; `refresh_token_reused` additionally clears the query cache, IndexedDB persistence keys (`hub-query-cache`) and posts `{type:'signout',reason:'reuse'}` on `BroadcastChannel('hub-auth')`. A module-level promise gives single-flight; `navigator.locks.request('hub-refresh', ...)` serialises across tabs; the refreshing tab shares the new token over the channel (memory only). `signOut` calls `logout` with the same cookie rules, then clears everything. States: recovering (skeleton), signedIn, signedOut, error (network failure shows retry and keeps `recovering`).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): in-memory session with cookie recovery and cross-tab lock`.

### Task FE-01.2: Auth gate, guards, idle warning

- [ ] **Step 1:** `ClientAuthGate.test.tsx`, `idle-warning.test.tsx`, `e2e/session.spec.ts`:
  ```ts
  test('shows area skeleton while recovering, then page', ...);
  test('signedOut redirects to /fr/login?next=%2Ffr%2Fportal%2Fhistory', ...);
  test('RequireRole shows no-access page (not a redirect loop)', () => {
    render(<RequireRole roles={['finance_officer']}>x</RequireRole>, {principal: support});
    expect(screen.getByRole('heading',{name:'Accès non autorisé'})).toBeVisible();
  });
  test('idle warning appears 2 min before staff idle limit and announces', async () => {
    vi.useFakeTimers(); renderStaff(); vi.advanceTimersByTime(28*60_000);
    expect(screen.getByRole('dialog',{name:'Votre session va expirer'})).toBeVisible();
    expect(liveRegion()).toHaveTextContent('Votre session va expirer');
  });
  ```
  Playwright: `test('two tabs reload without being signed out')` opens two pages with the same cookie, reloads both, asserts both show the portal home and the mock records at most one effective rotation per family.
- [ ] **Step 2:** run `npm test -- src/features/auth` and `npm run e2e -- session.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** `ClientAuthGate({area:'portal'|'staff', children})`: on mount call `recoverSession`; render `Skeleton` while `recovering`; `signedOut` triggers `redirect` with `next`; wrong principal type for the area (customer in staff area or the reverse) signs out with a notice. Mount `QueryClientProvider` here (one `QueryClient` per tab inside `useState`). `RequireRole`, `RequirePermission(operationId)` render the no-access page with `<h1>` focused and a link home. `useIdleWarning({limitMinutes, warnMinutesBefore})`: customer limit 5 with warning 1 minute before; staff limit 30 with warning 2 minutes before; activity = pointer, key, focus events; the dialog uses `Dialog` plus a polite announcement; "Rester connecté" calls `refreshToken` once. Absolute limit: 5 minutes before, show a notice and persist unsent form drafts to `sessionStorage` key `hub-draft:<route>` (non-sensitive text only, wiped on sign-out).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): auth gate, role guards and idle warning`.

### Task FE-01.3: Customer login (phone one-time code)

- [ ] **Step 1:** `CustomerLoginForm.test.tsx`:
  ```ts
  test('same confirmation for known and unknown numbers', async () => {
    mockOtp(202); await submitPhone('+243810000111'); expect(screen.getByRole('status')).toHaveTextContent('Si ce numéro est enregistré');
    mockOtp(202); await submitPhone('+243999999999'); expect(screen.getByRole('status')).toHaveTextContent('Si ce numéro est enregistré');
  });
  test('proof challenge is solved before requesting', ...);       // getProofChallenge then requestCustomerOtp with proof_token
  test('code field allows paste and has one-time-code autocomplete', () => expect(field).toHaveAttribute('autocomplete','one-time-code'));
  test('five wrong codes show restart message, keeps phone', ...);
  test('success lands on next path', ...);
  ```
- [ ] **Step 2:** `npm test -- CustomerLoginForm`. Expected: fail.
- [ ] **Step 3: implement.** Step 1 form (`react-hook-form`, zod: E.164 pattern `^\+[1-9][0-9]{7,14}$`, default prefix `+243` hint, `inputmode="tel"`, `autocomplete="tel"`): call `getProofChallenge`, solve in `ProofOfWork.ts` inside a Web Worker (spinner text "Vérification en cours", cancel button, timeout 10 s with retry), then `requestCustomerOtp({phone_e164, proof_token, locale})`; always show the neutral `auth.customer.sent`; start the `resend_after` countdown (announced at start and at zero only). Step 2: 6-digit field, `verifyCustomerOtp({challenge_id, code})` with `X-Token-Transport` omitted (cookie flow); on success `setToken(principal...)` and navigate to `next` or `/portal`. Errors: 422 `proof_required` restarts the challenge; 429 shows `errors.rate_limited` with `retry_after_seconds`; invalid code shows `auth.customer.invalidCode`; `challenge` dead (5 failures) returns to step 1 with the phone kept (SC 3.3.7). Dormancy re-verification needs no special UI. States: loading (button `aria-disabled`), error summary, offline (button disabled with explanation).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): customer phone one-time-code login`.

### Task FE-01.4: Step-up prompt and approval-outcome helper

- [ ] **Step 1:** `step-up.test.tsx`:
  ```ts
  test('replays the original request exactly once after step-up', async () => {
    let calls = 0; server.use(http.post('*/api/v1/ledger/adjustments', () => (++calls===1 ? stepUpRequired() : HttpResponse.json({data:APPROVAL},{status:202}))),
      http.post('*/api/v1/auth/step-up', () => HttpResponse.json({data:PAIR})));
    const res = await user.run(() => withStepUp('Proposer un ajustement', () => api.POST('/ledger/adjustments', {body})));
    await user.type(screen.getByLabelText(/Code/),'123456'); await user.click(screen.getByRole('button',{name:'Confirmer'}));
    await waitFor(()=>expect(calls).toBe(2));
  });
  test('three failures sign out', ...);
  test('webauthn offered first when enrolled; totp fallback absent for finance_officer', ...);
  test('202 with Approval shows "soumis pour approbation" with link', ...);
  ```
- [ ] **Step 2:** `npm test -- step-up`. Expected: fail.
- [ ] **Step 3: implement** `useStepUp` and `StepUpDialog` per `docs/08` 3.12: the `ApiError.stepUp.methods` and the principal's `acr` choose the order (WebAuthn first when listed; TOTP only if listed and the role is not `finance_officer` or `administrator`); WebAuthn flow uses `webauthnAssertionOptions` then browser `navigator.credentials.get` then `stepUp({method:'webauthn', assertion})`; TOTP uses `stepUp({method:'totp', totp_code})`; success calls `setToken` with the new pair and replays once; the dialog states `auth.stepUp.protects` with the action label; three failures call `signOut('user')`. Export `handleApprovalResult(res, t)`: a `202` response whose data has `kind` and `status: 'pending'` shows a toast "Soumis pour approbation (n° {id})" (fr) / "Submitted for approval (no. {id})" (en) with a link to `/staff/approvals/{id}`; keys `auth.approval.submitted` in `auth.json`. States: loading, error, offline (dialog disabled).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): step-up dialog with single replay and approval outcome helper`.

### Task FE-01.5: Staff login, MFA, navigation and guards from permissions

- [ ] **Step 1:** `nav.test.tsx`, `StaffLoginForm.test.tsx`, `e2e/guards.spec.ts`, `e2e/staff-login.spec.ts`:
  ```ts
  test.each([
    ['administrator', ['audit','admin-users','admin-keys','kb','approvals']],
    ['support_agent', ['tickets','ask','kb','approvals','dashboard']],
    ['finance_officer', ['ledger','reconciliation','ask','audit','approvals','ai-spend','dashboard']],
    ['operations_engineer', ['alerts','dashboard','ask','audit','approvals','notifications','ai-spend']],
  ])('%s sees exactly its entries', (role, ids) => expect(visibleNav(role).map(n=>n.id).sort()).toEqual(expect.arrayContaining(ids.sort())));
  test('administrator has no money, ticket, ask or dashboard entry', () => {
    const ids = visibleNav('administrator').map(n=>n.id);
    for (const bad of ['tickets','ask','dashboard','ledger','reconciliation','customers']) expect(ids).not.toContain(bad);
  });
  test('privacy entry only with dp_admin', ...);
  test('staff login: password then totp; webauthn only option shown for administrator once enrolled', ...);
  ```
  Playwright: administrator opens `/fr/staff/tickets` and sees the no-access page; support agent opening `/fr/staff/admin/users` sees the same.
- [ ] **Step 2:** `npm run gen:permissions && npm test -- nav StaffLoginForm && npm run e2e -- guards.spec.ts`. Expected: fail.
- [ ] **Step 3: implement.** `visibleNav(principal)` filters `STAFF_NAV` by `can(principal, entry.operationId)` and by attributes. Each staff `page.tsx` is wrapped by `RequirePermission` with the operationId of its list operation (declared in `routes.ts`, the one table that also generates the nav). `StaffLoginForm`: `staffLogin` returns `MfaChallenge`; if `enrollment_required` show `MfaEnrollment` (`startMfaEnrollment` then QR from `otpauth_uri` using a dynamically imported QR library, text key shown as alternative, `confirmMfaEnrollment`, then show `recovery_codes` once with acknowledgement checkbox); else `MfaStep` offering only `methods` returned, each calling `staffMfaVerify` (`totp_code` or `recovery_code`) or `webauthnAssertionOptions` plus `verifyWebauthnAssertion`. Wrong password and unknown account show the same `auth.staff.badCredentials`. If `NEXT_PUBLIC_OIDC_ENABLED === 'true'` show a link "Se connecter avec le compte de l'entreprise" to `/api/v1/auth/oidc/start`; otherwise nothing. `StaffShell` renders `<nav aria-label>` from `visibleNav`, language switcher, user menu with sign-out; `PortalShell` renders the 5-item bottom tab bar (48 px targets, icon plus label).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): staff login with MFA and permission-driven navigation`.

### Task FE-01.6: Security settings (WebAuthn, backup codes) and profile language

- [ ] **Step 1:** `SecuritySettings.test.tsx`:
  ```ts
  test('registers a security key', async () => { /* webauthnRegistrationOptions -> navigator.credentials.create mock -> registerWebauthnCredential */ });
  test('regenerating backup codes needs step-up and shows codes once', ...);
  test('company-login users see managed notice', ...);
  test('language change calls updateMe and switches locale route', ...);
  ```
- [ ] **Step 2:** `npm test -- SecuritySettings`. Expected: fail.
- [ ] **Step 3: implement** `/staff/me/security` per `docs/08` 3.13: security key registration (name field, `webauthnRegistrationOptions`, browser create, `registerWebauthnCredential`), backup-code regeneration through `useStepUp` and `regenerateRecoveryCodes`, banner "Required before go-live" for `finance_officer` and `administrator` without a WebAuthn credential (derived from `acr` and principal fields; if the principal has no field, show the banner based on `acr !== 'phr'`), sessions section omitted (no operation exists). `/portal/profile`: radio group fr/en calling `updateMe({locale})` then navigating to the same page in the other locale; shows `getMe` data read only.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): security settings and language preference`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes; new suites under `src/features/auth` all green.
3. `npm run gen:permissions && git diff --exit-code src/shared/lib/permissions.generated.ts`.
4. `npm run build && npm run size` exit 0 (portal at most 130 KB, staff at most 250 KB).
5. `npm run e2e -- session.spec.ts guards.spec.ts staff-login.spec.ts` pass in mock mode (`E2E_MODE=mock`); against the stack (`make up`, `E2E_MODE=stack`) the customer and staff login specs pass when backend slice 1 is ready.
6. `npm run e2e:a11y -- --grep "login|security"` zero serious or critical violations in `/fr` and `/en`.

## Demo script

1. Open `/fr/login`. FR: enter `+243810000111`, press "Recevoir un code": the message "Si ce numéro est enregistré, un code vient d'être envoyé par SMS." appears. EN: `/en/login`, "Send me a code", "If this number is registered...".
2. Enter the stub code: you land on `/fr/portal`; reload the page: you stay signed in (session recovered), the network tab shows one `auth/refresh` call.
3. Open a second tab on the portal and reload both: neither signs out.
4. Open `/fr/staff-login` as the administrator, password then security key. FR: "Deuxième vérification". EN: "Second verification". The navigation shows Audit, Utilisateurs, Clés API, Base de connaissances, Approbations only.
5. Try `/fr/staff/tickets` as the administrator: "Accès non autorisé" / "You do not have access".
6. As a finance officer, start any guarded action: the dialog "Confirmez que c'est bien vous" / "Confirm it is you" appears and the action runs once after confirmation.
7. Wait for the idle limit with timers shortened in the stack: "Votre session va expirer" appears and is announced.

## Done criteria

- Requirements: FR-15 (login, MFA, step-up, guards), FR-16 (language switch persisted), NFR-06, NFR-10.
- Test cases from `docs/10-test-strategy.md` section 8 (UI contribution): TC-FR-15-9 (step-up older than 5 minutes), TC-FR-15-10 (finance and administrator need WebAuthn; TOTP fallback absent), TC-FR-15-12 (cookie never visible to scripts; verified by `document.cookie` check in e2e), TC-FR-15-15 (administrator refused money, ticket, customer content and ask-the-data), TC-FR-15-16 (OTP always 202 behaviour shown), TC-FR-15-17 (CSRF header on the two cookie calls), TC-FR-16-3.

## Plan notes for the tech lead

- **Token pair responses:** `refreshToken`, `stepUp` and `verifyCustomerOtp` return `TokenPair` with `principal` inside `data`; the refresh request body is marked required although browsers send nothing. The client sends `{}`.
- **OIDC start:** D-47 says `hub-web` starts the redirect, but no operationId or path exists in `api/openapi.yaml`. Built behind a flag (see open item).
- **Nav versus docs/08:** `docs/08` 2.3 says the administrator may drive KB publishing and AI spend. `api/permissions.yaml` gives `getAiUsage` and `listAiRuns` only to `operations_engineer` and `finance_officer`, and KB create and publish to `support_agent` and `administrator`. Followed `permissions.yaml`; `docs/08` 2.3 and section 4 (cost "administrator only") need aligning.
- **Dashboard roles:** `getDashboardSummary` is allowed for `support_agent`, `finance_officer` and `operations_engineer` only, so the administrator has no dashboard (consistent with D-14).
- **Sessions list:** `docs/08` 3.13 mentions an active-sessions list; no operation exists, so it is omitted.
- **WebAuthn enforcement flag:** the principal has `acr` and `mfa_enrolled` but no field for enrolled WebAuthn credentials; the banner uses `acr !== 'phr'` as a proxy.
