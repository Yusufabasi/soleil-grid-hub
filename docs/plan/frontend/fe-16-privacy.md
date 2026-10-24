# Frontend slice 16: Privacy requests (data export and erasure), retention view

- **Goal:** customers request a copy of their data or the erasure of their account with a second proof of identity, see why erasure is blocked and how the 7-day cooling-off works, and can cancel; data-protection administrators (attribute `dp_admin`) work the request queue, verify, reject or propose approval; everyone allowed can read the retention table. The language never promises total deletion: ledger records are kept and pseudonymised.
- **Depends on:** backend slice 16 (privacy and lifecycle); frontend slices fe-00, fe-01 (step-up, approval helper), fe-04 (portal shell, profile), fe-15 (approvals).
- **Requirements served:** FR-20, NFR-07, NFR-10, NFR-11.
- **Size:** M, 5 days.
- **Spikes or open items:** (1) the format of `identity_proof` in `createDataExport` and `createErasureRequest` is a plain string with no stated structure; default: the customer enters their registered phone number and the 6-digit code received by SMS (obtained through `getProofChallenge`, `requestCustomerOtp`), and the UI sends the string `<challenge_id>:<code>` **[open: confirm with backend slice 16 author]**. (2) The wording of the erasure notice is pending legal review (D-28, D-35 context): default sentences in Interfaces.

## Read first

