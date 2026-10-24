# Frontend slice 10: Support desk (staff inbox, customer tickets) and knowledge base editor

- **Goal:** support agents work a ticket queue with triage, summaries and incident linking; customers raise and follow tickets in the portal with attachments; staff write, version and request publication of knowledge-base articles (publication needs a second approver). AI triage appears as a marked, overridable suggestion; nothing AI-written reaches a customer (reply drafting is slice 12).
- **Depends on:** backend slices 10 (support desk, KB) and 11 (triage and summaries; the screens render an "unavailable" state when the AI endpoints answer 404 or `failed`); frontend slices fe-00, fe-01, fe-02, fe-04 (portal shell, payment link).
- **Requirements served:** FR-08, FR-09 (triage display and override), FR-11 (summaries), FR-17, FR-16, NFR-10, NFR-11.
- **Size:** L, 9 days.
- **Spikes or open items:** (1) AI disclosure wording is pending legal (D-35, open); default sentence in Interfaces, kept as translation keys. (2) The queue filter "needs a look" (low-confidence triage) has no documented API parameter; default: not built, the triage panel shows confidence in words.

## Read first

- `docs/08-frontend-ux.md#3.2 Support ticket thread (customer and agent view)`
- `docs/08-frontend-ux.md#3.3 Staff: ticket queue with AI triage panel`
- `docs/08-frontend-ux.md#3.8 KB editor (FR-17)`
- `docs/08-frontend-ux.md#3.15 Incident linking (FR-07, FR-08)`
- `docs/08-frontend-ux.md#4. AI UX rules` (labelling, uncertainty, summaries, feedback rows)
- `docs/08-frontend-ux.md#5.5 Optimistic updates policy`
- `docs/08-frontend-ux.md#7.3 Screen-reader semantics for tables and live regions`
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)`
- `docs/00-shared-context.md` section 7: D-14, D-30, D-32, D-35, D-36, section 7.2 (triage vocabulary)
- OperationIds: `listTickets`, `createTicket`, `getTicket`, `updateTicket`, `listTicketMessages`, `createTicketMessage`, `uploadAttachment`, `listTicketAttachments`, `getAttachmentLink`, `listIncidents`, `createIncident`, `getIncident`, `updateIncident`, `linkIncidentTickets`, `getTicketTriage`, `requestTicketTriage`, `overrideTicketTriage`, `requestSummary`, `listSummaries`, `getSummary`, `listKbArticles`, `createKbArticle`, `getKbArticle`, `updateKbArticle`, `listKbVersions`, `getKbVersion`, `publishKbArticle`, `archiveKbArticle`, `getCustomer`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page.
- Access token in memory only; the public help pages (`/help/[slug]`) are server components using `src/api/server.ts` and only public endpoints.
- Budgets: portal first load at most 130 KB (the customer ticket pages add no Markdown library; plain text only), staff at most 250 KB; the KB editor preview and diff load with `next/dynamic`.
- next-intl `/fr` and `/en`; French first.
- WCAG 2.2 AA: queue is a native table with real links; no row-click-only; AI content carries `AiBadge` with text; customer message textarea shows a live character count announced at 4,500 and 5,000.
- Generated client only; updates send `If-Match` with `version`.
- Limits (D-30): message body 5,000 characters; at most 3 attachments of at most 5 MB each; types jpg, png, pdf only; checked in the browser and shown as field errors; the server is the authority.
- All user, AI and KB text is untrusted: rendered as text, or by the single `SafeMarkdown` component (raw HTML disabled, links `rel="noopener noreferrer nofollow"`); no `dangerouslySetInnerHTML` elsewhere.
- Never show model name, tokens, cost or latency (summaries carry a `model` field: do not render it).
- Navigation and buttons from `permissions.generated.ts`: tickets are `support_agent` (full) and `operations_engineer` (read, incident linking); the administrator and finance have no tickets; KB editing is `support_agent` and `administrator`.
- Do not edit frozen files.

## Review focus

1. A customer exceeds 5,000 characters or attaches a fourth or oversized file and loses the typed text: `MessageComposer.test.tsx` "limits shown as errors and text kept" (Task FE-10.6).
2. AI triage looks like a human decision or cannot be overridden: `TriagePanel.test.tsx` "marked as AI suggestion and override records reason" (Task FE-10.3).
3. Linking 101 tickets or a partial result is hidden: `LinkIncidentDialog.test.tsx` "101st checkbox disabled; partial result listed" (Task FE-10.5).
4. A KB author publishes alone, or an unsaved edit is lost: `KbEditor.test.tsx` "publish creates approval and says pending" and "beforeunload guard" (Task FE-10.7).
5. HTML or script in a ticket body or KB article executes: `SafeMarkdown.test.tsx` and `ThreadView.test.tsx` "script text rendered inert" (Task FE-10.2).

## Files

- `frontend/src/features/support/keys.ts`, `schemas/message.ts`, `schemas/attachments.ts`, `schemas/kb.ts`.
- `frontend/src/features/support/hooks/useTickets.ts`, `useTicket.ts`, `useMessages.ts`, `useSendMessage.ts`, `useAttachments.ts`, `useTriage.ts`, `useSummary.ts`, `useIncidents.ts`, `useLinkIncident.ts`, `useKb.ts`.
- `frontend/src/features/support/components/TicketQueue.tsx`, `TicketFilters.tsx`, `TicketHeader.tsx`, `ThreadView.tsx`, `MessageComposer.tsx`, `AttachmentPicker.tsx`, `AttachmentList.tsx`, `TriagePanel.tsx`, `SummaryPanel.tsx`, `IncidentList.tsx`, `IncidentDetail.tsx`, `LinkIncidentDialog.tsx`, `KbList.tsx`, `KbEditor.tsx`, `KbVersions.tsx`, `KbDiff.tsx`, `PublishRequestDialog.tsx`.
- `frontend/src/features/support/components/portal/MyTickets.tsx`, `NewTicketForm.tsx`, `MyThread.tsx`, `AiDisclosure.tsx`.
- `frontend/src/shared/ui/SafeMarkdown.tsx`.
- `frontend/src/app/[locale]/(staff)/staff/tickets/page.tsx`, `tickets/[id]/page.tsx`, `incidents/page.tsx`, `incidents/[id]/page.tsx`, `kb/page.tsx`, `kb/new/page.tsx`, `kb/[id]/edit/page.tsx`.
- `frontend/src/app/[locale]/(portal)/portal/support/page.tsx`, `support/new/page.tsx`, `support/[ticketId]/page.tsx`.
- `frontend/src/app/[locale]/(public)/help/[slug]/page.tsx` (modify: public KB articles from public endpoints; empty state if none).
- `frontend/src/i18n/messages/{fr,en}/tickets.json`, `kb.json`.
- Tests next to components; `frontend/e2e/support.spec.ts`, `kb.spec.ts`.

## Interfaces

**Routes (staff):** `/staff/tickets`, `/staff/tickets/[id]`, `/staff/incidents`, `/staff/incidents/[id]`, `/staff/kb`, `/staff/kb/new`, `/staff/kb/[id]/edit`. **Portal:** `/portal/support`, `/portal/support/new?payment=<topUpId>`, `/portal/support/[ticketId]`.

**Namespaces `tickets` and `kb` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `tickets.queue.title` | Demandes d'assistance | Support requests |
| `tickets.queue.caption` | Demandes ouvertes : {count, plural, =0 {aucun résultat} one {# résultat} other {# résultats}} | Open requests: {count, plural, =0 {no results} one {# result} other {# results}} |
| `tickets.queue.empty` | Aucune demande ouverte. Bon travail. | No open requests. Well done. |
| `tickets.status.open` | Ouverte | Open |
| `tickets.status.in_progress` | En cours | In progress |
| `tickets.status.pending_customer` | En attente du client | Waiting for customer |
| `tickets.status.resolved` | Résolue | Resolved |
| `tickets.status.closed` | Clôturée | Closed |
| `tickets.urgency.critical` | Critique | Critical |
| `tickets.urgency.high` | Haute | High |
| `tickets.urgency.normal` | Normale | Normal |
| `tickets.urgency.low` | Basse | Low |
| `tickets.category.billing_payment` | Facturation et paiement | Billing and payment |
| `tickets.category.no_power` | Pas de courant | No power |
| `tickets.category.meter_fault` | Panne de compteur | Meter fault |
| `tickets.category.tariff_question` | Question sur les tarifs | Tariff question |
| `tickets.category.account_change` | Changement de compte | Account change |
| `tickets.category.complaint` | Réclamation | Complaint |
| `tickets.category.safety_hazard` | Danger pour la sécurité | Safety hazard |
| `tickets.category.other` | Autre | Other |
| `tickets.triage.title` | Suggestion de l'IA | AI suggestion |
| `tickets.triage.applied` | Appliquée automatiquement. Vous pouvez la modifier. | Applied automatically. You can change it. |
| `tickets.triage.confidence.high` | Confiance élevée | High confidence |
| `tickets.triage.confidence.medium` | Confiance moyenne | Medium confidence |
| `tickets.triage.confidence.low` | Confiance faible : vérifiez attentivement | Low confidence: check carefully |
| `tickets.triage.override` | Modifier | Change |
| `tickets.triage.overrideReason` | Motif de la modification (3 caractères minimum) | Reason for the change (3 characters minimum) |
| `tickets.triage.unavailable` | Le tri par l'IA est indisponible. Triez à la main. | AI triage is unavailable. Please triage by hand. |
| `tickets.triage.pii` | Données personnelles détectées : {flags} | Personal data found: {flags} |
| `tickets.triage.needsHuman` | Cette demande doit être vue par une personne maintenant. | A person needs to look at this request now. |
| `tickets.summary.title` | Résumé de l'IA | AI summary |
| `tickets.summary.stale` | Généré à {time} ; {count, plural, one {# message plus récent} other {# messages plus récents}} | Generated at {time}; {count, plural, one {# newer message} other {# newer messages}} |
| `tickets.summary.generate` | Générer un résumé | Generate a summary |
| `tickets.thread.send` | Envoyer | Send |
| `tickets.thread.counter` | {count} / 5 000 caractères | {count} / 5,000 characters |
| `tickets.attach.add` | Joindre un fichier (3 maximum, 5 Mo chacun : jpg, png, pdf) | Attach a file (up to 3, 5 MB each: jpg, png, pdf) |
| `tickets.attach.tooMany` | Vous pouvez joindre 3 fichiers au maximum. | You can attach at most 3 files. |
| `tickets.attach.tooBig` | « {name} » dépasse 5 Mo. | "{name}" is larger than 5 MB. |
| `tickets.attach.badType` | « {name} » n'est pas un fichier jpg, png ou pdf. | "{name}" is not a jpg, png or pdf file. |
| `tickets.attach.scanning` | Analyse du fichier en cours | Scanning file |
| `tickets.attach.rejected` | Fichier refusé par l'analyse de sécurité | File rejected by the security scan |
| `tickets.portal.disclosure` | Notre personnel peut utiliser des outils d'IA pour préparer les réponses. Une personne relit toujours une réponse avant son envoi. | Our staff may use AI tools to prepare replies. A person always reviews a reply before it is sent. |
| `tickets.portal.new` | Nouvelle demande d'assistance | New support request |
| `tickets.portal.empty` | Vous n'avez aucune demande. Besoin d'aide ? Ouvrez une demande. | You have no requests. Need help? Open a request. |
| `tickets.incident.link` | Lier à un incident | Link to an incident |
| `tickets.incident.limit` | {count} sur 100 sélectionnées | {count} of 100 selected |
| `tickets.incident.result` | {linked} liées, {skipped} ignorées, {failed} en échec | {linked} linked, {skipped} skipped, {failed} failed |
| `kb.title` | Base de connaissances | Knowledge base |
| `kb.status.draft` | Brouillon | Draft |
| `kb.status.published` | Publié | Published |
| `kb.status.archived` | Archivé | Archived |
| `kb.save` | Enregistrer le brouillon | Save draft |
| `kb.requestPublish` | Demander la publication | Request publication |
| `kb.publish.pending` | Publication demandée (approbation n° {id}). Un deuxième approbateur doit la valider. | Publication requested (approval no. {id}). A second approver must confirm it. |
| `kb.unsaved` | Modifications non enregistrées | Unsaved changes |
| `kb.frOutOfDate` | La version française est en retard sur l'anglaise. | The French version is behind the English one. |

**Hooks:** `useTickets(filters)`; `useTicket(id)`; `useMessages(ticketId)`; `useSendMessage(ticketId)` (one `Idempotency-Key` per intent); `useTriage(ticketId)` (`getTicketTriage`, polls 3 s while `status === 'pending'`); `useOverrideTriage(ticketId)`; `useSummary(ticketId)`; `useLinkIncident(incidentId)`; `useKbArticle(id)`; `usePublishKb(id)`.

**Components:** `TriagePanel({ticketId})`, `SummaryPanel({ticketId})`, `LinkIncidentDialog({ticketIds})`, `KbEditor({article?})`, `MessageComposer({ticketId, role:'customer'|'agent'})`, `AttachmentPicker({value,onChange})` with `validateAttachments(files): {ok: File[]; errors: string[]}`, `SafeMarkdown({text})`.

**Operations consumed:** the 29 operationIds under Read first.

## Tasks

### Task FE-10.1: Staff ticket queue

- [ ] **Step 1: failing test** `TicketQueue.test.tsx`:
  ```ts
  test('native table with caption count, sortable headers and subject links', async () => {
    mockTickets([T1,T2]); render(<TicketQueue/>);
    expect(await screen.findByRole('table',{name:/Demandes ouvertes : 2 résultats/})).toBeVisible();
    expect(screen.getByRole('link',{name:T1.subject})).toHaveAttribute('href','/fr/staff/tickets/'+T1.id);
    expect(screen.getAllByRole('columnheader')[2]).toHaveAttribute('aria-sort');
  });
  test('filters apply on Appliquer; filters live in URL', ...);
  test('AI suggestion badge shown on category when triage_run_id present', ...);
  test('empty state, error with request id, offline banner, partial', ...);
  test('operations engineer sees list read-only without assign actions', ...);
  test('administrator and finance cannot open', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- TicketQueue`. Expected: fail.
- [ ] **Step 3: implement** `TicketQueue` with `DataTable` (number, subject link, urgency badge text, category label with `AiBadge kind="suggestion"` when `triage_run_id` is set and no override, status, assignee, age), filters (status, urgency, category, language, assignee `me`) with Apply, cursor links, no infinite scroll. Row triage panel opens on the ticket page (not beside the table) to keep one navigation model. Mutations: "Prendre" (assign to me) via `updateTicket({assignee_id}, If-Match)` is optimistic with rollback.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): staff ticket queue`.

### Task FE-10.2: Ticket detail, thread, reply and attachments (TC-FR-08-3)

- [ ] **Step 1:** `ThreadView.test.tsx`, `MessageComposer.test.tsx`, `e2e/support.spec.ts`:
  ```ts
  test('script text rendered inert', () => { expect(screen.getByText('<script>alert(1)</script>')).toBeVisible(); expect(document.querySelector('script[data-x]')).toBeNull(); });
  test('messages show author type, direction and locale lang attribute', () => expect(screen.getByText('Bonjour')).toHaveAttribute('lang','fr'));
  test('agent reply posts outbound message and clears composer', ...);
  test('status change to resolved sends If-Match and shows conflict dialog on 409', ...);
  test('attachment link fetched only when scan_status clean; infected shows rejected text', ...);
  ```
  Playwright: "support agent opens, assigns and resolves a ticket" with axe.
- [ ] **Step 2:** `npm test -- ThreadView MessageComposer`; `npm run e2e -- support.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** `/staff/tickets/[id]` (client): `TicketHeader` (subject, status select, urgency, assignee, category via `updateTicket` with `If-Match`), `ThreadView` (messages by time; `author_type` `ai_draft` messages are never shown here, they belong to the draft screen in fe-12; bodies as text with `white-space: pre-wrap`; `lang` from message locale), `MessageComposer` for agents (reply body up to 5,000 characters, `createTicketMessage` with `direction: 'outbound'` and an `Idempotency-Key`; internal notes use `direction: 'internal'` if the generated `MessageCreate` allows it), `AttachmentList` (`listTicketAttachments`; link via `getAttachmentLink` on click, opened in a new tab with `rel="noopener noreferrer"`; shows `tickets.attach.scanning` while `pending`). Only `support_agent` sees the composer and status controls.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): ticket detail with thread, reply and attachments`.

### Task FE-10.3: Triage panel with override (TC-FR-09-6 UI)

- [ ] **Step 1:** `TriagePanel.test.tsx`:
  ```ts
  test('marked as AI suggestion, applied automatically, confidence in words', async () => {
    mockTriage({status:'completed',category:'no_power',urgency:'high',confidence:0.91,applied:true,pii_flags:['phone'],override:null});
    render(<TriagePanel ticketId={ID}/>);
    expect(await screen.findByLabelText('Généré par IA')).toBeVisible();
    expect(screen.getByText('Appliquée automatiquement. Vous pouvez la modifier.')).toBeVisible();
    expect(screen.getByText('Confiance élevée')).toBeVisible(); expect(screen.queryByText('0.91')).toBeNull();
  });
  test('low confidence adds "vérifiez attentivement"', ...);
  test('override sends category, urgency and reason, then shows overridden marker with who and when', ...);
  test('reason shorter than 3 characters blocks submit', ...);
  test('pending shows status region "Tri en cours"; failed or refused shows manual triage message', ...);
  test('needs_human_now shows banner text only', ...);
  test('no override control for operations_engineer', ...);
  ```
- [ ] **Step 2:** `npm test -- TriagePanel`. Expected: fail.
- [ ] **Step 3: implement** `TriagePanel` using `useTriage`: when `status === 'pending'` poll every 3 s with a polite status "Tri par l'IA en cours"; when completed render category and urgency labels (vocabulary from section 7.2), language, PII flags as text, confidence in words (`>= 0.8` high, `>= 0.5` medium, else low; numbers never shown to non-admin), `AiBadge kind="suggestion"`. "Modifier" opens an inline form (selects of the canonical enums, reason) calling `overrideTicketTriage`; success refetches the ticket and triage, marks "Modifié par {name} le {date}". `requestTicketTriage` is offered as "Relancer le tri" only when `failed` or `refused`, to `support_agent`. Never changes the ticket urgency downward silently: the panel shows the effective ticket urgency from `getTicket`, and a warning when the override lowers urgency from `critical` or `high` (`tickets.triage.lowering`: fr "Vous abaissez l'urgence. Confirmez avec le motif.", en "You are lowering the urgency. Confirm with the reason.").
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): AI triage panel with recorded override`.

### Task FE-10.4: Summary panel (FR-11)

- [ ] **Step 1:** `SummaryPanel.test.tsx`:
  ```ts
  test('shows AI summary with badge, bullets as list, never renders model', () => { expect(screen.queryByText(/claude/i)).toBeNull(); });
  test('stale note when source_message_count < current message count', ...);
  test('generate button calls requestSummary with ticket subject and locale', ...);
  test('failed or refused shows nothing but a quiet unavailable message; ticket flow continues', ...);
  test('summary text is internal: no copy-to-customer button', () => expect(screen.queryByRole('button',{name:/envoyer au client/i})).toBeNull());
  ```
- [ ] **Step 2:** `npm test -- SummaryPanel`. Expected: fail.
- [ ] **Step 3: implement** collapsible region labelled `tickets.summary.title` above the thread, using `listSummaries({subject_id})` and `requestSummary({subject_type:'ticket', subject_id, locale})`; polls while `pending`; renders `text` and `bullets` via plain text; staleness note compares `source_message_count` with `useMessages` length; feedback buttons are not built (no operation).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): AI ticket summary panel`.

