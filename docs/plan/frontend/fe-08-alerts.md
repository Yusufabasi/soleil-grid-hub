# Frontend slice 08: Alerts, meter telemetry view, token retry and notification outbox

- **Goal:** operations engineers see and work alerts (list, detail, acknowledge, resolve), tune alert rules, inspect a meter's recent readings and tokens (including a safe manual token retry), and support and operations see the notification outbox with failures and retries.
- **Depends on:** backend slice 8 (telemetry and alerts; also tokens from slice 6 and notifications from slice 7); frontend slices fe-00, fe-01, fe-02 (meter detail page).
- **Requirements served:** FR-07, FR-06 (read side), FR-05 (token retry), FR-19 (outbox view), FR-16, NFR-10, NFR-11.
- **Size:** M, 5 days.
- **Spikes or open items:** there is no frontend slice file numbered 07; the notification outbox screen of backend slice 7 is delivered here. Default for the polling interval of the alerts list: 30 seconds **[open: replace by the dashboard stream if operations ask]**.

## Read first

- `docs/08-frontend-ux.md#3.6 Operations dashboard (FR-13)` (alert severity text, announcement rules)
- `docs/08-frontend-ux.md#7.3 Screen-reader semantics for tables and live regions` (dashboard critical alert row)
- `docs/08-frontend-ux.md#5.5 Optimistic updates policy` (acknowledge alert is allowed; token retry is not)
- `docs/08-frontend-ux.md#7.5 Motion, forms and errors`
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)`
- `docs/00-shared-context.md` section 7: D-07 (token retry and `unknown`), D-14, D-25, section 7.2 (alert kinds and offline rules)
- OperationIds: `listAlerts`, `getAlert`, `acknowledgeAlert`, `resolveAlert`, `listAlertRules`, `updateAlertRule`, `listMeterReadings`, `listMeterTokens`, `getToken`, `retryToken`, `getMeter`, `listNotifications`, `getNotification`, `retryNotification`, `listNotificationTemplates`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page.
- Access token in memory only; all pages client components under thin server pages.
- Budget: staff first load at most 250 KB; no charting library: readings use a table and an optional inline SVG sparkline written in the repo (under 2 KB).
- next-intl `/fr` and `/en`; French first.
- WCAG 2.2 AA: severity and status as text plus icon plus colour; the alerts live region announces only critical alerts (assertive) and batched warnings (polite, at most one per 30 s); no blinking.
- Generated client only; alert `details` is an open object and is rendered as escaped key-value text, never as HTML.
- Acknowledging an alert may be optimistic with rollback and a visible error; resolving and token retry are not optimistic.
- Token retry is never optimistic and sends no new token id: the API reuses the same token (D-07); the UI shows the server's response only.
- Navigation and buttons from `permissions.generated.ts`: alerts and rules are `operations_engineer` only; notification list is support and operations; retry of a notification is operations only.
- Do not edit frozen files.

## Review focus

1. A critical alert arrives but is not announced, or warnings spam a screen reader: `AlertsLive.test.tsx` "announces critical immediately and batches warnings" (Task FE-08.1).
2. A resolved alert reappears as new after the next poll, or acknowledge on one tab is overwritten silently: `AlertDetail.test.tsx` "optimistic acknowledge rolls back on error" (Task FE-08.2).
3. The retry button on a token is pressed twice and the user believes two tokens were issued: `TokenRetry.test.tsx` "retry once, button disabled, same token id shown" (Task FE-08.4).
4. A token in `unknown` state is described as failed and the customer is told to pay again: `TokenStatus.test.tsx` "unknown shown as needs review" (Task FE-08.4).
5. A failed notification looks sent: `NotificationList.test.tsx` "dead_lettered and failed show text and retry for operations only" (Task FE-08.5).

## Files

- `frontend/src/features/telemetry/keys.ts`, `hooks/useAlerts.ts`, `useAlert.ts`, `useAlertActions.ts`, `useAlertRules.ts`, `useMeterReadings.ts`.
- `frontend/src/features/telemetry/components/AlertList.tsx`, `AlertFilters.tsx`, `AlertDetail.tsx`, `AlertSeverityBadge.tsx`, `AlertsLive.tsx`, `AlertRuleForm.tsx`, `ReadingsTable.tsx`, `Sparkline.tsx`.
- `frontend/src/features/meters/components/MeterTokens.tsx`, `TokenStatus.tsx`, `TokenRetry.tsx`, `hooks/useMeterTokens.ts`, `hooks/useRetryToken.ts` (modify fe-02 meter detail to add tabs).
- `frontend/src/features/notifications/keys.ts`, `hooks/useNotifications.ts`, `useRetryNotification.ts`, `components/NotificationList.tsx`, `NotificationDetail.tsx`, `TemplateList.tsx`.
- `frontend/src/app/[locale]/(staff)/staff/alerts/page.tsx`, `alerts/[id]/page.tsx`, `alerts/rules/page.tsx`, `meters/[id]/readings/page.tsx` (tab), `notifications/page.tsx`, `notifications/[id]/page.tsx`.
- `frontend/src/i18n/messages/{fr,en}/alerts.json`, `notifications.json`.
- Tests next to code; `frontend/e2e/alerts.spec.ts`.

## Interfaces

**Routes (staff):** `/staff/alerts`, `/staff/alerts/[id]`, `/staff/alerts/rules`, `/staff/meters/[id]/readings`, `/staff/notifications`, `/staff/notifications/[id]`. Meter detail tabs: Détails, Relevés, Jetons.

**Namespaces `alerts` and `notifications` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `alerts.title` | Alertes | Alerts |
| `alerts.count` | {count, plural, =0 {Aucune alerte} one {# alerte} other {# alertes}} | {count, plural, =0 {No alerts} one {# alert} other {# alerts}} |
| `alerts.kind.meter_offline` | Compteur hors ligne | Meter offline |
| `alerts.kind.site_offline` | Site hors ligne | Site offline |
| `alerts.kind.low_balance` | Solde faible | Low balance |
| `alerts.kind.anomalous_consumption` | Consommation anormale | Anomalous consumption |
| `alerts.severity.critical` | Critique | Critical |
| `alerts.severity.warning` | Avertissement | Warning |
| `alerts.severity.info` | Information | Information |
| `alerts.status.open` | Ouverte | Open |
| `alerts.status.acknowledged` | Prise en charge | Acknowledged |
| `alerts.status.resolved` | Résolue | Resolved |
| `alerts.acknowledge` | Prendre en charge | Acknowledge |
| `alerts.resolve` | Résoudre | Resolve |
| `alerts.note` | Note (facultative) | Note (optional) |
| `alerts.empty` | Aucune alerte ne correspond à ces critères. | No alert matches these criteria. |
| `alerts.announce.critical` | Alerte critique : {summary} | Critical alert: {summary} |
| `alerts.announce.warnings` | {count, plural, one {# nouvel avertissement} other {# nouveaux avertissements}} | {count, plural, one {# new warning} other {# new warnings}} |
| `alerts.rules.title` | Règles d'alerte | Alert rules |
| `alerts.rules.saved` | Règle enregistrée. | Rule saved. |
| `alerts.readings.title` | Derniers relevés | Latest readings |
| `alerts.readings.flagged` | Relevé signalé : {flags} | Flagged reading: {flags} |
| `alerts.token.retry` | Renvoyer le jeton | Resend the token |
| `alerts.token.retryHelp` | Le même jeton est renvoyé. Aucun nouveau crédit n'est créé. | The same token is resent. No new credit is created. |
| `alerts.token.status.pending` | En attente | Pending |
| `alerts.token.status.sent` | Envoyé | Sent |
| `alerts.token.status.acknowledged` | Confirmé par le compteur | Acknowledged by the meter |
| `alerts.token.status.failed` | Échec | Failed |
| `alerts.token.status.cancelled` | Annulé | Cancelled |
| `alerts.token.status.unknown` | À vérifier (résultat incertain) | Needs review (outcome uncertain) |
| `alerts.token.retrying` | Nouvelle tentative n° {attempts} | Retry attempt {attempts} |
| `notifications.title` | Notifications | Notifications |
| `notifications.status.queued` | En file | Queued |
| `notifications.status.sending` | Envoi | Sending |
| `notifications.status.sent` | Envoyée | Sent |
| `notifications.status.delivered` | Remise | Delivered |
| `notifications.status.failed` | Échec | Failed |
| `notifications.status.dead_lettered` | Abandonnée après plusieurs échecs | Given up after repeated failures |
| `notifications.retry` | Réessayer l'envoi | Retry sending |

**Hooks:** `useAlerts({status?, severity?, kind?, site_id?, cursor?})` polling every 30 s while the tab is visible; `useAlertActions(id)` returns `acknowledge(note?)` (optimistic with rollback) and `resolve(note?)` (pessimistic); `useMeterReadings(meterId, {from?, to?, cursor?})`; `useMeterTokens(meterId)`; `useRetryToken(tokenId)` (pessimistic); `useNotifications(filters)`; `useRetryNotification(id)`.

**Components:** `AlertSeverityBadge({severity})` (icon plus text), `AlertsLive` (mounted on the alerts page; diffs new alerts from the poll and calls `useAnnounce`), `AlertRuleForm({rule})` (renders `params` entries as labelled number or switch inputs discovered from the current value types), `TokenRetry({token})`.

**Operations consumed:** the 15 operationIds under Read first.

## Tasks

### Task FE-08.1: Alerts list with live announcements (TC-FR-07-1, TC-FR-07-4 UI)

- [ ] **Step 1: failing test** `AlertList.test.tsx`, `AlertsLive.test.tsx`:
  ```ts
  test('table shows kind, severity text, status, site and opened time', async () => {
    mockAlerts([A_CRIT_SITE_OFFLINE]); render(<AlertList/>);
    expect(await screen.findByRole('table',{name:/Alertes/})).toBeVisible();
    expect(screen.getByText('Site hors ligne')).toBeVisible(); expect(screen.getByText('Critique')).toBeVisible();
  });
  test('announces critical immediately and batches warnings', async () => {
    vi.useFakeTimers(); render(<AlertsLive/>); pushPoll([CRIT]); expect(assertiveRegion()).toHaveTextContent('Alerte critique');
    pushPoll([W1]); pushPoll([W2]); await vi.advanceTimersByTimeAsync(30000);
    expect(politeRegion()).toHaveTextContent('2 nouveaux avertissements');
  });
  test('does not re-announce an alert seen in the previous poll', ...);
  test('empty, error, partial (poll failure keeps last data with banner), offline states', ...);
  test('administrator and finance cannot open the page', ...);   // RequirePermission listAlerts
  ```
- [ ] **Step 2:** `cd frontend && npm test -- AlertList AlertsLive`. Expected: fail.
- [ ] **Step 3: implement** `AlertList` (DataTable; filters apply with an Apply button; cursor links; default filter `status=open`), `AlertSeverityBadge`, `AlertsLive` (keeps a `Set` of seen alert ids from the previous poll; new `critical` calls `announce(message,'assertive')`; new `warning` are collected and announced politely at most once per 30 s with the count; `info` never announced). Poll with `refetchInterval: 30_000` while `document.visibilityState === 'visible'`. Staff roles other than operations never see the nav entry (FE-01 generates it).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): alerts list with severity badges and live announcements`.