- `docs/08-frontend-ux.md#3.14 Privacy requests: export and erasure (FR-20, D-29, D-21)`
- `docs/08-frontend-ux.md#3.11 Maker-checker: pending approvals and review (D-15)`
- `docs/08-frontend-ux.md#3.12 Step-up prompt (D-16)`
- `docs/08-frontend-ux.md#9.1 Security notes` (Export and erasure UI row)
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)`
- `docs/00-shared-context.md` section 7: D-14, D-21, D-28, D-29
- OperationIds: `getProofChallenge`, `requestCustomerOtp`, `createDataExport`, `listDataExports`, `getDataExport`, `createDataExportLink`, `createErasureRequest`, `listErasureRequests`, `getErasureRequest`, `verifyErasureRequest`, `approveErasureRequest`, `rejectErasureRequest`, `cancelErasureRequest`, `listRetentionPolicies`, `getApproval`, `getMe`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page.
- Access token in memory only. Export download links are 60 seconds, fetched on demand, opened once and never stored or shown as a standing URL.
- Budgets: portal first load at most 130 KB, staff at most 250 KB; privacy pages add no heavy library.
- next-intl `/fr` and `/en`; French first; plain, non-legal sentences on the customer side.
- WCAG 2.2 AA: status as text and a progress list (ordered list with `aria-current="step"`), dates as text, blocking reasons as a list with explanations, destructive actions with confirmation dialogs.
- Generated client only; every request sends an `Idempotency-Key` if the operation declares it.
- A customer can request export and erasure only for themselves; staff never choose another customer's data in the portal; the staff path requires the `dp_admin` attribute (`x-requires-attribute`) and role `administrator`.
- Export and erasure never display personal content of the subject in the staff queue beyond a pseudonymous id and status (D-14).
- Staff approvals of erasure and export go through the approvals screen (fe-15) with step-up; this slice only proposes and links.
- The erasure approve control is disabled until `cooling_off_ends_at` has passed, with the date as text; the server enforces it too.
- Do not edit frozen files.

## Review focus

1. A customer is promised "everything deleted" or sees a blocked request as failed: `ErasureRequest.test.tsx` "completion text explains financial records are kept pseudonymised; blocked shows reasons" (Task FE-16.2).
2. Export or erasure starts without the second proof: `SecondProof.test.tsx` "request not sent until code verified; neutral message for unknown numbers" (Task FE-16.1).
3. The cooling-off date or cancel button is missing or wrong: `ErasureStatus.test.tsx` "shows end date; cancel available until completed" (Task FE-16.2).
4. A second administrator approval is skipped by showing Approve too early: `PrivacyQueue.test.tsx` "approve disabled before cooling-off end" (Task FE-16.4).
5. A download link is kept or shown permanently: `ExportReady.test.tsx` "link fetched on click, opened once, not rendered after" (Task FE-16.1).

## Files

- `frontend/src/features/privacy/keys.ts`, `schemas/request.ts`.
- `frontend/src/features/privacy/hooks/useExports.ts`, `useExport.ts` (polls while not final), `useCreateExport.ts`, `useExportLink.ts`, `useErasureRequests.ts`, `useErasureRequest.ts`, `useCreateErasure.ts`, `useCancelErasure.ts`, `useVerifyErasure.ts`, `useProposeErasureApproval.ts`, `useRejectErasure.ts`, `useRetention.ts`.
- `frontend/src/features/privacy/components/SecondProof.tsx`, `ExportRequest.tsx`, `ExportStatus.tsx`, `ErasureRequest.tsx`, `ErasureBlockers.tsx`, `ErasureStatus.tsx`, `PrivacyPage.tsx` (portal), `PrivacyQueue.tsx`, `ErasureAdminDetail.tsx`, `ExportAdmin.tsx`, `RetentionTable.tsx`.
- `frontend/src/app/[locale]/(portal)/portal/profile/privacy/page.tsx`, `profile/privacy/erasure/[id]/page.tsx`, `profile/privacy/export/[id]/page.tsx`.
- `frontend/src/app/[locale]/(staff)/staff/admin/privacy/page.tsx`, `admin/privacy/erasure/[id]/page.tsx`, `admin/privacy/retention/page.tsx`.
- `frontend/src/i18n/messages/{fr,en}/privacy.json`.
- Tests next to components; `frontend/e2e/privacy.spec.ts`.

## Interfaces

**Routes:** portal `/portal/profile/privacy`, `/portal/profile/privacy/erasure/[id]`, `/portal/profile/privacy/export/[id]`; staff `/staff/admin/privacy`, `/staff/admin/privacy/erasure/[id]`, `/staff/admin/privacy/retention` (retention is readable by administrator, finance and operations).

**Namespace `privacy` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `privacy.title` | Mes données | My data |
| `privacy.export.button` | Télécharger mes données | Download my data |
| `privacy.erasure.button` | Supprimer mon compte | Delete my account |
| `privacy.proof.title` | Confirmez votre identité | Confirm your identity |
| `privacy.proof.phone` | Votre numéro de téléphone enregistré | Your registered phone number |
| `privacy.proof.send` | Recevoir un code | Send me a code |
| `privacy.proof.sent` | Si ce numéro est enregistré, un code vient d'être envoyé par SMS. | If this number is registered, a code has just been sent by SMS. |
| `privacy.proof.code` | Code à 6 chiffres | 6-digit code |
| `privacy.export.format` | Format du fichier | File format |
| `privacy.export.scope` | Données à inclure | Data to include |
| `privacy.export.scope.profile` | Profil | Profile |
| `privacy.export.scope.meters` | Compteurs | Meters |
| `privacy.export.scope.top_ups` | Recharges | Top-ups |
| `privacy.export.scope.ledger` | Mouvements d'argent | Money movements |
| `privacy.export.scope.tickets` | Demandes d'assistance | Support requests |
| `privacy.export.scope.notifications` | Notifications | Notifications |
| `privacy.export.status.pending_approval` | En attente d'approbation | Waiting for approval |
| `privacy.export.status.queued` | En file d'attente | Queued |
| `privacy.export.status.processing` | Préparation en cours | Being prepared |
| `privacy.export.status.ready` | Prêt à télécharger | Ready to download |
| `privacy.export.status.failed` | Échec | Failed |
| `privacy.export.status.expired` | Expiré | Expired |
| `privacy.export.status.rejected` | Refusé | Declined |
| `privacy.export.download` | Télécharger (le lien est valable 60 secondes) | Download (the link is valid for 60 seconds) |
| `privacy.erasure.blockers.title` | Pourquoi nous ne pouvons pas encore supprimer votre compte | Why we cannot delete your account yet |
| `privacy.erasure.blocker.wallet_non_zero` | Il reste de l'argent dans votre portefeuille. Utilisez-le ou demandez un remboursement. | There is still money in your wallet. Use it or ask for a refund. |
| `privacy.erasure.blocker.unacknowledged_tokens` | De l'énergie est encore en cours d'envoi à votre compteur. Réessayez plus tard. | Energy is still being sent to your meter. Try again later. |
| `privacy.erasure.blocker.legal_hold` | Nous ne pouvons pas supprimer votre compte tant qu'une obligation légale s'applique. | We cannot delete your account while a legal obligation applies. |
| `privacy.erasure.blocker.open_dispute` | Une demande ou un litige est en cours. Nous ne pouvons pas supprimer votre compte pour l'instant. | A request or dispute is open. We cannot delete your account for now. |
| `privacy.erasure.coolingOff` | Nous attendons 7 jours avant de supprimer quoi que ce soit, et nous envoyons un message à votre contact actuel pour que vous puissiez annuler. | We wait 7 days before deleting anything, and we send a message to your current contact so you can cancel. |
| `privacy.erasure.untilDate` | Délai de réflexion jusqu'au {date} | Cooling-off until {date} |
| `privacy.erasure.step.pending_approval` | Demande reçue | Request received |
| `privacy.erasure.step.verified` | Identité vérifiée, délai de réflexion | Identity verified, cooling-off |
| `privacy.erasure.step.approved` | Approuvée | Approved |
| `privacy.erasure.step.processing` | En cours | In progress |
| `privacy.erasure.step.completed` | Terminée | Completed |
| `privacy.erasure.status.rejected` | Refusée | Declined |
| `privacy.erasure.status.cancelled` | Annulée | Cancelled |
| `privacy.erasure.status.blocked_by_retention` | Bloquée | Blocked |
| `privacy.erasure.cancel` | Annuler ma demande | Cancel my request |
| `privacy.erasure.completedNote` | Vos informations personnelles ont été retirées. Les enregistrements financiers sont conservés comme la loi l'exige, sans vos données personnelles. | Your personal details have been removed. Financial records are kept as the law requires, without your personal data. |
| `privacy.admin.title` | Demandes de confidentialité | Privacy requests |
| `privacy.admin.verify` | Vérifier l'identité et démarrer le délai de réflexion | Verify identity and start the cooling-off |
| `privacy.admin.approve` | Proposer l'approbation de l'effacement | Propose approval of the erasure |
| `privacy.admin.approveLocked` | Disponible après le {date} | Available after {date} |
| `privacy.admin.reject` | Refuser la demande | Decline the request |
| `privacy.admin.blocked` | Effacement bloqué : {reasons} | Erasure blocked: {reasons} |
| `privacy.admin.submitted` | Proposition n° {id} envoyée. Un deuxième approbateur doit la valider. | Proposal no. {id} sent. A second approver must confirm it. |
| `privacy.retention.title` | Durées de conservation | Retention periods |

**Hooks:** `useExport(id)` polls every 5 s until status is `ready`, `failed`, `expired` or `rejected`; `useExportLink(id)` is a mutation (not a query) calling `createDataExportLink`; `useErasureRequest(id)` polls every 30 s while not final (`completed`, `rejected`, `cancelled`).

**Operations consumed:** the 16 operationIds under Read first.

## Tasks

### Task FE-16.1: Customer export request with second proof (TC-FR-20-7 UI)

- [ ] **Step 1: failing test** `SecondProof.test.tsx`, `ExportRequest.test.tsx`, `ExportReady.test.tsx`:
  ```ts
  test('request not sent until the code is entered; neutral message for unknown numbers', async () => {
    render(<ExportRequest/>); await user.click(btn('Télécharger mes données'));
    await user.type(phone(),'+243999999999'); await user.click(btn('Recevoir un code'));
    expect(screen.getByRole('status')).toHaveTextContent('Si ce numéro est enregistré'); expect(exportCalls).toBe(0);
  });
  test('submits identity_proof built from challenge id and code, format and scope, with Idempotency-Key', ...);
  test('202 shows waiting for approval status page', ...);
  test('status page polls and shows Prêt à télécharger when ready', ...);
  test('link fetched on click, opened once, not rendered after', async () => {
    await user.click(btn('Télécharger (le lien est valable 60 secondes)')); expect(linkCalls).toBe(1); expect(window.open).toHaveBeenCalledWith(URL,'_blank','noopener,noreferrer');
    expect(screen.queryByText(URL)).toBeNull();
  });
  test('expired and failed statuses show plain text; rejected shows declined', ...);
  test('step-up replay for createDataExport', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- SecondProof ExportRequest ExportReady`. Expected: fail.
- [ ] **Step 3: implement** `SecondProof` (steps: phone field E.164, `getProofChallenge` solved with the same worker as FE-01.3, `requestCustomerOtp` with the neutral confirmation, 6-digit field with paste allowed and `autocomplete="one-time-code"`; exposes `onProof(string)` with `<challenge_id>:<code>`), `ExportRequest` (format radio json or csv, scope checkboxes with all selected by default, submit via `withStepUp` if required then `createDataExport({format, scope, identity_proof})`, expects 202 with an approval; shows `ExportStatus`), `ExportStatus` page (poll `getDataExport`; when `ready` a button that calls `createDataExportLink` and opens the returned URL in a new tab with `noopener,noreferrer`, never rendering the URL). States: loading, error with request id, offline (buttons disabled).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): customer data export with second proof and one-time link`.

### Task FE-16.2: Customer erasure request, blockers and cooling-off (TC-FR-20-5 UI)

- [ ] **Step 1:** `ErasureRequest.test.tsx`, `ErasureBlockers.test.tsx`, `ErasureStatus.test.tsx`:
  ```ts
  test('blockers listed with plain explanations and next steps', () => {
    render(<ErasureBlockers blockers={[{code:'wallet_non_zero'},{code:'unacknowledged_tokens'}]}/>);
    expect(screen.getByText(/Il reste de l'argent dans votre portefeuille/)).toBeVisible(); expect(screen.getByText(/De l'énergie est encore en cours d'envoi/)).toBeVisible();
  });
  test('legal_hold and open_dispute give no detail about the hold', () => expect(document.body.textContent).not.toMatch(/\d{3,}/));
  test('no blockers: confirmation dialog shows cooling-off notice before submit', ...);
  test('submit sends reason and identity_proof; status page shows ordered steps with current step', ...);
  test('shows cooling-off end date as text and cancel button until completed', async () => {
    expect(screen.getByText('Délai de réflexion jusqu\'au 31 oct. 2026')).toBeVisible(); expect(btn('Annuler ma demande')).toBeVisible();
  });
  test('cancel calls cancelErasureRequest after confirmation', ...);
  test('completed text says financial records are kept without personal data; never says everything is deleted', () => expect(screen.getByText(/enregistrements financiers sont conservés/)).toBeVisible());
  test('blocked_by_retention status shows blockers, not "failed"', ...);
  ```
- [ ] **Step 2:** `npm test -- ErasureRequest ErasureBlockers ErasureStatus`. Expected: fail.
- [ ] **Step 3: implement** `/portal/profile/privacy` page with the two buttons, `ErasureRequest` (explanatory page first with the sentence about records kept pseudonymised; reason textarea 3 to 1,000 characters; `SecondProof`; confirmation dialog with `privacy.erasure.coolingOff`; `createErasureRequest({reason, identity_proof})` under `withStepUp` if required), `ErasureStatus` (`getErasureRequest`; `<ol>` of steps from the status values with `aria-current="step"`, end date via `format.dateTime` from `cooling_off_ends_at`, `ErasureBlockers` when `blockers.length > 0` or status `blocked_by_retention`, cancel button via `cancelErasureRequest`, completion note when `completed`). A 409 `erasure_blocked` from any call shows the blockers from the problem or the refetched request. The customer is never told details of a legal hold.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): customer erasure request with blockers and cooling-off`.

### Task FE-16.3: Staff privacy queue, verify and reject (TC-FR-20-3 UI)

- [ ] **Step 1:** `PrivacyQueue.test.tsx`, `ErasureAdminDetail.test.tsx`:
  ```ts
  test('only administrators with dp_admin see the page; others get no-access', ...);
  test('queue lists status text, blockers count, cooling-off end, requested time; pseudonymous ids only', () => expect(document.body.textContent).not.toMatch(/\+243|@/));
  test('verify under step-up starts cooling-off; 409 erasure_blocked shows blockers text', ...);
  test('reject needs a note and calls rejectErasureRequest', ...);
  test('exports tab lists data exports with status; no customer content', ...);
  test('empty, error, offline states', ...);
  ```
- [ ] **Step 2:** `npm test -- PrivacyQueue ErasureAdminDetail`. Expected: fail.
- [ ] **Step 3: implement** `/staff/admin/privacy` with tabs "Effacements" and "Exports" (`listErasureRequests`, `listDataExports`, native tables with caption and cursor links), `ErasureAdminDetail` (`getErasureRequest`: status, blockers as text codes translated by `privacy.erasure.blocker.*`, requester and verifier as short ids, dates; `Vérifier l'identité et démarrer le délai de réflexion` under `withStepUp` calling `verifyErasureRequest`; `Refuser la demande` with a note calling `rejectErasureRequest`). Page guard `RequirePermission operationId="listErasureRequests"` which includes the `dp_admin` attribute through fe-00's generated `can()`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): staff privacy request queue with verify and reject`.

### Task FE-16.4: Propose erasure approval after cooling-off; export on behalf (TC-FR-20-4)

- [ ] **Step 1:** `ErasureAdminDetail.approve.test.tsx`, `ExportAdmin.test.tsx`, `e2e/privacy.spec.ts`:
  ```ts
  test('approve disabled before cooling-off end with date text', () => {
    render(<ErasureAdminDetail id={R.id} now="2026-10-25T00:00:00Z"/>); expect(btn('Proposer l\'approbation de l\'effacement')).toBeDisabled();
    expect(screen.getByText('Disponible après le 31 oct. 2026')).toBeVisible();
  });
  test('after cooling-off, approve proposal runs step-up then approveErasureRequest and shows submitted message with approval link', ...);
  test('requester and verifier cannot be the second approver: message from API 403 shown', ...);
  test('dp_admin creates export for a customer: customer id required, one subject per request, approval message', ...);
  test('createDataExportLink only for ready exports; link opened once', ...);
  ```
  Playwright `privacy.spec.ts`: customer requests erasure (blocked, then unblocked), staff verifies, time travel (clock) past cooling-off, staff proposes approval, second administrator approves in the approvals screen, request page shows "Terminée"; axe on every page.
- [ ] **Step 2:** `npm test -- ErasureAdminDetail ExportAdmin`; `npm run e2e -- privacy.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** the approve proposal (`useProposeErasureApproval` calls `approveErasureRequest({note, payload_hash?})` per the generated `ErasureDecision`; the response is an `Approval`, so show `privacy.admin.submitted` with a link to `/staff/approvals/{id}`), the date gate (`new Date(cooling_off_ends_at) > now` disables the button; `now` is injectable in tests), `ExportAdmin` (form with customer picker by pseudonymous id typed (no customer search is available to administrators), format, scope, `createDataExport`, link button for `ready` exports through `createDataExportLink`).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): erasure approval proposal with cooling-off gate and staff export`.

### Task FE-16.5: Retention policies view

- [ ] **Step 1:** `RetentionTable.test.tsx`:
  ```ts
  test('table lists data class and period as text; caption; available to administrator, finance, operations', ...);
  test('support has no entry; no-access page', ...);
  test('notes that retention values are pending legal sign-off when the API flags it', ...);
  test('error and offline states', ...);
  ```
- [ ] **Step 2:** `npm test -- RetentionTable`. Expected: fail.
- [ ] **Step 3: implement** `RetentionTable` using `listRetentionPolicies` (render every property from the generated `RetentionPolicy`; no editing). Page `/staff/admin/privacy/retention`; the navigation entry "Durées de conservation" appears for roles the API allows. Add a link from the customer privacy page to a plain-language retention summary only if the API provides public text (it does not; none shown).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): retention policies view`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes.
3. `npm run i18n:check` exits 0 (`privacy` equal in both locales).
4. `npm run build && npm run size` exit 0 (portal privacy routes at most 130 KB, staff at most 250 KB).
5. `npm run e2e -- privacy.spec.ts` passes in mock mode; with `make up`, backend slice 16 and fixtures, the stack run passes (erasure completes after cooling-off with a second administrator).
6. `npm run e2e:a11y -- --grep privacy` zero serious or critical violations in both locales and at 360 px for the portal pages.
7. `grep -rn "supprim" src/i18n/messages/fr/privacy.json` shows no sentence that promises complete deletion without the retained-records explanation (checked by `privacy.copy.test.ts` that fails on the French and English phrases "toutes vos données" and "all your data" without "financ").

