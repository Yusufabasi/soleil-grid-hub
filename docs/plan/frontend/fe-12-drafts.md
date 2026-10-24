# Frontend slice 12: AI reply drafts, approval, recall and finance co-approval

- **Goal:** a support agent asks for an AI reply draft on a ticket, reviews it clearly labelled as AI-written with its citations and checks, edits it, approves it through a confirmation dialog and step-up (or rejects it), sees what the customer will receive, and can recall it within 30 seconds; money-affecting drafts require a finance co-approval. Nothing is ever sent without a person pressing Approve.
- **Depends on:** backend slice 12 (reply drafting with approval; needs slices 10, 11 and 7 for tickets, AI gateway, notifications); frontend slices fe-00, fe-01 (step-up, approval helper), fe-10 (ticket detail, `SafeMarkdown`, KB version read).
- **Requirements served:** FR-10, FR-16 (AI output language), NFR-08 (human review), NFR-10, NFR-11.
- **Size:** L, 7 days.
- **Spikes or open items:** (1) the payload sent when asking for a finance co-approval (`createApproval` with kind `draft_co_approval`) is not specified field by field in `api/openapi.yaml`; default `{draft_id, version}` plus the money claims text hashes computed by the server **[open: confirm with backend slice 12 author]**. (2) Customer-facing channel preview: the API gives no channel field for a draft; the preview shows the message text only.

## Read first

