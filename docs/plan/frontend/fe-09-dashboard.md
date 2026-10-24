# Frontend slice 09: Operations dashboard with live updates and stale-data indicator

- **Goal:** operations, finance and support staff open a dashboard that shows sites, revenue today (not for support), pending top-ups, token backlog and alert counts, updates live over server-sent events using the single-use ticket flow, tells the truth when data is stale, can be paused, and works on a control-room wall display.
- **Depends on:** backend slice 9 (dashboard read models, cache and SSE); frontend slices fe-00, fe-01, fe-08 (alert badge and announcement helper).
- **Requirements served:** FR-13, FR-07 (alert counts and feed), NFR-02 (dashboard latency perception), NFR-10, NFR-11.
- **Size:** L, 7 days.
- **Spikes or open items:** the SSE reconnect semantics: `docs/03` and D-18 say a new ticket per connection, which means the browser's automatic `Last-Event-ID` resume cannot be used (a new `EventSource` object sends no `Last-Event-ID`). Default: every new connection starts with a server `snapshot` event, and the client treats that as the resync point (see Plan notes). Polling fallback interval 10 s **[open: confirm with SRE load numbers]**.

## Read first

- `docs/08-frontend-ux.md#3.6 Operations dashboard (FR-13)`
- `docs/08-frontend-ux.md#5.6 SSE handling and reconnect`
- `docs/08-frontend-ux.md#7.3 Screen-reader semantics for tables and live regions` (dashboard rows and the SSE state row)
- `docs/08-frontend-ux.md#7.1 Checklist mapped to success criteria` (SC 2.2.2 pause, stop, hide)
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)` (Stream hook, Client state and caching)
- `docs/00-shared-context.md` section 7: D-14, D-18, D-48
- OperationIds: `getDashboardSummary`, `createStreamTicket`, `streamDashboard`, `listSites`.
- SSE event names (from `streamDashboard`): `snapshot`, `site.status_changed`, `revenue.updated`, `alert.raised`, `alert.updated`, `heartbeat`, `resync_required`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page (`connect-src 'self'` covers the stream).
- Access token in memory only; the stream authenticates with a single-use ticket in the query string (30 s, IP-bound) because `EventSource` cannot set headers; the ticket is never logged, stored or reused.
- A stream lives at most 10 minutes; the client requests a new ticket and reconnects; at most 3 concurrent streams per user, so one tab owns the stream and shares events with the others.
- Budget: staff first load at most 250 KB for the dashboard route; no chart library (tiles and tables).
- next-intl `/fr` and `/en`; French first; time zone shown as the site's zone with text.
- WCAG 2.2 AA: status is icon plus text plus colour; metric tiles are not live regions; only critical alerts and stale transitions are announced; "Pause updates" button (SC 2.2.2); updates never move focus or reorder rows under the pointer.
- Generated client only; SSE payloads are validated with zod schemas derived by hand from the generated component types (compile-time `satisfies` checks against `schema.d.ts`), unknown events ignored.
- No global store library (`docs/03`): connection status uses `useSyncExternalStore` over a tiny module.
- Roles: `getDashboardSummary` and the stream are allowed for support, finance and operations only; the administrator has no dashboard. Revenue is withheld by the API for support; the UI renders the revenue tile only when `revenue_today` is present.
- Do not edit frozen files.

## Review focus

1. The dashboard shows old numbers as live after the stream silently dies: `useSse.test.ts` "no event for 60 s becomes stale" and `e2e/dashboard.spec.ts` "banner after server stops sending" (Task FE-09.2).
2. Ticket reuse after a reconnect gives repeated 401 loops: `useSse.test.ts` "new ticket for every connection" (Task FE-09.1).
3. Three tabs each open a stream and the third is refused: `streamLeader.test.ts` "only the leader opens the stream, others receive broadcast" (Task FE-09.3).
4. Live updates steal focus, shuffle rows or flood the screen reader: `SitesTable.test.tsx` "updates cells in place, no focus change, sort not re-applied" and `DashboardLive.test.tsx` (Task FE-09.4).
5. Support sees revenue or the administrator reaches the page: `Dashboard.test.tsx` "revenue tile absent when revenue_today missing" and guard test (Task FE-09.4).

## Files

- `frontend/src/shared/hooks/useSse.ts`: generic ticketed event-stream hook.
- `frontend/src/shared/hooks/sseStatus.ts`: connection status store (`useSyncExternalStore`).
- `frontend/src/features/analytics/keys.ts`, `schemas/sse.ts` (zod payload schemas), `reducer.ts` (pure state reducer), `streamLeader.ts` (Web Locks leader plus `BroadcastChannel`).
- `frontend/src/features/analytics/hooks/useDashboard.ts` (combines summary query, stream, pause).
- `frontend/src/features/analytics/components/Dashboard.tsx`, `SummaryTiles.tsx`, `SitesTable.tsx`, `AlertFeed.tsx`, `LiveStatus.tsx`, `StaleBanner.tsx`, `PauseButton.tsx`, `WallMode.tsx`.
- `frontend/src/app/[locale]/(staff)/staff/dashboard/page.tsx`, `loading.tsx`, `error.tsx`.
- `frontend/src/test/FakeEventSource.ts`, `frontend/src/test/fixtures/dashboard.ts`.
- `frontend/src/i18n/messages/{fr,en}/dashboard.json`.
- Tests next to code; `frontend/e2e/dashboard.spec.ts`.

## Interfaces

**Route (staff):** `/staff/dashboard` (optional `?site=<uuid>` filter, `?display=wall` for the wall mode).

**Namespace `dashboard` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `dashboard.title` | Tableau de bord des opérations | Operations dashboard |
| `dashboard.live.live` | En direct | Live |
| `dashboard.live.reconnecting` | Reconnexion en cours (tentative {n}) | Reconnecting (attempt {n}) |
| `dashboard.live.stale` | Données possiblement périmées. Dernière mise à jour : {time} | Data may be out of date. Last update: {time} |
| `dashboard.live.paused` | Mises à jour suspendues. {count, plural, =0 {Aucun changement en attente} one {# changement en attente} other {# changements en attente}} | Updates paused. {count, plural, =0 {No change waiting} one {# change waiting} other {# changes waiting}} |
| `dashboard.live.pause` | Suspendre les mises à jour | Pause updates |
| `dashboard.live.resume` | Reprendre les mises à jour | Resume updates |
| `dashboard.announce.stale` | Mises à jour en direct interrompues. Les données peuvent être périmées. | Live updates paused. Data may be out of date. |
| `dashboard.announce.restored` | Mises à jour en direct rétablies. | Live updates restored. |
| `dashboard.revenue.title` | Revenus du jour | Revenue today |
| `dashboard.sites.online` | Sites en service | Sites operational |
| `dashboard.sites.summary` | {online} sur {total} | {online} of {total} |
| `dashboard.pending` | Recharges en attente | Pending top-ups |
| `dashboard.backlog` | Jetons en attente d'envoi | Tokens waiting to be sent |
| `dashboard.alerts.title` | Alertes actives | Active alerts |
| `dashboard.alerts.critical` | {count} critiques | {count} critical |
| `dashboard.alerts.warning` | {count} avertissements | {count} warnings |
| `dashboard.site.status.operational` | En service | Operational |
| `dashboard.site.status.degraded` | Dégradé | Degraded |
| `dashboard.site.status.offline` | Hors ligne | Offline |
| `dashboard.site.status.maintenance` | Maintenance | Maintenance |
| `dashboard.site.metersOnline` | Compteurs en ligne | Meters online |
| `dashboard.site.stale` | périmé | stale |
| `dashboard.empty` | Aucun site n'est configuré. | No site is configured. |
| `dashboard.readSummary` | Lire le résumé | Read summary |
| `dashboard.summarySentence` | Revenus du jour : {revenue}. {online} sites sur {total} en service. {critical} alertes critiques. | Revenue today: {revenue}. {online} of {total} sites operational. {critical} critical alerts. |

**Hook `useSse` (in `src/shared/hooks/useSse.ts`):**
```ts
type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'stale' | 'fallback' | 'closed';
useSse(opts: {
  getTicket: () => Promise<{ticket: string; expires_at: string; stream_max_seconds: number}>; // createStreamTicket
  buildUrl: (ticket: string) => string;               // '/api/v1/dashboard/stream?ticket=' + ticket + optional site_id
  events: Record<string, (data: unknown, id?: string) => void>;
  onResync: (reason: 'cursor_too_old'|'server_restart'|'max_age'|'role_changed') => void;
  fallbackPoll?: {intervalMs: number; run: () => Promise<void>};
  staleAfterMs?: number;                              // default 60_000
  enabled?: boolean;
}): {status: StreamStatus; attempt: number; lastEventAt?: number; close(): void}
```
Rules: one ticket per connection; backoff 1 s doubling to 30 s with jitter; after 3 consecutive failures switch to `fallback` (polling every 10 s) while still retrying the stream every 30 s; a `heartbeat` or any event sets `live`; no event for `staleAfterMs` sets `stale`; `resync_required` with `max_age` reconnects immediately with a new ticket without showing `reconnecting` when the gap is under 2 s; reason `role_changed` stops reconnecting and calls `onResync`.

**Reducer:** `dashboardReducer(state, event)` pure: handles `snapshot` (replace), `site.status_changed` (patch one site), `revenue.updated` (replace revenue and top-ups today), `alert.raised`/`alert.updated` (feed, max 20, keyed by id), unknown (ignored).

**Operations consumed:** `getDashboardSummary`, `createStreamTicket`, `streamDashboard` (through `EventSource`), `listSites` (site filter).

## Tasks

### Task FE-09.1: Ticketed stream hook with backoff, rotation and fallback

- [ ] **Step 1: failing test** `frontend/src/shared/hooks/useSse.test.ts` with `FakeEventSource` and fake timers:
  ```ts
  test('new ticket for every connection', async () => {
    renderHook(() => useSse(opts)); await flush(); fes(0).emit('snapshot',{...});
    fes(0).error(); await vi.advanceTimersByTimeAsync(1500);
    expect(ticketCalls).toBe(2); expect(fes(1).url).not.toBe(fes(0).url);
  });
  test('backoff 1s,2s,4s with jitter bound and cap 30s', ...);
  test('three failures switch to polling fallback every 10s', async () => { ...; expect(pollCalls).toBe(2); expect(result.current.status).toBe('fallback'); });
  test('max_age resync reconnects with new ticket without showing reconnecting for gaps under 2s', ...);
  test('role_changed stops reconnecting and calls onResync', ...);
  test('ticket never appears in logs or storage', () => { expect(JSON.stringify(localStorage)+JSON.stringify(sessionStorage)).not.toContain(TICKET); });
  test('close() ends everything and clears timers', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- useSse`. Expected: fail (module missing).
- [ ] **Step 3: implement** `useSse` per Interfaces. Create `src/test/FakeEventSource.ts` (records `url`, supports `addEventListener(name, fn)`, `emit(name, data, id)`, `error()`, `close()`); the hook receives an injectable constructor via an internal `__EventSourceImpl` option defaulting to `globalThis.EventSource` (test-only seam, not exported from `index`). Parse `MessageEvent.data` with `JSON.parse` inside try/catch (malformed events reported through `console.warn` without payload). Status goes to `sseStatus.ts` (`setStatus`, `useStreamStatus`). Staleness timer resets on every event.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): ticketed server-sent events hook with backoff and polling fallback`.

### Task FE-09.2: Dashboard reducer, payload schemas and stale detection (TC-FR-13-3 UI)

- [ ] **Step 1:** `reducer.test.ts`, `sse.schema.test.ts`, `StaleBanner.test.tsx`:
  ```ts
  test('snapshot replaces state; site.status_changed patches one site', ...);
  test('revenue.updated replaces per-currency revenue', ...);
  test('alert.raised then alert.updated keeps one entry, feed capped at 20', ...);
  test('unknown event ignored; malformed payload rejected by zod', ...);
  test('payload schemas match generated types', () => { expectTypeOf<z.infer<typeof siteStatusChanged>>().toMatchTypeOf<components['schemas']['SseSiteStatusChanged']>(); });
  test('StaleBanner shown when status stale or cache_age_seconds > 60, with last update time', ...);
  test('stale transition announces once, restoration announces once', ...);
  ```
- [ ] **Step 2:** `npm test -- reducer sse.schema StaleBanner`. Expected: fail.
- [ ] **Step 3: implement** `schemas/sse.ts` (zod for `DashboardSummary`, `SseSiteStatusChanged`, `SseRevenueUpdated`, `Alert` subset, `SseHeartbeat`, `SseResyncRequired`; field names exactly those in `schema.d.ts`), `reducer.ts`, `StaleBanner` (persistent banner `role="status"` text `dashboard.live.stale`, shown when stream status is `stale` or `fallback` older than 30 s, or when the summary reports `cache_age_seconds > 60`, which covers a replica partition serving stale cache), `LiveStatus` (icon plus text: Live, Reconnecting, Stale, Fallback). Announcements: a `useEffect` on status transitions calls `announce` politely at most once per 30 s (debounce) with `dashboard.announce.stale` and `dashboard.announce.restored`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): dashboard state reducer, payload schemas and stale banner`.

### Task FE-09.3: One stream per user across tabs

- [ ] **Step 1:** `streamLeader.test.ts` with fake `navigator.locks` and a fake `BroadcastChannel`:
  ```ts
  test('only the leader opens the stream; followers receive broadcast events', async () => {
    const a = startLeader(env()); const b = startLeader(env());
    await flush(); expect(openStreams).toBe(1); a.publish({name:'revenue.updated',data:R}); expect(b.received).toEqual([{name:'revenue.updated',data:R}]);
  });
  test('when leader closes, a follower becomes leader and opens a stream', ...);
  test('follower shows status from leader broadcast', ...);
  test('no Web Locks support falls back to every tab opening its own stream', ...);
  ```
- [ ] **Step 2:** `npm test -- streamLeader`. Expected: fail.
- [ ] **Step 3: implement** `streamLeader.ts`: `startLeader({onEvent, onStatus, openStream})` using `navigator.locks.request('hub-dashboard-stream', {mode:'exclusive'}, callback)` where the callback holds the lock for the life of the tab and runs `openStream`; every event and status the leader sees is posted on `BroadcastChannel('hub-dashboard')`; followers just listen. Fallback when `navigator.locks` is undefined: open the stream in each tab (the API limit is 3). `useDashboard` wires `useSse` into the leader callback.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): single leader stream shared across browser tabs`.

