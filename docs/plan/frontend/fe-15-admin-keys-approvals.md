# Frontend slice 15: Approvals (maker-checker), users and roles, API keys, audit log, AI spend

- **Goal:** staff see every pending approval with the exact proposed payload, approve or reject as a different person after step-up; administrators manage users, roles, MFA resets and integrator and gateway keys through approvals; authorised roles read the audit log; operations and finance read AI spend. The administrator stays a platform administrator with no money, ticket, customer-content or ask-the-data screens.
- **Depends on:** backend slices 1 (approvals, identity) and 15 (API keys); frontend slices fe-00, fe-01 (step-up), fe-03, fe-12 (co-approval page), fe-14, fe-16 (erasure approval links).
- **Requirements served:** FR-15, FR-18, FR-17 (publication approval), NFR-09 and NFR-13 (AI spend view), NFR-06, NFR-10, NFR-11.
- **Size:** L, 9 days.
- **Spikes or open items:** (1) How the creator receives an API-key secret after approval is not fully specified (`ApiKeyCreate` says the secret is delivered once to the creator when the approval executes); default: the key detail page shows the secret once in a modal if the `getApiKey` response contains `secret`, never again **[open: confirm delivery with backend slice 15 author]**. (2) Payload-hash verification in the browser needs the server's canonical form; default: the page displays the stored hash and sends it back, the server's 409 is the check (see Plan notes).

## Read first