### Task FE-10.5: Incident linking (up to 100 tickets)

- [ ] **Step 1:** `LinkIncidentDialog.test.tsx`, `IncidentList.test.tsx`:
  ```ts
  test('101st checkbox is disabled with explanation and counter announced', async () => {
    renderQueueWithTickets(150); await selectFirst(100); expect(screen.getByLabelText(/Sélectionner la demande 101/)).toBeDisabled();
    expect(liveRegion()).toHaveTextContent('100 sur 100 sélectionnées');
  });
  test('new incident then link; result summary lists linked, skipped, failed', async () => {
    server.use(link200({linked:37,skipped:2,failed:0})); await submit(); expect(screen.getByText('37 liées, 2 ignorées, 0 en échec')).toBeVisible();
  });
  test('existing incident search selects and links', ...);
  test('incident page lists linked tickets, site, status select with If-Match', ...);
  test('operations engineer can link and update incidents; finance cannot see', ...);
  ```
- [ ] **Step 2:** `npm test -- LinkIncidentDialog IncidentList`. Expected: fail.
- [ ] **Step 3: implement** a checkbox column on `TicketQueue` (label "Sélectionner la demande {number}"), a "Lier à un incident" button, `LinkIncidentDialog` (search `listIncidents` or create with title, severity, optional site via `createIncident`; then `linkIncidentTickets({ticket_ids})` with at most 100 ids); partial result text from the response and the error detail; `IncidentList`, `IncidentDetail` (`getIncident`, `updateIncident` status and severity). No mass reply is offered.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): incident linking for up to 100 tickets`.

### Task FE-10.6: Customer tickets in the portal (TC-FR-08-5 UI)

- [ ] **Step 1:** `NewTicketForm.test.tsx`, `AttachmentPicker.test.tsx`, `MyThread.test.tsx`:
  ```ts
  test('validateAttachments rejects 4th file, over 5 MB and wrong type', () => {
    const r = validateAttachments([f('a.jpg',1e6),f('b.png',1e6),f('c.pdf',1e6),f('d.jpg',1e6)]); expect(r.errors).toContain('tickets.attach.tooMany');
    expect(validateAttachments([f('big.png',5*1024*1024+1)]).errors[0]).toBe('tickets.attach.tooBig');
    expect(validateAttachments([f('x.exe',10)]).errors[0]).toBe('tickets.attach.badType');
  });
  test('limits shown as errors and text kept', async () => { await type(5001chars); expect(screen.getByRole('alert')).toHaveTextContent('5 000'); expect(textarea).toHaveValue(typed); });
  test('character counter announced at 4,500 and 5,000', ...);
  test('?payment=<id> prefills subject and body reference without asking twice', ...);
  test('AI disclosure sentence shown on new ticket page', () => expect(screen.getByText(/Notre personnel peut utiliser des outils d'IA/)).toBeVisible());
  test('offline: reply kept, "Envoyer quand la connexion revient" button; no silent background send', ...);
  test('thread shows only agent and customer messages, no AI draft or label', ...);
  ```
- [ ] **Step 2:** `npm test -- NewTicketForm AttachmentPicker MyThread`. Expected: fail.
- [ ] **Step 3: implement** the portal pages with `MyTickets` (list, empty state with button), `NewTicketForm` (subject 3 to 120 characters, body 1 to 5,000, optional attachments via `AttachmentPicker`, optional `meter_id`, payment prefill from `?payment=` calling `getTopUp` for reference text, `createTicket` with `channel: 'portal'`, `locale` from route, `Idempotency-Key`), `MyThread` (`listTicketMessages`, reply composer with the same limits; draft text kept in memory and `sessionStorage` key `hub-draft:support:<id>`; offline shows the button that the user presses when online, never Background Sync), `AiDisclosure` (key `tickets.portal.disclosure`; wording pending legal **[open]**). Attachments upload through `uploadAttachment` as multipart after ticket creation, one request per file with progress text; photos are downscaled in the browser (canvas, longest side 1280 px, JPEG quality 0.7) before upload; PDFs untouched. Customers never see the `ai_draft` author type.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): customer support tickets with attachment limits and AI disclosure`.

### Task FE-10.7: Knowledge base editor with versions and publication request (TC-FR-17-4 UI)

- [ ] **Step 1:** `KbEditor.test.tsx`, `SafeMarkdown.test.tsx`, `e2e/kb.spec.ts`:
  ```ts
  test('publish creates approval and says pending, article stays draft', async () => {
    server.use(publish202(APPROVAL)); await requestPublish(); expect(await screen.findByRole('status')).toHaveTextContent('Publication demandée (approbation n°');
    expect(screen.getByText('Brouillon')).toBeVisible();
  });
  test('save sends If-Match; conflict dialog on 409', ...);
  test('beforeunload guard while dirty and "Modifications non enregistrées" status', ...);
  test('preview uses SafeMarkdown: raw html and javascript: links inert', ...);
  test('versions list and diff view show two versions', ...);
  test('FR-behind-EN notice when translation group has newer EN version', ...);
  test('administrator and support can edit; finance and operations get no KB nav entry', ...);
  test('step-up dialog replays publication request once', ...);
  ```
- [ ] **Step 2:** `npm test -- KbEditor SafeMarkdown`; `npm run e2e -- kb.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** `KbList` (filters status, locale), `KbEditor` (title, slug (create only), locale, tags, body as plain textarea with a Preview tab through `SafeMarkdown`, change note, save via `updateKbArticle` with `If-Match`; create via `createKbArticle`), `KbVersions` (`listKbVersions`, `getKbVersion`) with a dynamically imported `KbDiff` (line diff, text only), `PublishRequestDialog` (shows exact text, runs `withStepUp`, calls `publishKbArticle`, handles 202 through `handleApprovalResult`; records nothing as published; shows `publish_approval_id` link to `/staff/approvals/{id}`), archive via `archiveKbArticle` with confirmation. `SafeMarkdown` uses a Markdown parser configured with raw HTML disabled plus DOMPurify as a second layer, loaded only where used (not in the portal).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): knowledge base editor with versions and publication request`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes.
3. `npm run i18n:check` exits 0 (`tickets`, `kb` equal in both locales).
4. `npm run build && npm run size` exit 0: portal support routes at most 130 KB, staff routes at most 250 KB.
5. `npm run e2e -- support.spec.ts kb.spec.ts` pass in mock mode; with `make up` and backend slices 10 and 11, `E2E_MODE=stack` runs of the same specs pass.
6. `npm run e2e:a11y -- --grep "support|tickets|kb"` zero serious or critical violations in both locales and at 360 px for the portal routes.
7. `grep -rn "dangerouslySetInnerHTML" src` lists only `src/shared/ui/SafeMarkdown.tsx`.

## Demo script

1. As a customer open `/fr/portal/support/new`: the sentence "Notre personnel peut utiliser des outils d'IA pour préparer les réponses..." is shown. EN: "Our staff may use AI tools...".
2. Write a message, attach two photos; try a fourth file: "Vous pouvez joindre 3 fichiers au maximum." / "You can attach at most 3 files."
3. As a support agent open `/fr/staff/tickets`: the new request is in the queue with an "AI suggestion" marker on its category after triage.
4. Open it: the panel "Suggestion de l'IA" says "Appliquée automatiquement. Vous pouvez la modifier." Change the category with a reason: it now reads "Modifié par ...".
5. Press "Générer un résumé" / "Generate a summary": the summary appears with the AI badge.
6. Select five tickets, "Lier à un incident" / "Link to an incident", create an incident: "5 liées, 0 ignorées, 0 en échec" / "5 linked, 0 skipped, 0 failed".
7. Open `/fr/staff/kb/new`, write an article, "Demander la publication" / "Request publication": "Publication demandée (approbation n° …)". The article stays "Brouillon" / "Draft".

## Done criteria

- Requirements: FR-08, FR-09 (triage display and override), FR-11 (summaries), FR-17 (editor and publication request), FR-16, NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8: TC-FR-08-3 (agent opens, assigns and resolves in the UI), TC-FR-08-5 (attachment and length limits shown), TC-FR-09-6 (low-confidence shown in words; paging banner informational), TC-FR-17-4 (publication by the author alone is not completed, approval pending), TC-FR-17-1 (versions listed), TC-FR-15-15 (administrator has no tickets).

## Plan notes for the tech lead

- **Triage confirmation:** `docs/08` 3.3 and D-32 describe a one-click "Looks right" that records a confirmation; the API only has `overrideTicketTriage`. Not built; the AI marker stays until an override. Add a confirm operation or drop the wording.
- **Paging confirmation:** `docs/08` 3.3 describes staff confirmation for on-call paging above a threshold; no operation exists, so the panel shows the banner only.
- **Low-confidence queue filter:** no `listTickets` parameter documented; not built.
- **`ai_draft` messages in the thread:** `TicketMessage.author_type` includes `ai_draft`; the thread view hides them and fe-12 shows them in the draft screen.
- **Summary `model` field:** present in `Summary`; never rendered (cost and model are not shown outside the admin cost page, which is also not available to the administrator under `permissions.yaml`).
- **KB publishers:** `permissions.yaml` allows `support_agent` and `administrator` to call `publishKbArticle`; D-36 requires a second approver, so the UI treats the call as a request that returns an approval.
- **Public help:** `docs/03` places public KB articles at `(public)/help/[slug]`; no public KB operationId exists in `api/openapi.yaml`, so that page shows its empty state until a public endpoint is defined.
