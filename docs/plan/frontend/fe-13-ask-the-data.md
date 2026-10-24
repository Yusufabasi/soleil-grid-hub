# Frontend slice 13: Ask the data

- **Goal:** support agents, finance officers and operations engineers ask questions in French or English, always see the exact SQL that ran and a plain restatement of the question, read the result in an accessible table, get clear role-limited and failure messages, and reopen their own history. The administrator has no such screen.
- **Depends on:** backend slice 13 (ask the data; needs the AI gateway and `analytics_*` roles); frontend slices fe-00, fe-01.
- **Requirements served:** FR-12, FR-16 (question and explanation language), NFR-02 (8 s budget perceived), NFR-08, NFR-10, NFR-11.
- **Size:** M, 4 days.
- **Spikes or open items:** none. Default for the history retention shown to users: whatever the API returns (the UI does not promise a period); `docs/08` mentions 90 days, which is not in the API.

## Read first

- `docs/08-frontend-ux.md#3.5 Ask the data (FR-12)` (wireframe, SQL always shown, refusal wording, state table)
- `docs/08-frontend-ux.md#4. AI UX rules` (labelling, refusals, failures, cost rule)
- `docs/08-frontend-ux.md#7.5 Motion, forms and errors` (accessible result tables)
- `docs/08-frontend-ux.md#7.3 Screen-reader semantics for tables and live regions` (Ask the data row)
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)` (Client state: AbortSignal for cancel)
- `docs/00-shared-context.md` section 7: D-14, D-24, D-31
- OperationIds: `askTheData`, `listAskHistory`, `getAskQuestion`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page.
- Access token in memory only. The question text is sent only to `askTheData`; it is never put in URLs or logs.
- Budgets: staff first load at most 250 KB; no SQL syntax-highlighting library (plain monospace); CSV export is hand-written.
- next-intl `/fr` and `/en`; French first; the request sends `locale` from the active route.
- WCAG 2.2 AA: result table native with caption equal to the question and `scope` headers, numeric columns right aligned with tabular figures, wide table inside a focusable scroll region; status announcements through the live region.
- Generated client only; `AskQuestion.rows` is `unknown[][]` and every cell is rendered as text.
- The SQL is always visible whenever it exists; never behind a toggle. The UI never shows `model`, `duration_ms` or any cost field (the administrator, the only role that could see cost, has no access to this screen; D-14, D-24).
- Query rows are never sent anywhere except the browser's own CSV export; no "explain these rows" feature exists (D-31).
- The administrator has no route and no navigation entry; support and operations see aggregate views only, finance pseudonymous rows; the UI does not try to hide columns itself.
- Outer time budget 8 s: the UI shows a timeout message after 10 s of waiting at most and lets the user cancel at any time.
- Do not edit frozen files.

## Review focus

1. The result appears without its SQL, or the SQL is collapsed: `AskResult.test.tsx` "SQL block visible with accessible name whenever sql is present" (Task FE-13.2).
2. A refused or role-limited question looks like an error and reveals what exists in out-of-scope data: `AskStates.test.tsx` "role-limited message names the role limit and offers allowed examples, no table names" (Task FE-13.3).
3. A cell value containing HTML or a spreadsheet formula executes: `AskResult.test.tsx` "cells inert" and `csv.test.ts` "formula neutralisation" (Task FE-13.2).
4. The user waits forever on a slow query: `AskForm.test.tsx` "cancel aborts the request; 10 s shows timeout message" (Task FE-13.1).
5. The administrator reaches the page by URL: `guards` test and `e2e/ask.spec.ts` "administrator sees no entry and no access page" (Task FE-13.3).

## Files

- `frontend/src/features/analytics/keys.ts` (modify), `hooks/useAsk.ts`, `hooks/useAskHistory.ts`, `hooks/useAskQuestion.ts`.
- `frontend/src/features/analytics/schemas/ask.ts` (question 3 to 1,000 characters).
- `frontend/src/features/analytics/components/AskForm.tsx`, `ExampleQuestions.tsx`, `AskResult.tsx`, `SqlBlock.tsx`, `ResultTable.tsx`, `AskStates.tsx`, `AskHistory.tsx`.
- `frontend/src/features/analytics/lib/csv.ts` (`toCsv(columns, rows)` with formula neutralisation).
- `frontend/src/app/[locale]/(staff)/staff/ask/page.tsx`, `ask/[id]/page.tsx`.
- `frontend/src/i18n/messages/{fr,en}/ask.json`.
- Tests next to code; `frontend/e2e/ask.spec.ts`.

## Interfaces

**Routes (staff):** `/staff/ask` (form, latest result, history), `/staff/ask/[id]` (reopen one question).

**Namespace `ask` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `ask.title` | Interroger les données | Ask the data |
| `ask.scope.support` | Périmètre : Support (lecture seule, données agrégées) | Scope: Support (read-only, aggregated data) |
| `ask.scope.finance` | Périmètre : Finance (lecture seule, données pseudonymisées) | Scope: Finance (read-only, pseudonymous data) |
| `ask.scope.ops` | Périmètre : Opérations (lecture seule, données agrégées) | Scope: Operations (read-only, aggregated data) |
| `ask.question` | Votre question | Your question |
| `ask.submit` | Demander | Ask |
| `ask.cancel` | Annuler | Cancel |
| `ask.examples` | Exemples | Examples |
| `ask.example.support.1` | Combien de demandes ouvertes par catégorie ? | How many open requests per category? |
| `ask.example.support.2` | Quel est le délai moyen de première réponse cette semaine ? | What is the average first response time this week? |
| `ask.example.finance.1` | Revenus par site ce mois-ci | Revenue by site this month |
| `ask.example.finance.2` | Les 10 dernières recharges échouées | The last 10 failed top-ups |
| `ask.example.ops.1` | Combien de compteurs hors ligne par site ? | How many meters are offline per site? |
| `ask.example.ops.2` | Quels sites ont le plus d'alertes ouvertes ? | Which sites have the most open alerts? |
| `ask.ai.label` | SQL écrit par l'IA. Exécuté en lecture seule, limité à votre rôle. | SQL written by AI. Run read-only, limited to your role. |
| `ask.sql.title` | Requête exécutée (lecture seule) | Query that ran (read-only) |
| `ask.sql.copy` | Copier la requête | Copy query |
| `ask.interpretation` | Interprétation : {text} | Interpretation: {text} |
| `ask.rows` | {count, plural, =0 {Aucune ligne} one {# ligne} other {# lignes}} | {count, plural, =0 {No rows} one {# row} other {# rows}} |
| `ask.truncated` | Affichage limité à {shown} lignes (il y en a au moins davantage). Précisez votre question ou exportez. | Showing {shown} rows (there are more). Narrow your question or export. |
| `ask.zeroRows` | La requête s'est exécutée et n'a trouvé aucune ligne. | The query ran and found no rows. |
| `ask.export` | Exporter en CSV | Export as CSV |
| `ask.status.writing` | Rédaction de la requête | Writing the query |
| `ask.status.running` | Exécution de la requête | Running the query |
| `ask.status.done` | {count, plural, one {# ligne retournée} other {# lignes retournées}} | {count, plural, one {# row returned} other {# rows returned}} |
| `ask.refused.role` | Votre rôle ne permet pas de poser cette question. Les données demandées ne sont pas dans votre périmètre. | Your role does not allow this question. The data you asked about is outside your scope. |
| `ask.refused.alternatives` | Voici ce que vous pouvez demander : | Here is what you can ask: |
| `ask.unsafe` | Cette question demande des données ou des actions que je ne peux pas lire ou modifier. Aucune requête n'a été exécutée. | This question needs data or actions I cannot read or change. No query was run. |
| `ask.failed` | La question n'a pas pu être traitée. Reformulez-la ou réessayez. Référence : {id} | The question could not be processed. Rephrase it or try again. Reference: {id} |
| `ask.timeout` | Le délai de 8 secondes est dépassé. Reformulez avec une période plus courte ou un périmètre plus petit. | The 8-second limit was exceeded. Rephrase with a shorter period or a smaller scope. |
| `ask.history.title` | Mes questions | My questions |
| `ask.history.empty` | Vos questions apparaîtront ici. | Your questions will appear here. |
| `ask.history.rerun` | Poser à nouveau | Ask again |

**Hooks:** `useAsk()` mutation `{mutate(question), cancel(), status}` (uses `AbortController`; if the response `status === 'running'` poll `getAskQuestion` every 1 s up to 10 s); `useAskHistory({cursor})`; `useAskQuestion(id)`.

**CSV helper:** `toCsv(columns: {name:string}[], rows: unknown[][]): string` quotes every field, doubles quotes, and prefixes any cell starting with `=`, `+`, `-` or `@` with an apostrophe.

**Operations consumed:** `askTheData`, `listAskHistory`, `getAskQuestion`.

## Tasks

### Task FE-13.1: Question form, examples, progress and cancel

- [ ] **Step 1: failing test** `AskForm.test.tsx`:
  ```ts
  test('role-specific example chips fill the box', async () => {
    renderAs('finance_officer'); await user.click(screen.getByRole('button',{name:'Revenus par site ce mois-ci'}));
    expect(screen.getByLabelText('Votre question')).toHaveValue('Revenus par site ce mois-ci');
  });
  test('submits question and locale then announces progress steps politely', async () => {
    await ask('Combien ?'); expect(lastBody()).toEqual({question:'Combien ?', locale:'fr'});
    expect(liveRegion()).toHaveTextContent('Rédaction de la requête');
  });
  test('cancel aborts the request', async () => { await ask('x'); await user.click(btn('Annuler')); expect(aborted()).toBe(true); });
  test('10 s without answer shows timeout message', async () => { vi.useFakeTimers(); await ask('x'); await vi.advanceTimersByTimeAsync(10000); expect(screen.getByText(/Le délai de 8 secondes est dépassé/)).toBeVisible(); });
  test('question shorter than 3 or longer than 1,000 characters shows field error', ...);
  test('offline disables Demander with explanation; history readable from cache', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- AskForm`. Expected: fail.
- [ ] **Step 3: implement** `AskForm` (react-hook-form, zod 3 to 1,000 characters; textarea labelled "Votre question"), `ExampleQuestions` (chips by role scope: support for `support_agent`, finance for `finance_officer`, ops for `operations_engineer`; the role comes from the principal, the scope label from `AskQuestion.role_scope` after the first answer), `useAsk` (mutation with `AbortSignal`; no retries; an `Idempotency-Key` is not sent unless the generated operation declares it). While pending show the status region with `ask.status.writing` then `ask.status.running` (switch after 2 s), and a Cancel button; after 10 s abort and show `ask.timeout`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): ask-the-data question form with progress and cancel`.

### Task FE-13.2: Result with SQL always shown, table and export (TC-FR-12-4)

- [ ] **Step 1:** `AskResult.test.tsx`, `ResultTable.test.tsx`, `csv.test.ts`:
  ```ts
  test('SQL block visible with accessible name whenever sql is present', () => {
    render(<AskResult q={COMPLETED}/>); expect(screen.getByRole('region',{name:'Requête exécutée (lecture seule)'})).toHaveTextContent('SELECT s.name');
    expect(screen.getByLabelText('Généré par IA')).toBeVisible();
  });
  test('interpretation sentence from explanation and AI label', ...);
  test('table caption is the question; numeric columns right aligned; scope col headers', ...);
  test('cells inert: html and script text shown as text', ...);
  test('truncated notice when truncated true', ...);
  test('zero rows is not an error', () => expect(screen.getByText('La requête s\'est exécutée et n\'a trouvé aucune ligne.')).toBeVisible());
  test('never renders model or duration', () => { expect(screen.queryByText(/claude/i)).toBeNull(); expect(screen.queryByText(/3120|3,1 s/)).toBeNull(); });
  test('over 100 rows paginates 50 per page with skip link', ...);
  test('toCsv neutralises formulas and doubles quotes', () => expect(toCsv([{name:'a'}],[['=1+1'],['say "hi"']])).toBe('"a"\n"\'=1+1"\n"say ""hi"""'));
  test('copy query button copies exact sql', ...);
  ```
- [ ] **Step 2:** `npm test -- AskResult ResultTable csv`. Expected: fail.
- [ ] **Step 3: implement** `AskResult` composed of `AiBadge kind="sql"` plus `ask.ai.label`; `SqlBlock` (`<section aria-label>` with `<pre><code>`; copy button via `navigator.clipboard`, announcing "Requête copiée"); interpretation line from `explanation`; `ResultTable` using `DataTable` with `columns` from the response (names as headers, humanised only by replacing underscores with spaces; type `bigint`, `integer`, `numeric` right aligned and formatted with `format.number`), caption equal to the question, client pagination 50 rows per page above 100 rows, "Passer le tableau" skip link; a truncated notice; export button producing `toCsv` and downloading through a Blob with `Content-Disposition`-like filename `question-<id>.csv` (no network). Amounts are shown as the API returns them (minor units are not guessed); the explanation tells the unit.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): ask-the-data result with SQL, accessible table and CSV export`.

### Task FE-13.3: Refusal, unsafe, failure states and administrator absence (TC-FR-12-8 UI)

- [ ] **Step 1:** `AskStates.test.tsx`, `e2e/ask.spec.ts`:
  ```ts
  test('refused shows role-limited message and allowed examples, no table names', () => {
    render(<AskStates q={{...REFUSED, role_scope:'analytics_support'}}/>);
    expect(screen.getByText(/Votre rôle ne permet pas de poser cette question/)).toBeVisible();
    expect(screen.getByRole('button',{name:'Combien de demandes ouvertes par catégorie ?'})).toBeVisible();
    expect(document.body.textContent).not.toMatch(/v_|top_ups|ledger/);
  });
  test('rejected_unsafe shows "Aucune requête n\'a été exécutée" and no SQL run label', ...);
  test('failed shows reference id and retry/rephrase', ...);
  test('SQL shown when produced even if status failed (for rephrasing)', ...);
  test('role in {support,finance,ops} sees nav entry; administrator does not', ...);
  ```
  Playwright: "administrator sees no entry and `/fr/staff/ask` shows the no-access page"; "support agent asking a finance question gets the role-limited message" (mock returns `refused`).
- [ ] **Step 2:** `npm test -- AskStates`; `npm run e2e -- ask.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** `AskStates` switch on `AskQuestion.status`: `running` (status region), `completed` (result), `refused` (neutral message `ask.refused.role` plus clickable allowed examples for the user's scope; no table or view names, no count), `rejected_unsafe` (`ask.unsafe`), `failed` (`ask.failed` with `id`, and `ask.timeout` when the request aborted client-side or the API reports a timeout code). The page is wrapped in `RequirePermission operationId="askTheData"` (so the administrator gets the no-access page) and the navigation entry comes from FE-01.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): ask-the-data refusal and failure states`.

### Task FE-13.4: History and reopen

- [ ] **Step 1:** `AskHistory.test.tsx`:
  ```ts
  test('lists own questions with time, status text and row count; link reopens', ...);
  test('empty state', () => expect(screen.getByText('Vos questions apparaîtront ici.')).toBeVisible());
  test('Poser à nouveau refills the box, does not auto-run', async () => { await user.click(btn('Poser à nouveau')); expect(askCalls).toBe(0); expect(box()).toHaveValue(Q.question); });
  test('reopen shows stored sql and rows from getAskQuestion', ...);
  test('error and offline states; cursor Next link', ...);
  ```
- [ ] **Step 2:** `npm test -- AskHistory`. Expected: fail.
- [ ] **Step 3: implement** `AskHistory` (`listAskHistory`, table of question, time, status badge text, row count, "Rouvrir" link to `/staff/ask/[id]`, "Poser à nouveau" button), `/staff/ask/[id]` page using `getAskQuestion` and `AskResult`; cursor pagination links; no deletion control.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): ask-the-data history and reopen`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes.
3. `npm run i18n:check` exits 0.
4. `npm run build && npm run size` exit 0.
5. `npm run e2e -- ask.spec.ts` passes in mock mode; with `make up`, backend slice 13 and the AI stub, the stack run passes (finance asks a question and sees SQL and rows).
6. `npm run e2e:a11y -- --grep ask` zero serious or critical violations in both locales.
7. `grep -rn "duration_ms\|\.model\b" src/features/analytics` returns no match in components (only in generated types).

## Demo script

1. Sign in as a finance officer; open `/fr/staff/ask`: "Interroger les données", "Périmètre : Finance (lecture seule, données pseudonymisées)". EN: "Ask the data", "Scope: Finance (read-only, pseudonymous data)".
2. Press the chip "Revenus par site ce mois-ci" / "Revenue by site this month" and "Demander" / "Ask": steps "Rédaction de la requête", "Exécution de la requête" are announced.
3. The result shows "SQL écrit par l'IA..." / "SQL written by AI...", the full query, the interpretation and the table with caption.
4. Press "Exporter en CSV" / "Export as CSV": a file downloads; cells starting with "=" are prefixed.
5. Sign in as a support agent and ask a finance question: "Votre rôle ne permet pas de poser cette question." / "Your role does not allow this question." with allowed examples.
6. Sign in as the administrator: no "Interroger les données" entry; the URL shows "Accès non autorisé" / "You do not have access".
7. Open "Mes questions" / "My questions", reopen one, press "Poser à nouveau" / "Ask again": the box is filled and nothing runs automatically.

## Done criteria

- Requirements: FR-12 (question, SQL shown, result, refusal, history), FR-16, NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8: TC-FR-12-4 (exact executed SQL always shown), TC-FR-12-8 (administrator has no ask-the-data screen; support and operations see no phone digits or row-level attributes because none are rendered), TC-FR-12-9 (8 s limit handled in the UI), TC-FR-15-15, TC-NFR-10-1.

## Plan notes for the tech lead

- **`AskQuestion` exposes `model` and `duration_ms`:** the schema returns them to every caller, but `docs/08` and D-14 keep cost and latency away from the screens of roles that can ask. The UI never renders them; the API could omit them for non-admin roles.
- **History retention:** `docs/08` 3.5 says 90 days; no API statement. Not displayed.
- **Synchronous call:** `askTheData` is assumed to answer within the 8 s outer budget; if the API returns `status: running`, the UI polls `getAskQuestion`.
- **Money in results:** result cells are raw (the SQL decides units); the plan does not guess minor units. A convention for `*_minor` columns would need an API note.
- **Examples** are UI content, not API data; the finance examples reference the same views the backend catalog exposes, which the backend author should confirm.