- `docs/08-frontend-ux.md#3.10 Role and API key admin (FR-15, FR-18)`
- `docs/08-frontend-ux.md#3.11 Maker-checker: pending approvals and review (D-15)`
- `docs/08-frontend-ux.md#3.12 Step-up prompt (D-16)`
- `docs/08-frontend-ux.md#3.9 Audit log viewer (FR-15)`
- `docs/08-frontend-ux.md#4. AI UX rules` (cost row)
- `docs/08-frontend-ux.md#2.3 Staff app sitemap and navigation per role`
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)`
- `docs/00-shared-context.md` section 7: D-13, D-14, D-15, D-16, D-19, D-34, D-36
- OperationIds: `listApprovals`, `createApproval`, `getApproval`, `approveApproval`, `rejectApproval`, `listUsers`, `createUser`, `getUser`, `updateUser`, `replaceUserRoles`, `replaceUserSites`, `resetUserMfa`, `listRoles`, `listSites`, `listAuditLog`, `listApiKeys`, `createApiKey`, `getApiKey`, `updateApiKey`, `revokeApiKey`, `rotateApiKey`, `getAiUsage`, `listAiRuns`, `getAiRun`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page.
- Access token in memory only. API-key secrets exist only in component state while the one-time modal is open; never in storage, URL, query cache persistence or logs.
- Budgets: staff first load at most 250 KB; the audit JSON viewer is a `<pre>` of escaped text.
- next-intl `/fr` and `/en`; French first.
- WCAG 2.2 AA: tables native; the one-time secret modal has Copy and an acknowledgement checkbox; destructive actions need typed confirmation; maker-disabled buttons explain why in text.
- Generated client only; approvals send `payload_hash` back on approve (`ApprovalDecision`).
- The maker cannot approve their own proposal: the UI disables the controls with text (user compared client-side; the server also compares the session); a 403 `self_approval_forbidden` or `conflict_of_interest` is shown in plain words.
- Step-up (max age 5 minutes) guards approvals, role grants, user creation, MFA reset, key creation and revocation; handled by `withStepUp` (FE-01).
- Role and key changes are never optimistic; every proposal screen says "Rien n'est encore appliqué" until the approval status is `executed`.
- AI cost, tokens and latency (`getAiUsage`, `listAiRuns`) appear only on `/staff/ai-spend` and only for roles the API allows (operations and finance); they appear nowhere else.
- The administrator has no navigation entry or route for tickets, money, customers, ask-the-data, dashboard.
- Do not edit frozen files.

## Review focus

1. The maker approves their own proposal, or a mismatched payload is approved: `ApprovalReview.test.tsx` "maker cannot approve; approve sends stored payload_hash; 409 mismatch message" (Task FE-15.2).
2. The approval screen shows a summary instead of the exact payload: `ApprovalReview.test.tsx` "renders every payload field and the raw canonical JSON" (Task FE-15.2).
3. An API-key secret is shown twice or stored: `KeySecretModal.test.tsx` "shown once, wiped on close, not in storage" (Task FE-15.4).
4. A gateway key is created without sites or address range, or with scopes of the other kind: `ApiKeyForm.test.tsx` "kind switches scope set; gateway requires sites and CIDR; expiry max 180 days" (Task FE-15.4).
5. An administrator reaches money or ticket content, or finance sees the full audit: `nav` and guard tests plus `AuditLog.test.tsx` "streams come from the API; no client filtering that widens access" (Tasks FE-15.5, FE-01 tests).

## Files

- `frontend/src/features/approvals/keys.ts`, `hooks/useApprovals.ts`, `useApproval.ts`, `useDecideApproval.ts`.
- `frontend/src/features/approvals/components/ApprovalInbox.tsx`, `ApprovalReview.tsx`, `PayloadView.tsx`, `ApprovalActions.tsx`, `ApprovalKindLink.tsx`, `payloadRenderers.ts` (per-kind field labels).
- `frontend/src/features/admin/keys.ts`, `hooks/useUsers.ts`, `useUserMutations.ts`, `useRoles.ts`, `useApiKeys.ts`, `useApiKeyMutations.ts`, `useAudit.ts`, `useAiUsage.ts`.
- `frontend/src/features/admin/schemas/user.ts`, `apiKey.ts`.
- `frontend/src/features/admin/components/UserList.tsx`, `UserForm.tsx`, `RolesDialog.tsx`, `SitesDialog.tsx`, `ResetMfaDialog.tsx`, `RoleMatrix.tsx`, `ApiKeyList.tsx`, `ApiKeyForm.tsx`, `ApiKeyDetail.tsx`, `KeySecretModal.tsx`, `RevokeKeyDialog.tsx`, `AuditLog.tsx`, `AuditFilters.tsx`, `AiSpend.tsx`, `AiRunList.tsx`.
- `frontend/src/app/[locale]/(staff)/staff/approvals/page.tsx`, `approvals/[id]/page.tsx`, `admin/users/page.tsx`, `admin/users/[id]/page.tsx`, `admin/roles/page.tsx`, `admin/api-keys/page.tsx`, `admin/api-keys/new/page.tsx`, `admin/api-keys/[id]/page.tsx`, `audit/page.tsx`, `ai-spend/page.tsx`.
- `frontend/src/i18n/messages/{fr,en}/approvals.json`, `admin.json`, `audit.json`.
- Tests next to components; `frontend/e2e/approvals.spec.ts`, `admin.spec.ts`.

## Interfaces

**Routes (staff):** `/staff/approvals`, `/staff/approvals/[id]`, `/staff/admin/users`, `/staff/admin/users/[id]`, `/staff/admin/roles`, `/staff/admin/api-keys`, `/staff/admin/api-keys/new`, `/staff/admin/api-keys/[id]`, `/staff/audit`, `/staff/ai-spend`.

**Namespace `approvals` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `approvals.title` | Approbations | Approvals |
| `approvals.tab.waiting` | À valider par moi ({count}) | Waiting for me ({count}) |
| `approvals.tab.mine` | Mes propositions ({count}) | My proposals ({count}) |
| `approvals.tab.history` | Historique | History |
| `approvals.kind.reconciliation_resolution` | Résolution de rapprochement | Reconciliation resolution |
| `approvals.kind.manual_adjustment` | Ajustement manuel | Manual adjustment |
| `approvals.kind.refund` | Remboursement | Refund |
| `approvals.kind.tariff_change` | Changement de tarif | Tariff change |
| `approvals.kind.role_grant` | Attribution de rôle | Role grant |
| `approvals.kind.user_creation` | Création d'utilisateur | User creation |
| `approvals.kind.mfa_reset` | Réinitialisation de l'authentification forte | MFA reset |
| `approvals.kind.api_key_creation` | Création de clé d'API | API key creation |
| `approvals.kind.erasure` | Effacement de données | Data erasure |
| `approvals.kind.data_export` | Export de données | Data export |
| `approvals.kind.kb_publish` | Publication d'article | Article publication |
| `approvals.kind.draft_co_approval` | Accord de la finance sur un brouillon | Finance co-approval of a draft |
| `approvals.status.pending` | En attente | Pending |
| `approvals.status.approved` | Approuvée | Approved |
| `approvals.status.rejected` | Rejetée | Rejected |
| `approvals.status.cancelled` | Annulée | Cancelled |
| `approvals.status.expired` | Expirée | Expired |
| `approvals.status.executed` | Appliquée | Applied |
| `approvals.status.failed` | Échec à l'application | Failed to apply |
| `approvals.empty` | Rien n'attend votre validation. | Nothing is waiting for you. |
| `approvals.payload.title` | Contenu exact de la proposition | Exact content of the proposal |
| `approvals.payload.raw` | Afficher le contenu brut (JSON) | Show raw content (JSON) |
| `approvals.hash` | Empreinte de la proposition : {hash} | Proposal fingerprint: {hash} |
| `approvals.maker.blocked` | Vous avez proposé ceci. Une autre personne doit l'approuver. | You proposed this. Another person must approve it. |
| `approvals.approve` | Approuver | Approve |
| `approvals.reject` | Rejeter | Reject |
| `approvals.reject.note` | Motif du rejet | Reason for rejection |
| `approvals.confirm.title` | Confirmer : {kind} | Confirm: {kind} |
| `approvals.done.approved` | Approbation enregistrée. | Approval recorded. |
| `approvals.done.rejected` | Proposition rejetée. | Proposal rejected. |
| `approvals.error.self_approval_forbidden` | Vous ne pouvez pas approuver votre propre proposition. | You cannot approve your own proposal. |
| `approvals.error.conflict_of_interest` | Vous êtes lié à ce client : une autre personne doit approuver. | You are linked to this customer: another person must approve. |
| `approvals.error.approval_payload_mismatch` | Cette proposition a changé depuis son envoi. Rejetez-la et demandez-en une nouvelle. | This proposal changed after it was submitted. Reject it and ask for a new one. |
| `approvals.error.expired` | Cette proposition a expiré. | This proposal has expired. |
| `approvals.notAppliedYet` | Rien n'est encore appliqué tant que l'approbation n'est pas validée. | Nothing is applied until the approval is confirmed. |

**Namespaces `admin` and `audit` (key: fr / en), the main ones:**

| Key | fr | en |
|---|---|---|
| `admin.users.title` | Utilisateurs | Users |
| `admin.users.new` | Nouvel utilisateur | New user |
| `admin.users.submitted` | Proposition n° {id} envoyée. L'utilisateur sera créé après approbation d'un autre administrateur. | Proposal no. {id} sent. The user will be created after another administrator approves. |
| `admin.roles.title` | Rôles et permissions | Roles and permissions |
| `admin.roles.adminNote` | Les administrateurs n'ont accès ni à l'argent, ni aux demandes d'assistance, ni aux données clients, ni à l'interrogation des données. | Administrators have no access to money, support requests, customer data or ask-the-data. |
| `admin.roles.lastAdmin` | Le dernier administrateur ne peut pas être rétrogradé. | The last administrator cannot be demoted. |
| `admin.mfaReset.submit` | Demander la réinitialisation | Request reset |
| `admin.mfaReset.note` | Un deuxième administrateur doit approuver. L'utilisateur devra se réinscrire à la prochaine connexion. | A second administrator must approve. The user will have to re-enrol at next sign-in. |
| `admin.keys.title` | Clés d'API | API keys |
| `admin.keys.kind.gateway` | Passerelle | Gateway |
| `admin.keys.kind.integrator` | Intégrateur | Integrator |
| `admin.keys.status.active` | Active | Active |
| `admin.keys.status.revoked` | Révoquée | Revoked |
| `admin.keys.status.expired` | Expirée | Expired |
| `admin.keys.expiry` | Expire le {date} (180 jours maximum) | Expires on {date} (180 days maximum) |
| `admin.keys.secret.title` | Copiez cette clé maintenant | Copy this key now |
| `admin.keys.secret.once` | Elle ne sera plus jamais affichée. | It will never be shown again. |
| `admin.keys.secret.ack` | J'ai enregistré cette clé | I stored this key |
| `admin.keys.revoke.confirm` | Saisissez le nom de la clé pour confirmer | Type the key name to confirm |
| `admin.keys.submitted` | Proposition n° {id} envoyée. Un deuxième administrateur doit l'approuver. | Proposal no. {id} sent. A second administrator must approve it. |
| `audit.title` | Journal d'audit | Audit log |
| `audit.outcome.success` | Réussi | Success |
| `audit.outcome.denied` | Refusé | Denied |
| `audit.outcome.error` | Erreur | Error |
| `audit.details` | Afficher les détails | Show details |
| `audit.empty` | Aucun événement ne correspond à ces critères. | No event matches these criteria. |
| `aispend.title` | Dépenses d'IA | AI spend |
| `aispend.feature.triage` | Tri | Triage |
| `aispend.feature.reply_draft` | Brouillons de réponse | Reply drafts |
| `aispend.feature.summary` | Résumés | Summaries |
| `aispend.feature.ask_data` | Interrogation des données | Ask the data |
| `aispend.tokensEquivalent` | Environ {tokens} jetons par dollar (indicatif) | About {tokens} tokens per dollar (indicative) |

**Hooks:** `useApprovals({status?, kind?, mine?, cursor?})`; `useApproval(id)`; `useDecideApproval(id)` with `approve({note?})` (sends `payload_hash` from the loaded approval) and `reject({note})`; `useApiKeys()`; `useCreateApiKey()`; `useAudit(filters)`; `useAiUsage()`.

**Components:** `PayloadView({approval})` renders `payload` entries as a labelled definition list (amounts via `MoneyText exact`, ids as text, nested objects flattened with dotted labels) and a raw JSON `<pre>` disclosure; `ApprovalKindLink({approval})` links by kind: `draft_co_approval` to `/staff/drafts/{subject_id}/co-approve?approval={id}` (fe-12), `erasure` and `data_export` to `/staff/admin/privacy` (fe-16), `kb_publish` to the KB article, others show no link.

**Operations consumed:** the 24 operationIds under Read first.

## Tasks

### Task FE-15.1: Approvals inbox

- [ ] **Step 1: failing test** `ApprovalInbox.test.tsx`:
  ```ts
  test('tabs: waiting for me excludes my own proposals; counts shown', async () => {
    mockApprovals([A_OTHER, A_MINE]); renderAs(P_ME);
    expect(await screen.findByRole('tab',{name:'À valider par moi (1)'})).toBeVisible();
    expect(screen.getByRole('link',{name:/Ajustement manuel/})).toBeVisible();
  });
  test('table shows kind label, maker, age, subject, status text', ...);
  test('empty state', () => expect(screen.getByText('Rien n\'attend votre validation.')).toBeVisible());
  test('support agent sees only my proposals tab (cannot approve)', ...);
  test('error, partial, offline states', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- ApprovalInbox`. Expected: fail.
- [ ] **Step 3: implement** `ApprovalInbox` (tabs as `role="tablist"` with arrow-key navigation, `useApprovals`; "waiting" = status `pending` and `maker_id !== principal.id`; "mine" = `maker_id === principal.id`; "history" = non-pending), `DataTable` with link per row to `/staff/approvals/[id]`, cursor links. Visibility of the waiting tab requires `usePermission('approveApproval')`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): approvals inbox with waiting and own proposals`.

### Task FE-15.2: Approval review, approve and reject (TC-FR-15-7 UI, TC-FR-15-8 UI, TC-FR-15-9 UI)

- [ ] **Step 1:** `ApprovalReview.test.tsx`, `ApprovalActions.test.tsx`, `e2e/approvals.spec.ts`:
  ```ts
  test('renders every payload field and the raw canonical JSON', () => { for (const [k] of Object.entries(A.payload)) expect(screen.getByText(labelFor(k))).toBeVisible(); expect(screen.getByText(/"amount_minor": 500/)).toBeInTheDocument(); });
  test('maker cannot approve: buttons disabled with explanation', () => { renderAs(MAKER); expect(screen.getByRole('button',{name:'Approuver'})).toBeDisabled(); expect(screen.getByText('Vous avez proposé ceci. Une autre personne doit l\'approuver.')).toBeVisible(); });
  test('approve sends stored payload_hash after step-up, shows recorded message', async () => {
    await user.click(btn('Approuver')); await confirmDialog(); await stepUp();
    expect(lastBody()).toEqual({payload_hash:A.payload_hash, note:undefined}); expect(await screen.findByRole('status')).toHaveTextContent('Approbation enregistrée.');
  });
  test('409 approval_payload_mismatch shows plain message and disables approve', ...);
  test('403 self_approval_forbidden and conflict_of_interest messages', ...);
  test('expired shows expired message, no actions', ...);
  test('reject requires note and sends rejectApproval', ...);
  test('kind links: draft_co_approval goes to fe-12 page', ...);
  ```
  Playwright: maker proposes (any slice's proposal in mock), second user approves; first user sees the disabled state on their own.
- [ ] **Step 2:** `npm test -- ApprovalReview ApprovalActions`; `npm run e2e -- approvals.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** `/staff/approvals/[id]`: header (kind, status, maker, created, expires), `PayloadView`, fingerprint line `approvals.hash` (the stored `payload_hash`, selectable text), `ApprovalKindLink`, `ApprovalActions`. Approve opens a confirmation dialog repeating the kind and the key amounts, then `withStepUp(kindLabel, () => approveApproval({payload_hash, note}))`; reject opens a dialog with a required note then `rejectApproval({note})` (also under step-up if the API requires it). Controls are disabled for the maker (`maker_id === principal.id`) with `approvals.maker.blocked`, and hidden without `approveApproval` permission. Map problem codes `self_approval_forbidden`, `conflict_of_interest`, `approval_payload_mismatch`, and an expired status to the translated messages. After success refetch the approval and the inbox lists and announce politely.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): approval review with exact payload, hash binding and decisions`.

### Task FE-15.3: Users, roles, sites and MFA reset (TC-FR-15-11 UI)

- [ ] **Step 1:** `UserList.test.tsx`, `RolesDialog.test.tsx`, `ResetMfaDialog.test.tsx`, `RoleMatrix.test.tsx`, `e2e/admin.spec.ts`:
  ```ts
  test('create user submits proposal and shows submitted message, user not listed as active', async () => { server.use(createUser202(APPROVAL)); await fillAndSubmit(); expect(await screen.findByRole('status')).toHaveTextContent('Proposition n°'); });
  test('role change is a proposal through step-up; shows "Rien n\'est encore appliqué"', ...);
  test('last administrator cannot be demoted: message from API 409 shown', ...);
  test('role matrix generated from listRoles: Autorisé / Non autorisé text with icons', ...);
  test('administrator note about no money, tickets, customer data, ask-the-data is shown', ...);
  test('MFA reset needs second administrator; note shown; no immediate effect', ...);
  test('webauthn_enrolled and mfa_enrolled columns as text', ...);
  test('only administrator sees admin entries', ...);
  ```
- [ ] **Step 2:** `npm test -- UserList RolesDialog ResetMfaDialog RoleMatrix`. Expected: fail.
- [ ] **Step 3: implement** `UserList` (`listUsers`; columns name, email, roles as text list, attributes, status, MFA and WebAuthn enrolled as text, last login), `UserForm` (create: `createUser` returns 202, show `admin.users.submitted`), edit `updateUser` (status active or suspended, locale, full name), `RolesDialog` (`replaceUserRoles` with roles, attributes, reason ≥ 5; 202 approval message; the dialog states that finance and administrator grants need a second administrator and take effect at the next token refresh), `SitesDialog` (`replaceUserSites` with site multiselect from `listSites`; support and operations users only), `ResetMfaDialog` (`resetUserMfa` proposal with reason, note `admin.mfaReset.note`), `RoleMatrix` (`listRoles`: rows permissions, columns roles, cell text "Autorisé"/"Non autorisé" with icon; `admin.roles.adminNote` shown above). All proposals through `withStepUp` and `handleApprovalResult`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): user, role, site and MFA reset proposals`.

### Task FE-15.4: API keys with one-time secret (TC-FR-18-2 UI, TC-FR-18-4 UI)

- [ ] **Step 1:** `ApiKeyForm.test.tsx`, `KeySecretModal.test.tsx`, `RevokeKeyDialog.test.tsx`, `ApiKeyList.test.tsx`:
  ```ts
  test('kind switches scope set; gateway requires sites and CIDR; expiry max 180 days', async () => {
    await choose('Passerelle'); expect(scopeLabels()).toEqual(['telemetry:write','tokens:acknowledge','gateway:heartbeat']);
    await submitEmpty(); expect(errors()).toContain(/site/i); expect(errors()).toContain(/adresse/i);
    await choose('Intégrateur'); expect(scopeLabels()).not.toContain('telemetry:write');
    setExpiry(daysFromNow(181)); expect(screen.getByRole('alert')).toHaveTextContent('180 jours');
  });
  test('create sends proposal through step-up and shows submitted message, no secret yet', ...);
  test('secret modal: shown once, copy button, ack checkbox required to close, wiped from state on close, not in storage', async () => {
    render(<KeySecretModal secret="sgh_live_ab12_secret"/>); expect(screen.getByText('sgh_live_ab12_secret')).toBeVisible();
    expect(screen.getByRole('button',{name:'Fermer'})).toBeDisabled(); await user.click(ack()); await user.click(close());
    expect(screen.queryByText('sgh_live_ab12_secret')).toBeNull(); expect(JSON.stringify(localStorage)+JSON.stringify(sessionStorage)).not.toContain('secret');
  });
  test('list shows prefix only, kind, scopes, CIDR, tier, last used, status text', ...);
  test('revoke needs typed key name and step-up; calls revokeApiKey', ...);
  test('update allows only name, CIDRs and tier; scopes and expiry not editable', ...);
  test('rotate is a proposal (approval) and shows submitted message', ...);
  ```
- [ ] **Step 2:** `npm test -- ApiKeyForm KeySecretModal RevokeKeyDialog ApiKeyList`. Expected: fail.
- [ ] **Step 3: implement** `ApiKeyList` (`listApiKeys`), `ApiKeyForm` (name, kind radio, scopes as a checkbox group whose options depend on kind: gateway `telemetry:write`, `tokens:acknowledge`, `gateway:heartbeat`; integrator `customers:read`, `meters:read`, `topups:read`, `topups:write`, `tickets:read`, `tickets:write`, `tickets:inbound`; sites multiselect and CIDR list required for gateway; rate-limit tier select `standard`, `high`, `gateway` (gateway tier only for gateway kind); expiry date at most 180 days from today; reason), submit via `createApiKey` under `withStepUp`, 202 shows `admin.keys.submitted`. `ApiKeyDetail` (`getApiKey`): if the response includes `secret`, open `KeySecretModal` (state-only copy, ack checkbox, Close disabled until acknowledged, secret variable set to `null` on close, `autoComplete="off"`); `updateApiKey` (name, CIDRs, tier); `rotateApiKey` proposal; `RevokeKeyDialog` (type the name, step-up, `revokeApiKey`). Never persist query data of `getApiKey` (set `gcTime: 0` and exclude from persistence).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): API key admin with one-time secret display`.

### Task FE-15.5: Audit log viewer

- [ ] **Step 1:** `AuditLog.test.tsx`:
  ```ts
  test('table with time (UTC and local), actor, action, entity, outcome text, request id; cursor links', ...);
  test('filters stream, actor, action, entity type, outcome, date range apply on Appliquer', ...);
  test('details disclosure shows escaped JSON', () => expect(document.querySelector('img')).toBeNull());
  test('no edit or delete controls exist', () => expect(screen.queryByRole('button',{name:/supprimer|modifier|delete|edit/i})).toBeNull());
  test('streams offered are those present in the API response, not a client list', ...);
  test('empty, error, offline states', ...);
  test('finance and operations see the page; support does not', ...);
  ```
- [ ] **Step 2:** `npm test -- AuditLog`. Expected: fail.
- [ ] **Step 3: implement** `AuditLog` (`listAuditLog`; columns occurred_at via `DateText` plus a UTC text, `actor_type` and `actor_id` short form, `action`, `entity_type` and `entity_id`, outcome badge text, `request_id`; row disclosure with `details` as `JSON.stringify(details, null, 2)` in `<pre>`; hash fields shown in a "Intégrité" disclosure as text). Filters from query params only; the stream filter lists the distinct `stream` values of loaded rows plus a free field. No export button (no operation exists). Page guard `listAuditLog`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): audit log viewer`.

### Task FE-15.6: AI spend page

- [ ] **Step 1:** `AiSpend.test.tsx`, `nocost.test.tsx`:
  ```ts
  test('per-feature table: requests, tokens, cost in dollars exact, daily cap and spent today, p95 latency', ...);
  test('token equivalent per dollar shown as indicative', ...);
  test('role without getAiUsage permission (support, administrator) sees no entry and gets no-access page', ...);
  test('cost, tokens and latency strings never render outside the ai-spend page (scan of ticket, draft and ask components)', ...);
  test('run list shows status and feature only; detail does not show prompt text', ...);
  ```
- [ ] **Step 2:** `npm test -- AiSpend nocost`. Expected: fail.
- [ ] **Step 3: implement** `AiSpend` (`getAiUsage`: period selector from query params; table by feature with `MoneyText exact` for `cost`, `daily_cap`, `spent_today`; tokens as numbers; `p95_latency_ms`; refusals; caps card (per ticket, per customer daily, per API key daily); indicative token equivalent) and `AiRunList` (`listAiRuns`, `getAiRun`: id, feature, status, time; no prompt payload even if returned). Page guard `getAiUsage` (operations and finance per `permissions.yaml`).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): AI spend page for finance and operations`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes.
3. `npm run i18n:check` exits 0 (`approvals`, `admin`, `audit` equal in both locales).
4. `npm run build && npm run size` exit 0.
5. `npm run e2e -- approvals.spec.ts admin.spec.ts` pass in mock mode; with `make up`, backend slices 1 and 15, the stack runs pass (user creation needs a second administrator; key secret shown once).
6. `npm run e2e:a11y -- --grep "approvals|admin|audit|ai-spend"` zero serious or critical violations in both locales.
7. `grep -rn "localStorage\|sessionStorage" src/features/admin` returns nothing.