### Task FE-09.4: Dashboard page, tiles, sites table, alert feed, pause (TC-FR-13-2 UI)

- [ ] **Step 1:** `Dashboard.test.tsx`, `SitesTable.test.tsx`, `DashboardLive.test.tsx`, `e2e/dashboard.spec.ts`:
  ```ts
  test('renders tiles and sites table from summary', ...);
  test('revenue tile absent when revenue_today missing (support)', () => expect(screen.queryByText('Revenus du jour')).toBeNull());
  test('one revenue figure per currency, exact formatting', ...);
  test('updates cells in place, no focus change, sort not re-applied', async () => {
    const row = screen.getByRole('link',{name:'Kasenga'}); row.focus(); push('site.status_changed',{site_id:K,status:'degraded'});
    expect(row).toHaveFocus(); expect(within(rowOf(K)).getByText('Dégradé')).toBeVisible(); expect(rowOrder()).toEqual(before);
  });
  test('pause buffers events and shows count; resume applies them', ...);
  test('metric tiles are not live regions; read summary button announces one sentence', ...);
  test('stale site row shows "périmé" text when last data older than 30 min', ...);
  test('wall mode: large type class, no hover-only info', ...);
  test('administrator cannot open the route', ...);
  ```
  Playwright: mock server sends `snapshot`, then `site.status_changed`; assert the table cell changes within 2 s without reload; then stop events for 61 s (fake clock) and assert the stale banner.