### Task FE-08.2: Alert detail, acknowledge and resolve

- [ ] **Step 1:** `AlertDetail.test.tsx`:
  ```ts
  test('shows summary and escaped details as key-value list', () => { expect(screen.getByText('<img src=x onerror=1>')).toBeVisible(); expect(document.querySelector('img')).toBeNull(); });
  test('optimistic acknowledge rolls back on error', async () => {
    server.use(post500('/alerts/'+A.id+'/acknowledge')); await user.click(btn('Prendre en charge'));
    expect(screen.getByText('Prise en charge')).toBeVisible(); await waitFor(()=>expect(screen.getByText('Ouverte')).toBeVisible());
    expect(screen.getByRole('alert')).toBeVisible();
  });
  test('resolve is pessimistic and needs optional note field', ...);
  test('buttons absent when status resolved', ...);
  test('links to meter, site and customer pages when ids present', ...);
  ```
- [ ] **Step 2:** `npm test -- AlertDetail`. Expected: fail.
- [ ] **Step 3: implement** `AlertDetail`: header with kind, severity, status; definition list from `details` (keys sorted, values `String()`-ed and rendered as text); timeline fields (opened, acknowledged by and at, resolved at); `acknowledge` with `onMutate` cache update and `onError` rollback plus an alert region message (the only optimistic action here); `resolve` waits for the server and then refetches list and detail; both accept an optional note (`AlertAction.note`). Focus returns to the status heading after an action; a polite announcement says "Alerte prise en charge" / "Alerte résolue" (keys `alerts.announce.acknowledged`, `alerts.announce.resolved`).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): alert detail with acknowledge and resolve`.

### Task FE-08.3: Alert rules

- [ ] **Step 1:** `AlertRuleForm.test.tsx`:
  ```ts
  test('renders params as labelled inputs by value type', ...);        // number -> spinbutton, boolean -> switch
  test('save sends only changed fields and shows saved status', ...);
  test('disabling a critical rule asks for confirmation', ...);
  test('only operations engineers can save; others read-only', ...);
  test('validation error from server maps to field via JSON pointer', ...);
  ```
- [ ] **Step 2:** `npm test -- AlertRuleForm`. Expected: fail.
- [ ] **Step 3: implement** `/staff/alerts/rules` listing the four rule kinds (`listAlertRules`), each in a `Field` group with enabled switch, severity select, and params inputs generated from the current `params` object (numbers use `inputmode="numeric"`, labels from `alerts.rules.param.<key>` with fallback to the raw key). Submit sends `updateAlertRule` with the changed fields only; confirmation dialog before disabling a rule whose severity is `critical`. Show the known defaults as help text from messages: meter silent more than 90 minutes, gateway heartbeat missing 15 minutes (`alerts.rules.help.meter_offline`, `.site_offline`).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): alert rules editor`.