## Demo script

1. As an administrator create a user: "Proposition n° … envoyée. L'utilisateur sera créé après approbation d'un autre administrateur." / "Proposal no. … sent. The user will be created after another administrator approves."
2. Open "Approbations" as the same administrator: the item appears under "Mes propositions" only; the review page shows "Vous avez proposé ceci. Une autre personne doit l'approuver." / "You proposed this. Another person must approve it."
3. Sign in as a second administrator: it appears under "À valider par moi" / "Waiting for me"; open it, check the exact content and fingerprint, press "Approuver" / "Approve", confirm step-up: "Approbation enregistrée." / "Approval recorded."
4. Create an API key of kind "Passerelle" / "Gateway" with sites and address range; after the second administrator approves, open the key: "Copiez cette clé maintenant" / "Copy this key now"; tick "J'ai enregistré cette clé" / "I stored this key" and close; reopen: no secret.
5. Revoke a key by typing its name.
6. As the administrator open `/fr/staff/tickets`, `/fr/staff/ledger`, `/fr/staff/ask`: "Accès non autorisé" / "You do not have access"; the navigation shows only Approbations, Utilisateurs, Rôles, Clés d'API, Base de connaissances, Audit.
7. As an operations engineer or finance officer open "Dépenses d'IA" / "AI spend": per-feature cost in dollars; as support or the administrator the entry is absent.