- [ ] **Step 2:** `npm test -- Dashboard SitesTable DashboardLive`; `npm run e2e -- dashboard.spec.ts`. Expected: fail.
- [ ] **Step 3: implement.** `Dashboard` (client component) loads `getDashboardSummary` (`staleTime` Infinity while the stream is live, 10 s when in fallback), seeds the reducer, runs the leader stream, renders: `SummaryTiles` (revenue per currency via `MoneyText`, sites online, pending top-ups, token backlog, alert counts with text), `SitesTable` (native table with caption; columns name, status badge, meters online, open alerts, last data when known; rows mutate in place; sort is applied only when the user presses "Appliquer le tri" after "N changements"), `AlertFeed` (events received since page load, newest first, critical entries announced assertively through the same `AlertsLive` rule of fe-08; an empty feed shows "Aucune nouvelle alerte depuis l'ouverture de la page"), `PauseButton` (buffers events in the reducer queue), `WallMode` (reads `?display=wall`: body class `wall` with minimum 24 px text, no tooltips, no automatic reload in v1). The `site` filter changes the `site_id` query of the stream and refetches. Page-level `RequirePermission operationId="getDashboardSummary"`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): live operations dashboard`.

### Task FE-09.5: Loading, error, partial and offline states, summary sentence, wall display polish

- [ ] **Step 1:** `Dashboard.states.test.tsx`:
  ```ts
  test('loading skeleton with single hidden Loading text', ...);
  test('snapshot failure shows error card with retry, stream not started', () => expect(ticketCalls).toBe(0));
  test('partial: revenue failure shows "Indisponible" tile, others live', ...);
  test('offline banner, values greyed with as-of times, reconnect attempts shown', ...);
  test('empty state links to sites for operations only', ...);
  test('summary sentence uses plural-safe messages in fr and en', ...);
  ```
- [ ] **Step 2:** `npm test -- Dashboard.states`. Expected: fail.
- [ ] **Step 3: implement** `loading.tsx` and widget-level error boundaries (`WidgetBoundary` per tile so one failing tile degrades alone); the error state does not start the stream; offline detection via `useConnectivity` from fe-04 (move `connectivity.ts` to `src/shared/offline` already created there; import it); "Lire le résumé" button builds the sentence `dashboard.summarySentence` and announces it politely; empty state shows a link to `/staff/sites` only when `usePermission('createSite')`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): dashboard states and spoken summary`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes (`useSse`, reducer, leader, dashboard suites).
3. `npm run i18n:check` exits 0.
4. `npm run build && npm run size` exit 0 (`/[locale]/staff/dashboard` at most 250 KB first load).
5. `npm run e2e -- dashboard.spec.ts` passes in mock mode; with `make up` and backend slice 9, `E2E_MODE=stack npm run e2e -- dashboard.spec.ts` passes (a top-up made by the stub moves "Revenus du jour" within the freshness target).
6. `npm run e2e:a11y -- --grep dashboard` zero serious or critical violations at `/fr/staff/dashboard` and `?display=wall`, in `/en` too.
7. Manual: open three tabs; the network panel shows one `dashboard/stream` request; close the first tab and a second one takes over within 5 seconds.