- `docs/08-frontend-ux.md#3.4 AI draft review screen (FR-10, invariant 2)` (wireframe, all rules, state table)
- `docs/08-frontend-ux.md#4. AI UX rules`
- `docs/08-frontend-ux.md#3.11 Maker-checker: pending approvals and review (D-15)` (co-approval is an approval kind)
- `docs/08-frontend-ux.md#3.12 Step-up prompt (D-16)`
- `docs/08-frontend-ux.md#7.3 Screen-reader semantics for tables and live regions` (AI status and undo window rows)
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)` (Client state: optimistic only for draft edit)
- `docs/00-shared-context.md` section 7: D-35, D-36, D-50, section 3 invariant 2
- OperationIds: `requestReplyDraft`, `listTicketDrafts`, `getDraft`, `editDraft`, `approveDraft`, `coApproveDraft`, `getDraftMoneyClaims`, `rejectDraft`, `recallDraft`, `createApproval`, `getApproval`, `getKbVersion`, `getTicket`, `listTicketMessages`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page.
- Access token in memory only. Invariant 2: no code path sends a draft without the explicit Approve confirmation dialog; no timer, no keyboard shortcut for approve, no auto-approve.
- Budgets: staff first load at most 250 KB; the draft route loads its editor lazily.
- next-intl `/fr` and `/en`; French first; draft body carries its own `lang` attribute (the draft locale).
- WCAG 2.2 AA: AI label is text plus icon, never colour only; the undo countdown is visual only (`aria-hidden`) with announcements at start, at 10 s left and at expiry; dialogs return focus.
- Generated client only. Approve uses `Idempotency-Key` and `version`; edit is optimistic only for the editor's local text, never for status.
- Never show model name, tokens, cost or latency on the draft screen. The administrator and finance cannot open the draft screen except the finance co-approval page (restricted view).
- Draft and KB text are untrusted: the body is plain text; citations excerpts via plain text; no clickable links or images from the model are rendered in the preview.
- Approve, co-approve and recall require step-up where the API says so (`approveDraft` does).
- Do not edit frozen files.

## Review focus

1. A draft is sent by a double click or a stray key: `ApproveDialog.test.tsx` "enter on the page does not approve; one request with one Idempotency-Key" and `e2e/drafts.spec.ts` (Task FE-12.3).
2. A money-affecting draft shows a normal Approve button: `DraftReview.test.tsx` "money_affecting shows co-approval notice and no direct approve" (Task FE-12.3).
3. Editing at approval changes text that skips the lints: `ApproveDialog.test.tsx` "re-lint findings from server shown before closing" (Task FE-12.3).
4. The undo bar disappears at the wrong time or spams a screen reader: `RecallBar.test.tsx` "announces only at start, 10 s and expiry; refused after window" (Task FE-12.4).
5. Uncited or unsupported claims look fine: `CitationList.test.tsx` and `DraftReview.test.tsx` "lint findings and unverified money claims are listed and block silent approve" (Task FE-12.2).

## Files

- `frontend/src/features/support/keys.ts` (modify: draft keys).
- `frontend/src/features/support/hooks/useDrafts.ts`, `useDraft.ts` (polls while generating), `useRequestDraft.ts`, `useEditDraft.ts`, `useApproveDraft.ts`, `useRejectDraft.ts`, `useRecallDraft.ts`, `useMoneyClaims.ts`, `useCoApprove.ts`.
- `frontend/src/features/support/components/drafts/DraftSection.tsx` (inside ticket detail), `DraftReview.tsx`, `AiBanner.tsx`, `DraftEditor.tsx`, `CitationList.tsx`, `CitationDrawer.tsx`, `ChecksPanel.tsx`, `CustomerPreview.tsx`, `ApproveDialog.tsx`, `RejectDialog.tsx`, `RecallBar.tsx`, `DraftHistory.tsx`, `CoApproveView.tsx`, `RequestFinanceApproval.tsx`.
- `frontend/src/features/support/drafts/countdown.ts` (pure helper `remainingSeconds(recallUntil, now)`).
- `frontend/src/app/[locale]/(staff)/staff/tickets/[id]/draft/[draftId]/page.tsx`, `staff/drafts/[draftId]/co-approve/page.tsx`.
- `frontend/src/i18n/messages/{fr,en}/drafts.json`.
- Tests next to components; `frontend/e2e/drafts.spec.ts`.

## Interfaces

**Routes (staff):** `/staff/tickets/[id]/draft/[draftId]` (review), `/staff/drafts/[draftId]/co-approve?approval=<id>` (finance, restricted view). The ticket detail page (fe-10) shows `DraftSection` with "Rédiger avec l'IA" and the list of drafts.

**Namespace `drafts` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `drafts.request` | Rédiger avec l'IA | Draft with AI |
| `drafts.writeByHand` | Rédiger à la main | Write by hand |
| `drafts.generating` | Rédaction du brouillon en cours ({seconds} s) | Drafting reply ({seconds} s) |
| `drafts.banner` | RÉDIGÉ PAR L'IA. PAS ENCORE ENVOYÉ. Le client ne voit rien tant que vous n'avez pas approuvé. | WRITTEN BY AI. NOT SENT YET. The customer sees nothing until you approve. |
| `drafts.bannerEdited` | Brouillon de l'IA, modifié par vous. Pas encore envoyé. | AI draft, edited by you. Not sent yet. |
| `drafts.language` | Langue de la réponse | Reply language |
| `drafts.body` | Texte de la réponse (modifiable) | Reply text (editable) |
| `drafts.citations` | Sources utilisées | Sources used |
| `drafts.citation.open` | Ouvrir la source {title}, version {version} | Open source {title}, version {version} |
| `drafts.citation.unavailable` | Source indisponible | Source unavailable |
| `drafts.checks` | Vérifications | Checks |
| `drafts.checks.none` | Aucun problème détecté par les contrôles automatiques. | No problem found by the automatic checks. |
| `drafts.checks.unverifiedMoney` | Montant non vérifié dans le grand livre : « {text} » | Amount not verified against the ledger: "{text}" |
| `drafts.preview.title` | Ce que le client recevra | What the customer will receive |
| `drafts.actions.approve` | Approuver et envoyer | Approve and send |
| `drafts.actions.save` | Enregistrer les modifications | Save edits |
| `drafts.actions.reject` | Rejeter | Reject |
| `drafts.actions.regenerate` | Régénérer | Regenerate |
| `drafts.read` | J'ai lu ce brouillon et vérifié les montants avec le paiement | I read this draft and checked the amounts against the payment |
| `drafts.approve.title` | Envoyer cette réponse à {name} ? | Send this reply to {name}? |
| `drafts.approve.confirm` | Envoyer la réponse | Send reply |
| `drafts.money.required` | Accord de la finance requis avant l'envoi : ce brouillon contient un engagement d'argent. | Finance co-approval required before sending: this draft commits money. |
| `drafts.money.request` | Demander l'accord de la finance | Request finance approval |
| `drafts.money.waiting` | En attente de la finance (approbation n° {id}) | Waiting for finance (approval no. {id}) |
| `drafts.recall.bar` | Réponse programmée. Annuler ({seconds} s) | Reply scheduled. Undo ({seconds} s) |
| `drafts.recall.button` | Annuler l'envoi | Undo |
| `drafts.recall.announceStart` | Réponse programmée. Vous pouvez annuler pendant 30 secondes. | Reply scheduled. You can undo for 30 seconds. |
| `drafts.recall.announce10` | Il reste 10 secondes pour annuler. | 10 seconds left to undo. |
| `drafts.recall.sent` | Envoyée à {time} | Sent at {time} |
| `drafts.recall.tooLate` | Déjà envoyée : l'annulation n'est plus possible. | Already sent: undo is no longer possible. |
| `drafts.recall.done` | Envoi annulé. Le brouillon n'a pas été envoyé. | Send cancelled. The draft was not sent. |
| `drafts.reject.title` | Pourquoi rejetez-vous ce brouillon ? | Why are you rejecting this draft? |
| `drafts.reject.reason.inaccurate` | Informations inexactes | Inaccurate |
| `drafts.reject.reason.wrong_tone` | Ton inadapté | Wrong tone |
| `drafts.reject.reason.wrong_language` | Mauvaise langue | Wrong language |
| `drafts.reject.reason.missing_info` | Informations manquantes | Missing information |
| `drafts.reject.reason.policy` | Contraire à la politique | Against policy |
| `drafts.reject.reason.other` | Autre | Other |
| `drafts.error.failed` | L'IA n'a pas pu rédiger de réponse. Rédigez à la main ou réessayez. | The AI could not draft a reply. Write by hand or try again. |
| `drafts.error.refused` | L'IA a refusé de rédiger cette réponse. | The AI declined to write this reply. |
| `drafts.error.budget` | Limite d'IA atteinte pour cette demande. Rédigez votre réponse à la main. | AI limit reached for this request. Write your reply by hand. |
| `drafts.error.versionConflict` | Ce brouillon a changé. Rechargez-le. | This draft changed. Reload it. |
| `drafts.coapprove.title` | Accord de la finance sur un brouillon | Finance co-approval of a draft |
| `drafts.coapprove.claims` | Engagements d'argent dans la réponse | Money statements in the reply |
| `drafts.coapprove.action` | Donner l'accord de la finance | Give finance approval |

**Hooks:** `useDraft(id)` polls every 2 s while `generation_status === 'generating'`; `useApproveDraft(id)` takes `{body?, version}` and an idempotency key created when the dialog opens; `useRecallDraft(id)`; `useRequestDraft(ticketId)` sends `{locale, instructions?}`.

**Pure helper:** `remainingSeconds(recallUntil: string|null, now: number): number` returns 0 when null or past.

**Operations consumed:** the 14 operationIds under Read first.

## Tasks

### Task FE-12.1: Request a draft and show generation states

- [ ] **Step 1: failing test** `DraftSection.test.tsx`:
  ```ts
  test('Rédiger avec l\'IA calls requestReplyDraft with ticket language and shows generating status', async () => {
    await user.click(screen.getByRole('button',{name:'Rédiger avec l\'IA'}));
    expect(requestBody()).toEqual({locale:'fr'}); expect(await screen.findByRole('status')).toHaveTextContent('Rédaction du brouillon en cours');
  });
  test('editor stays usable while generating (write by hand)', ...);
  test('failed, refused and budget states show plain messages and keep hand-writing', ...);
  test('ready draft links to review screen; list shows status chip per message_status', ...);
  test('superseded draft shows "Un brouillon plus récent existe"', ...);
  test('hidden for roles without requestReplyDraft', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- DraftSection`. Expected: fail.
- [ ] **Step 3: implement** `DraftSection` in the ticket detail (fe-10): button, optional "Consigne pour l'IA" textarea (max 500 characters), language select defaulting to the ticket language mapped to `fr` or `en`, `useRequestDraft`, list from `listTicketDrafts`. Generation states map from `Draft.generation_status`: `generating` (elapsed seconds, polite status), `ready` (link), `failed` (`drafts.error.failed`), `refused` (`drafts.error.refused`), `superseded`. A problem code that means budget exhausted (confirm the exact code in `api/openapi.yaml`; fallback: any 429 or `budget_blocked` text) shows `drafts.error.budget`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): request an AI reply draft with generation states`.

### Task FE-12.2: Draft review screen (label, editor, citations, checks, preview) (TC-FR-10-3 UI)

- [ ] **Step 1:** `DraftReview.test.tsx`, `CitationList.test.tsx`:
  ```ts
  test('persistent AI banner as text, switches to edited wording after edit', async () => {
    render(<DraftReview draftId={D.id}/>); expect(screen.getByText(/RÉDIGÉ PAR L'IA\. PAS ENCORE ENVOYÉ\./)).toBeVisible();
    await user.type(editor(),' merci'); await user.click(btn('Enregistrer les modifications'));
    expect(screen.getByText(/Brouillon de l'IA, modifié par vous/)).toBeVisible();
  });
  test('body is lang-tagged with draft locale', () => expect(preview()).toHaveAttribute('lang','fr'));
  test('citations list title, version, excerpt; opening shows KB version in drawer with focus handling', ...);
  test('unavailable citation shows "Source indisponible" and approve needs the read checkbox', ...);
  test('lint findings and unverified money claims listed in checks panel', ...);
  test('preview strips markdown images and links to plain text', () => expect(preview().querySelector('a,img')).toBeNull());
  test('refused or failed draft has no editable body presented as a draft', ...);
  test('language select + Régénérer respects per-ticket budget message', ...);
  ```
- [ ] **Step 2:** `npm test -- DraftReview CitationList`. Expected: fail.
- [ ] **Step 3: implement** `DraftReview` two-pane layout (stacked below 900 px): left `AiBanner` (`role="note"`, `AiBadge kind="draft"`), language select, `DraftEditor` (plain textarea, local state, `editDraft({body})` on "Enregistrer les modifications"; optimistic only for the textarea content), `CitationList` (numbered, from `citations`, each button opens `CitationDrawer` that loads `getKbVersion(article_id, version)` and shows the text via `SafeMarkdown`; drawer is a modal dialog on narrow screens and a labelled complementary region on wide screens, focus moved into it and returned on close), `ChecksPanel` (`lint_findings` as a list, `money_claims` with `verified_against_ledger === false` as warnings using `drafts.checks.unverifiedMoney`, empty case `drafts.checks.none`). Right: `CustomerPreview` showing the body as the customer would receive it, plain text, with the agent's display name as sender line from the principal; no channel claims. Status chip from `message_status`. No cost, model or token fields anywhere.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): AI draft review screen with citations and checks`.

### Task FE-12.3: Approve flow with confirmation, step-up and finance gating (TC-FR-10-1 UI, TC-FR-10-7 UI)

- [ ] **Step 1:** `ApproveDialog.test.tsx`, `DraftReview.money.test.tsx`, `e2e/drafts.spec.ts`:
  ```ts
  test('enter on the page does not approve; approve needs dialog confirm', async () => {
    render(<DraftReview draftId={D.id}/>); await user.keyboard('{Enter}'); expect(approveCalls).toBe(0);
    await user.click(btn('Approuver et envoyer')); expect(screen.getByRole('dialog',{name:/Envoyer cette réponse à Amani/})).toBeVisible(); expect(approveCalls).toBe(0);
  });
  test('double click on Envoyer la réponse sends one request with one Idempotency-Key and the version', async () => {
    await user.dblClick(btn('Envoyer la réponse')); expect(approveCalls).toHaveLength(1); expect(approveCalls[0].body.version).toBe(1);
  });
  test('dialog shows the exact final text and step-up replays once', ...);
  test('server lint findings on edited text are shown before the dialog closes (422/409 with findings)', ...);
  test('money_affecting shows co-approval notice and no direct approve', () => {
    renderMoney(); expect(screen.getByText(/Accord de la finance requis avant l'envoi/)).toBeVisible();
    expect(screen.queryByRole('button',{name:'Approuver et envoyer'})).toBeNull(); expect(btn('Demander l\'accord de la finance')).toBeVisible();
  });
  test('409 money_co_approval_required (race) switches to the notice', ...);
  test('read checkbox required when money_affecting, pii or unverified claims exist', ...);
  test('version conflict 409 shows reload message and keeps edits', ...);
  ```
  Playwright (keyboard only): open draft, tab to editor, edit, tab to Approve, Enter opens dialog, Tab to confirm, Enter sends; assert focus returns to the recall bar.
- [ ] **Step 2:** `npm test -- ApproveDialog DraftReview.money`; `npm run e2e -- drafts.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** `ApproveDialog` (native dialog; title with the customer's name from `getTicket`/customer display, shows the final text read-only, a "read" checkbox when required, confirm button "Envoyer la réponse" disabled until allowed, and `aria-disabled` while pending); on confirm `withStepUp('Envoyer une réponse', () => approveDraft({body: editedIfChanged, version}))` with `Idempotency-Key` created when the dialog opened. Success moves to the recall bar. When `money_affecting` is true, `DraftReview` hides the direct Approve and shows `drafts.money.required`; `RequestFinanceApproval` calls `createApproval({kind:'draft_co_approval', subject_type:'draft', subject_id: draftId, payload:{draft_id, version}, reason})` (reason textarea, at least 5 characters) then shows `drafts.money.waiting` with a link to `/staff/approvals/{id}`; once the approval executed (poll `getApproval` every 10 s while waiting; draft `co_approved_by` set), the normal Approve appears. Permission `ticket.ai_approve` is represented by `usePermission('approveDraft')`. There is no keyboard shortcut bound to approve.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): draft approval with confirmation, step-up and finance gating`.

### Task FE-12.4: Recall window (TC-FR-10-8 UI)

- [ ] **Step 1:** `RecallBar.test.tsx`, `countdown.test.ts`:
  ```ts
  test('remainingSeconds', () => { expect(remainingSeconds(null,0)).toBe(0); expect(remainingSeconds('2026-10-24T10:00:30Z', Date.parse('2026-10-24T10:00:02Z'))).toBe(28); });
  test('bar shows countdown aria-hidden and announces at start, 10s and expiry only', async () => {
    vi.useFakeTimers(); render(<RecallBar draft={approvedDraft(30)}/>);
    expect(liveRegion()).toHaveTextContent('Vous pouvez annuler pendant 30 secondes');
    await vi.advanceTimersByTimeAsync(20000); expect(liveRegion()).toHaveTextContent('Il reste 10 secondes');
    await vi.advanceTimersByTimeAsync(10000); expect(screen.getByText(/Envoyée à/)).toBeVisible(); expect(screen.queryByRole('button',{name:'Annuler l\'envoi'})).toBeNull();
    expect(announceCount()).toBe(3);
  });
  test('undo within window calls recallDraft and shows cancelled message', ...);
  test('server says window passed: shows "Déjà envoyée"', ...);
  test('bar is not auto-dismissed before the window ends and is reachable by keyboard', ...);
  ```
- [ ] **Step 2:** `npm test -- RecallBar countdown`. Expected: fail.
- [ ] **Step 3: implement** `RecallBar` from the draft's `recall_until` (server time source: `Draft` response; offset corrected with the response `Date` header captured by the client wrapper as `serverSkewMs`), visual countdown `aria-hidden`, a real "Annuler l'envoi" button calling `recallDraft({reason?})`; a 409 or 422 meaning the window has passed shows `drafts.recall.tooLate` and refetches; on success `drafts.recall.done`, status chip `recalled`, and the draft returns to editable state is not assumed (the message status decides). After expiry the bar shows "Envoyée à {time}" using `sent_message_id` presence or refetch.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): 30-second recall bar for approved drafts`.

### Task FE-12.5: Reject, regenerate and draft history (audit trail)

- [ ] **Step 1:** `RejectDialog.test.tsx`, `DraftHistory.test.tsx`:
  ```ts
  test('reject needs a reason from the enum list; optional note; sends rejectDraft', ...);
  test('rejected draft is read-only with reason shown', ...);
  test('regenerate disabled with budget message when budget exhausted', ...);
  test('history lists created, edited, approved by, co-approved by, sent, recalled with times', ...);
  test('own approval shows "vous", others show a short id', ...);
  ```
- [ ] **Step 2:** `npm test -- RejectDialog DraftHistory`. Expected: fail.
- [ ] **Step 3: implement** `RejectDialog` (radio group of the six reasons, optional note) calling `rejectDraft`; `Régénérer` calls `requestReplyDraft` again (the previous becomes `superseded`); `DraftHistory` renders available audit facts from the `Draft` (`created_at`, `edited`, `approved_by`, `co_approved_by`, `recall_until`, `sent_message_id`, `rejection_reason`); names are not resolved (no user lookup operation for support), so the current user is "vous" and others appear as the first 8 characters of the id with a tooltip-free text label.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): draft rejection, regeneration and history`.

### Task FE-12.6: Finance co-approval view (restricted)

- [ ] **Step 1:** `CoApproveView.test.tsx`:
  ```ts
  test('shows only money claims and payload hash status, never conversation or customer identity', async () => {
    render(<CoApproveView draftId={D.id} approvalId={A.id}/>);
    expect(await screen.findByText('Engagements d\'argent dans la réponse')).toBeVisible(); expect(screen.queryByText(/Amani/)).toBeNull();
  });
  test('hash mismatch blocks approval', ...);
  test('maker cannot co-approve (same user)', ...);
  test('co-approve sends payload_hash and optional note after step-up', ...);
  test('only finance_officer can open; support cannot', ...);
  ```
- [ ] **Step 2:** `npm test -- CoApproveView`. Expected: fail.
- [ ] **Step 3: implement** `CoApproveView`: loads `getApproval` (for `payload_hash`, maker, status) and `getDraftMoneyClaims` (restricted view), lists each claim with `MoneyText` and "vérifié dans le grand livre" or "non vérifié" text, shows "Aucune conversation ni identité client n'est affichée ici", requires the hash status "correspond" (the stored hash equals the approval's `payload_hash`), disables the button for the maker with explanation, then `withStepUp('Accord de la finance', () => coApproveDraft({payload_hash, note}))`. Success announces and links back to the approvals list (fe-15).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): finance co-approval view for money-affecting drafts`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes.
3. `npm run i18n:check` exits 0 (`drafts` equal in both locales).
4. `npm run build && npm run size` exit 0.
5. `npm run e2e -- drafts.spec.ts` passes in mock mode; with `make up` and backend slice 12, the stack run passes (approve then message appears after 30 seconds; recall inside the window cancels).
6. `npm run e2e:a11y -- --grep drafts` zero serious or critical violations in both locales; keyboard-only flow of Task FE-12.3 passes.
7. `grep -rn "model\|tokens\|cost\|duration_ms" src/features/support/components/drafts` returns nothing in rendered text (a test `drafts.nocost.test.tsx` renders every component with a draft fixture containing these fields and asserts none appears in the DOM).