## Demo script

1. As a customer open `/fr/portal/profile/privacy`: "Mes données", two buttons. EN: "My data", "Download my data", "Delete my account".
2. "Télécharger mes données" / "Download my data": confirm your number and the SMS code, choose format and scope; the status page shows "En attente d'approbation" / "Waiting for approval".
3. "Supprimer mon compte" / "Delete my account" with money left in the wallet: the page lists "Il reste de l'argent dans votre portefeuille..." / "There is still money in your wallet..."
4. Empty the wallet, retry: the confirmation shows "Nous attendons 7 jours avant de supprimer quoi que ce soit..." / "We wait 7 days before deleting anything...", then the request page shows the steps and "Délai de réflexion jusqu'au ..." / "Cooling-off until ...", with "Annuler ma demande" / "Cancel my request".
5. As an administrator with `dp_admin` open `/fr/staff/admin/privacy`: verify the identity (step-up); the approve button says "Disponible après le ..." / "Available after ..." until the date passes; then propose the approval; a second administrator approves in "Approbations".
6. Customer page now reads "Terminée" and "Vos informations personnelles ont été retirées. Les enregistrements financiers sont conservés comme la loi l'exige, sans vos données personnelles." / "Your personal details have been removed. Financial records are kept as the law requires, without your personal data."
7. Open "Durées de conservation" / "Retention periods" as finance: read-only table.