## Done criteria

- Requirements: FR-15 (approvals, users, roles, audit view, MFA reset), FR-18 (keys), FR-17 (publication approval visible), NFR-09 and NFR-13 (spend view), NFR-06, NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8: TC-FR-15-7 (self-approval refused and disabled), TC-FR-15-8 (approval bound to the payload hash; mismatch message), TC-FR-15-9 (step-up before approvals, role grants, key creation), TC-FR-15-11 (MFA reset alone has no effect), TC-FR-15-15 (administrator has no money, ticket, customer or ask screens), TC-FR-18-2 (key shown once), TC-FR-18-4 (create a scoped key), TC-FR-18-6 (180-day limit and approval for creation), TC-FR-17-4 (publication approval pending), TC-FR-10-7 (co-approval items reach the finance page), TC-FR-14-4.

## Plan notes for the tech lead

- **AI spend roles:** `docs/08` section 4 and 2.3 say the administrator sees AI cost on its own page; `api/permissions.yaml` allows `getAiUsage`, `listAiRuns`, `getAiRun` only for `operations_engineer` and `finance_officer`, and D-14 gives the administrator no such access. Followed `permissions.yaml`. The wording "administrator only" in `docs/08` and in the coordinator's brief needs correcting.
- **Hash verification:** the Approval carries `payload_hash` (example `sha256:9b1f0c7a52d5`, a truncated form) with no stated canonical serialisation, so a browser-side recomputation would be a guess. The screen shows the fingerprint and relies on the server's `approval_payload_mismatch` 409. `docs/08` 3.11 promises a "Hash check" status; it is reduced to the server check until `docs/05` specifies the canonical form.
- **Key secret delivery:** see open item; if the API returns the secret in the `createApiKey` response instead, the same modal is used from the approval-executed notification.
- **Who sees which approvals:** `listApprovals` is allowed for four roles, `approveApproval` for three (not support). The server decides which pending items each role may decide; the UI does not filter beyond maker versus not-maker.
- **Audit export and integrity indicator:** no API operation for export or for "chain verified"; neither is built (`docs/08` 3.9 mentions both).
- **Sessions list and user names:** no operation to resolve user ids to names outside the administrator's user list; approvals show `maker_id` as a short id except for the current user, and for administrators a name from `listUsers` when available.