## Demo script

1. As a support agent open a ticket, press "Rédiger avec l'IA" / "Draft with AI": "Rédaction du brouillon en cours" appears, then the review screen.
2. Read the banner "RÉDIGÉ PAR L'IA. PAS ENCORE ENVOYÉ." / "WRITTEN BY AI. NOT SENT YET." Open source [1]: the knowledge-base text shows in a drawer.
3. Edit one sentence, save: the banner becomes "Brouillon de l'IA, modifié par vous" / "AI draft, edited by you".
4. Press "Approuver et envoyer" / "Approve and send": the dialog "Envoyer cette réponse à ... ?" shows the final text; confirm the step-up; the bar "Réponse programmée. Annuler (28 s)" / "Reply scheduled. Undo (28 s)" appears and a screen reader announces it once.
5. Press "Annuler l'envoi" / "Undo" inside 30 seconds: "Envoi annulé." / "Send cancelled." Repeat and wait: "Envoyée à 10:42" / "Sent at 10:42".
6. Use a ticket whose draft promises a refund: the notice "Accord de la finance requis avant l'envoi" shows and only "Demander l'accord de la finance" is offered; as a finance officer open the co-approval page: only the money statements show.

## Done criteria

- Requirements: FR-10 (review, edit, approve, reject, recall, co-approval), FR-16 (draft language), NFR-08 (human gate in the UI), NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8: TC-FR-10-6 (agent reviews, edits, approves and sends with the audit trail visible), TC-FR-10-7 (money-affecting draft cannot be sent without co-approval; re-lint on edited text), TC-FR-10-8 (recall within 30 seconds and refused after), TC-FR-10-1 (UI never sends without the confirmation dialog), TC-FR-10-3 (links and images from model text are not rendered), TC-FR-10-4 (unavailable citation shown as unavailable), TC-NFR-08-5 (refusal shown neutrally and never as a draft).

## Plan notes for the tech lead

- **Co-approval needs `finance_lead`:** `coApproveDraft` carries `x-requires-attribute: finance_lead` in `api/openapi.yaml`; only finance officers with that attribute see the action on the co-approval page (generated `can()`, fe-00).
- **Co-approval modelling:** `api/openapi.yaml` has both `coApproveDraft` (finance, needs `payload_hash`) and the approval kind `draft_co_approval` (D-50). The plan uses the approval to request and notify, and `coApproveDraft` as the executing action on the finance page. The exact `payload` of the approval is not specified; flagged as open.
- **Names in history:** support agents cannot read the user list, so approvers other than the current user appear as short ids.
- **Channel preview:** `docs/08` 3.4 shows "Channel: SMS + portal" and SMS segment count; the `Draft` schema has no such fields, so they are not shown.
- **Undo for urgent tickets:** the earlier idea of a zero-delay option is not built; the window is the server's `recall_until`.
- **Skew:** the recall countdown uses the server's `recall_until` corrected by the `Date` response header to avoid wrong countdowns on phones with bad clocks.