### Task FE-08.4: Meter readings and token tabs with safe retry (TC-FR-05-1 UI)

- [ ] **Step 1:** `ReadingsTable.test.tsx`, `TokenStatus.test.tsx`, `TokenRetry.test.tsx`:
  ```ts
  test('readings table: time, energy kWh, power W, credit kWh; flagged rows list flags in text', ...);
  test('unknown status shown as needs review, not failed', () => expect(screen.getByText('À vérifier (résultat incertain)')).toBeVisible());
  test('pending with attempts>0 shows "Nouvelle tentative n° 2"', ...);
  test('retry once, button disabled, same token id shown', async () => {
    await user.click(btn('Renvoyer le jeton')); await user.click(btn('Renvoyer le jeton')); expect(retryCalls).toBe(1);
    expect(screen.getByText(TOKEN.id)).toBeVisible();
  });
  test('retry hidden unless operations_engineer and status in failed/unknown/pending', ...);
  test('retry shows help text that no new credit is created', ...);
  ```
- [ ] **Step 2:** `npm test -- ReadingsTable TokenStatus TokenRetry`. Expected: fail.
- [ ] **Step 3: implement** `ReadingsTable` (DataTable of `MeterReading`: `recorded_at` and `received_at` with `DateText`, `energy_wh_total` as kWh, `power_w`, `credit_remaining_wh` as kWh, flags as text; cursor pagination; a `Sparkline` of `power_w` for the last 24 points as `<svg role="img" aria-label="...">` with a visually hidden data table already present, so the sparkline is decorative-plus). `MeterTokens` (`listMeterTokens`): columns token code masked, energy, counter, status (`TokenStatus`), attempts, expiry, last error. `TokenRetry`: button visible when `usePermission('retryToken')` and status in `failed`, `unknown` or (`pending` with attempts > 0); opens a confirm dialog with `alerts.token.retryHelp`; `retryToken` pessimistic; disables while pending; shows the response token (same id) and refetches.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): meter readings, tokens and safe token retry`.

### Task FE-08.5: Notification outbox view (FR-19)

- [ ] **Step 1:** `NotificationList.test.tsx`, `e2e/alerts.spec.ts`:
  ```ts
  test('failed and dead_lettered show text; retry only for operations_engineer', async () => {
    renderAs('support_agent'); expect(screen.queryByRole('button',{name:'Réessayer l\'envoi'})).toBeNull();
    renderAs('operations_engineer'); expect(screen.getByRole('button',{name:'Réessayer l\'envoi'})).toBeVisible();
  });
  test('recipient is masked and last_error shown in a details disclosure', ...);
  test('retry success refetches and announces', ...);
  test('templates list shows required parameters', ...);
  ```
  Playwright `alerts.spec.ts`: "operations engineer acknowledges, resolves an alert and retries a token" with axe check on each page.
- [ ] **Step 2:** `npm test -- NotificationList`; `npm run e2e -- alerts.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** `NotificationList` (filters status, channel; DataTable: created, channel, template code, masked recipient, status badge with text, attempts, retry button), `NotificationDetail` (`getNotification`), `TemplateList` (`listNotificationTemplates`, read only: code, channel, locales, required and optional params). Retry is `retryNotification` pessimistic with a polite announcement. No create form is built here (`createNotification` is a support-agent operation without a documented screen; not built).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): notification outbox view with retry`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes.
3. `npm run i18n:check` exits 0.
4. `npm run build && npm run size` exit 0 (staff routes at most 250 KB).
5. `npm run e2e -- alerts.spec.ts` passes in mock mode; with `make up`, backend slice 8 and the meter simulator, `E2E_MODE=stack npm run e2e -- alerts.spec.ts` passes (offline alert appears and is acknowledged).
6. `npm run e2e:a11y -- --grep "alerts|notifications"` zero serious or critical violations in `/fr` and `/en`.

## Demo script

1. Sign in as an operations engineer; open `/fr/staff/alerts`: "Alertes" with severity text. EN `/en/staff/alerts`: "Alerts".
2. Stop the meter simulator for one site: within the offline thresholds a "Site hors ligne" / "Site offline" alert appears; a screen reader announces "Alerte critique : ..." / "Critical alert: ...".
3. Open it, press "Prendre en charge" / "Acknowledge", then "Résoudre" / "Resolve" after restarting the simulator.
4. Open `/fr/staff/alerts/rules`, change a threshold, save: "Règle enregistrée." / "Rule saved."
5. Open a meter, tab "Jetons" / "Tokens": a token in `unknown` shows "À vérifier (résultat incertain)" / "Needs review (outcome uncertain)"; press "Renvoyer le jeton" / "Resend the token" once and read the help text.
6. Open `/fr/staff/notifications` as support: failed messages show text and no retry button; as operations the retry button appears.

## Done criteria

- Requirements: FR-07 (alert views and actions), FR-06 (readings view), FR-05 (token retry control), FR-19 (outbox view), FR-16, NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8 (UI contribution): TC-FR-07-1, TC-FR-07-4 (kinds and offline labels), TC-FR-05-1 (single retry control, same token), TC-FR-19-7 (permanent failure surfaced), TC-FR-06-9 (flagged implausible readings are displayed with their flags), TC-FR-15-15.

## Plan notes for the tech lead

- **No `fe-07` file:** the backend slice 7 notification screens are folded into this file because the requested file list has no `fe-07`.
- **Alerts role:** `api/permissions.yaml` limits `listAlerts`, `acknowledgeAlert` and `resolveAlert` to `operations_engineer`; `docs/08` 3.6 shows alerts on the dashboard for several roles (via `getDashboardSummary` counts, which is allowed). Followed `permissions.yaml` for the alerts list.
- **Alert rule params:** `AlertRule.params` is an open object, so the rule form is generic; the executor must not invent per-kind field names beyond what the API returns.
- **Meter token list:** `listMeterTokens` is allowed for customers, support, operations and finance, but the retry control is operations only (`retryToken`). The portal token list (fe-04) uses `listTokens`.
- **Create notification:** no screen planned for `createNotification`.