## Demo script

1. Sign in as an operations engineer; open `/fr/staff/dashboard`: "Tableau de bord des opérations", "En direct", tiles and sites table. EN: "Operations dashboard", "Live".
2. Create a top-up with the provider stub: "Revenus du jour" / "Revenue today" changes without reload.
3. Stop the meter simulator for a site: its row changes to "Hors ligne" / "Offline" in place; a critical alert appears in the feed and is announced.
4. Stop the API stream (kill `be_stream`): after about 60 seconds the banner "Données possiblement périmées. Dernière mise à jour : …" / "Data may be out of date. Last update: …" appears and a polite announcement is made; restart: "Mises à jour en direct rétablies." / "Live updates restored."
5. Press "Suspendre les mises à jour" / "Pause updates": changes are counted, not applied; resume applies them.
6. Open as support: no revenue tile. Open as the administrator: no dashboard entry, URL shows the no-access page.
7. Open `?display=wall` on a large screen: large text, same content.

## Done criteria

- Requirements: FR-13 (live dashboard, stale indicator), FR-07 (alert counts and feed), NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8: TC-FR-13-2 (live update through SSE appears; reconnect handled by snapshot, see note), TC-FR-13-3 (stale indicator visible when the API serves stale cache), TC-FR-13-5 (a new single-use ticket per connection), TC-FR-13-6 (10-minute end handled by reconnect, role change stops the stream), TC-FR-13-1 (displayed totals equal the API figures, no client arithmetic), TC-NFR-10-1.

## Plan notes for the tech lead

- **Last-Event-ID versus ticket flow:** `docs/10` TC-FR-13-2 says "reconnect resumes with Last-Event-ID". With one `EventSource` per single-use ticket the client cannot send `Last-Event-ID` (browsers cannot set it manually; `EventSource` sends it only when it reconnects the same object, which would fail because the ticket is spent). Followed `docs/03` and D-18: new ticket, new connection, `snapshot` as the resync. The test strategy case should be reworded or the API should accept a `last_event_id` query parameter.
- **Alert feed content:** support and finance cannot call `listAlerts`, so the feed shows only events received since page load plus counts from the summary.
- **Leader election:** `docs/08` 5.6 proposes `BroadcastChannel`; Web Locks are added to elect the leader because the API limits users to three concurrent streams.
- **No zustand:** `docs/08` mentions it for connection status; `docs/03` forbids a global store library; `useSyncExternalStore` used.
- **Wall mode reload:** `docs/08` 3.6 wants auto-reload on a new build; no API field exposes a build id and D-49 handles tab survival across rollouts through retained chunks, so no automatic reload is built.