## Done criteria

- Requirements: FR-20 (export and erasure flows, blockers, cooling-off, queue, retention view), NFR-07, NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8: TC-FR-20-4 (operator runs erasure in the UI and sees confirmation and a completed state), TC-FR-20-5 (blockers and cooling-off shown, approval disabled early), TC-FR-20-7 (no request without the second proof), TC-FR-20-3 (unauthorised roles have no screen; customers only their own), TC-FR-15-15 (administrator without `dp_admin` sees no privacy queue).

## Plan notes for the tech lead

- **`identity_proof` format** is unspecified in `api/openapi.yaml` (see open item). Customer retyping the registered phone is also the only second proof available without a new operation.
- **Erasure status vocabulary:** `docs/08` 3.14 lists "Requested, Cooling-off, Under review, Completed or Cancelled"; the API enum is `pending_approval`, `verified`, `approved`, `processing`, `completed`, `rejected`, `cancelled`, `blocked_by_retention`. Followed the API enum (D-01) and mapped to labels.
- **Blocker codes:** the API lists `wallet_non_zero`, `unacknowledged_tokens`, `legal_hold`, `open_dispute`; `docs/08` mentions a "7-day cooling-off" as a blocker-like state, which is a status field, not a blocker code.
- **Administrator picking a customer:** administrators have no customer-content operations (D-14), so the export-on-behalf form takes a pseudonymous customer id typed by the operator; no search is offered.
- **Retention roles:** `listRetentionPolicies` is allowed for administrator, finance and operations, so the retention page appears for those three roles even though the rest of the privacy section is `dp_admin` only.
- **Portal route:** privacy lives under `/portal/profile/privacy` (`docs/03` folder `profile/`); `docs/08` calls it `/account`.
