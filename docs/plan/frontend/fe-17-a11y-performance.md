# Frontend slice 17: Accessibility, performance and security hardening

- **Goal:** prove WCAG 2.2 AA and the performance and security rules across every route of `hub-web`: an automated axe sweep over a route manifest in both locales, themes and states; keyboard-only flows at 200 percent zoom; layout stress, target sizes, forced colours and reduced motion; the throttled performance gate; configuration regressions (nonce CSP, Server Actions, image optimiser, secrets in the bundle); an accessibility statement and a manual screen-reader checklist for each release.
- **Depends on:** backend slice 17 (hardening; the stack for the final end-to-end runs); every earlier frontend slice (fe-00 to fe-16).
- **Requirements served:** NFR-10, NFR-02 (bundle and perceived performance), NFR-11, NFR-06 (frontend controls), FR-16.
- **Size:** L, 7 days.
- **Spikes or open items:** (1) real-device measurement of LCP and INP on a low-end Android phone is manual and recorded in `docs/plan/progress.md` **[open: device availability; default is the emulated profile below]**. (2) Real-user monitoring has no ingestion endpoint in `api/openapi.yaml`; not built in v1.

## Read first

- `docs/08-frontend-ux.md#7. Accessibility plan to WCAG 2.2 AA (NFR-10)` (all of 7.1 to 7.6)
- `docs/08-frontend-ux.md#8. Performance, offline, images and fonts` (8.1 to 8.5)
- `docs/08-frontend-ux.md#9. Frontend security and test strategy` (9.1, 9.2)
- `docs/08-frontend-ux.md#6.4 Text expansion and layout`, `#6.5 Testing translations`
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)` (Accessibility and quality paragraph, Middleware, Next.js configuration, Environment variables)
- `docs/00-shared-context.md` section 7: D-44, D-46, D-48, D-49
- `docs/10-test-strategy.md` section 8 rows NFR-10, NFR-11, NFR-06, NFR-02 (to match test names)
- OperationIds: none new; the sweep exercises every earlier operation through the mock and stack modes.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*` (only `NEXT_PUBLIC_SITE_ORIGIN`, `NEXT_PUBLIC_API_BASE`, `NEXT_PUBLIC_DEFAULT_LOCALE`); image optimiser off; nonce CSP on every HTML response.
- Access token in memory only; no test or tool in this slice may weaken that (no token in storage for convenience).
- Budgets: portal first load at most 130 KB gzipped, staff at most 250 KB, public pages at most 90 KB; no budget is raised in this slice without a change request.
- next-intl `/fr` and `/en`; French first; the pseudo-locale (`/xx`-style string transform in tests only) never ships to production.
- WCAG 2.2 AA is a release gate: zero serious or critical axe violations; AA failures block release; minor ones need a baseline entry with an expiry date.
- Generated client only; mock mode uses MSW or Playwright routes derived from `api/openapi.yaml` examples, never handwritten response types.
- Targets: at least 24x24 CSS px everywhere (SC 2.5.8), 44 px staff controls, 48 px portal controls.
- No test-only HTTP endpoint (D-38); scenarios come from `e2e/support/mock-api.ts` in mock mode and from the fixtures command in stack mode.
- Do not edit frozen files; no application features are added in this slice, only tests, scripts, the accessibility statement page, the footer link and fixes found by the tests.

## Review focus

1. A new route is added without an accessibility test: `routes.manifest.test.ts` "every page.tsx is in the manifest" (Task FE-17.1).
2. A sticky header or the bottom tab bar covers the focused element, or a modal traps focus: `e2e/keyboard.spec.ts` "focus is never obscured and dialogs return focus" (Task FE-17.2).
3. French text breaks a button or hides an amount at 360 px, 200 percent: `e2e/layout-stress.spec.ts` "pseudo-locale and 200 percent keep critical text visible" (Task FE-17.3).
4. A refactor re-enables Server Actions, the image route or inline scripts: `e2e/security-config.spec.ts` (Task FE-17.4).
5. Bundle size creeps up between slices until the portal fails on a real phone: `npm run size` regression of more than 10 percent versus the committed baseline (`frontend/size-baseline.json`) fails (Task FE-17.5).

## Files

- `frontend/e2e/routes.manifest.ts`: list of every route with locale-free path, area, params and required principal; `frontend/e2e/routes.manifest.test.ts`.
- `frontend/e2e/support/mock-api.ts` (modify: named scenarios `populated`, `empty`, `error500`, `offline`, `loading`, `partial`), `frontend/e2e/support/principals.ts` (customer, support, finance lead, operations, administrator, dp admin).
- `frontend/e2e/a11y-sweep.spec.ts`: axe over manifest by locale, theme, scenario, viewport.
- `frontend/e2e/keyboard.spec.ts`: keyboard-only flows.
- `frontend/e2e/layout-stress.spec.ts`: 320 px, 200 percent zoom, text spacing, forced colours, reduced motion, pseudo-locale.
- `frontend/e2e/target-size.spec.ts`: bounding-box assertions.
- `frontend/e2e/security-config.spec.ts`: CSP, Server Actions, image route, bundle secrets.
- `frontend/e2e/i18n-routes.spec.ts`: every route in both locales without missing keys; unsupported locale behaviour.
- `frontend/scripts/pseudo-locale.mjs`: writes a transformed message set for tests (accents, 40 percent padding, `[!! ... !!]`).
- `frontend/scripts/scan-bundle.mjs`: scans `.next` client chunks for secret patterns (`sgh_`, `-----BEGIN`, `AKIA`, `sk-`).
- `frontend/lighthouserc.json` (modify from fe-00), `frontend/size-baseline.json`, `frontend/scripts/check-size.mjs` (modify: regression check).
- `frontend/src/app/[locale]/(public)/accessibility/page.tsx` (statement, server component), `frontend/src/shared/ui/Footer.tsx` (help, accessibility statement, contact in the same place on every page).
- `frontend/src/i18n/messages/{fr,en}/public.json` (modify: statement and footer keys).
- `frontend/a11y/screen-reader-checklist.md` and `frontend/a11y/release-checklist.md`.
- `frontend/a11y/axe-baseline.json` (empty list to start; each entry has `rule`, `route`, `reason`, `expires`).

## Interfaces

**Route manifest entry:** `{ path: string; area: 'public'|'portal'|'staff'; principal?: 'customer'|'support'|'finance_lead'|'operations'|'administrator'|'dp_admin'; params?: Record<string,string>; scenarios: ('populated'|'empty'|'error500'|'offline'|'loading'|'partial')[] }`. The manifest lists at least: all public pages; portal home, topup, topup status, history, receipt, tokens, support list, new ticket, thread, profile, privacy, erasure status, export status; staff dashboard, tickets, ticket detail, draft review, co-approve, incidents, kb list and editor, ask, ask history, customers, customer detail, sites, plans, meters, meter detail, readings, alerts, alert detail, alert rules, notifications, ledger accounts, transaction, adjustment form, periods, reconciliation (batches, batch, exceptions, exception), approvals, approval, users, roles, api keys, audit, ai-spend, privacy queue, retention, security settings.

**Messages added (namespace `public`):**

| Key | fr | en |
|---|---|---|
| `public.footer.help` | Aide | Help |
| `public.footer.accessibility` | Déclaration d'accessibilité | Accessibility statement |
| `public.footer.contact` | Contacter l'assistance | Contact support |
| `public.a11y.title` | Déclaration d'accessibilité | Accessibility statement |
| `public.a11y.intro` | Soleil Grid vise la conformité aux WCAG 2.2 niveau AA pour le portail client et l'application du personnel. | Soleil Grid aims for WCAG 2.2 level AA conformance for the customer portal and the staff application. |
| `public.a11y.status` | Cet outil est en cours d'audit. Les écarts connus sont suivis et corrigés sous 30 jours. | This product is under audit. Known gaps are tracked and fixed within 30 days. |
| `public.a11y.feedback` | Un problème d'accessibilité ? Écrivez-nous ou ouvrez une demande depuis votre espace. | Found an accessibility problem? Write to us or open a request from your area. |

**Scripts (package.json):** `e2e:a11y` runs `a11y-sweep.spec.ts`; `e2e:keyboard`; `e2e:stress`; `e2e:security`; `check:bundle` runs `scan-bundle.mjs`.

**Operations consumed:** none directly.

## Tasks

### Task FE-17.1: Route manifest and axe sweep (TC-NFR-10-1, TC-FR-16-1)

- [ ] **Step 1: failing test** `frontend/e2e/routes.manifest.test.ts` (Vitest) and `a11y-sweep.spec.ts`:
  ```ts
  test('every page.tsx is in the manifest', () => {
    const pages = globSync('src/app/[[]locale[]]/**/page.tsx').map(toRoute);
    const manifest = MANIFEST.map(m=>m.path);
    expect(pages.filter(p=>!manifest.includes(p))).toEqual([]);
  });
  for (const r of MANIFEST) for (const locale of ['fr','en']) for (const theme of ['light','dark'])
    test(`${r.path} ${locale} ${theme} axe`, async ({page}) => {
      await mockApi(page, r.principal, 'populated'); await page.emulateMedia({colorScheme: theme as any});
      await page.goto(`/${locale}${fill(r)}`); await page.waitForLoadState('networkidle');
      const res = await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
      expect(serious(res).filter(notBaselined)).toEqual([]);
      await expect(page.locator('html')).toHaveAttribute('lang', locale);
    });
  // plus a per-route loop over the other scenarios, light theme and fr only: empty, error500, offline, loading, partial
  ```
- [ ] **Step 2:** `cd frontend && npm test -- routes.manifest && npm run e2e:a11y`. Expected: fail (manifest missing or missing routes).
- [ ] **Step 3: implement** `routes.manifest.ts` with every route listed under Interfaces and params fixtures; extend `mock-api.ts` with the six scenarios (`loading` delays responses by 2 s; `offline` uses `context.setOffline`; `partial` fails the secondary request of each page, for example meters on a customer page); implement `principals.ts` that seeds the in-memory session by answering `refreshToken` with the right `Principal` (roles and attributes). Fix every violation found in earlier slices' components (the fix goes into the component, never into the baseline unless a minor issue has an expiry date).
- [ ] **Step 4:** run until pass; the sweep prints a summary of routes, scenarios and violations.
- [ ] **Step 5: commit** `test(web): route manifest and axe sweep over locales, themes and states`.

### Task FE-17.2: Keyboard-only flows and focus visibility (TC-NFR-10-2)

- [ ] **Step 1:** `e2e/keyboard.spec.ts`:
  ```ts
  test('portal: top-up to status using only the keyboard at 200% zoom, 360 px', async ({page}) => {
    await useViewport(page, 360, 640, {zoom: 2}); await mockApi(page,'customer','populated'); await page.goto('/fr/portal');
    await tabTo(page, 'link', 'Recharger'); await page.keyboard.press('Enter'); /* choose chip, provider, continue, pay */ 
    await expect(page.getByRole('heading',{name:/En attente de votre téléphone/})).toBeVisible();
    await assertFocusVisibleEverywhere(page);
  });
  test('portal: write and send a support reply by keyboard', ...);
  test('staff: review and approve a draft by keyboard; focus returns to the recall bar', ...);
  test('staff: resolve a reconciliation exception proposal by keyboard', ...);
  test('staff: create an API key by keyboard and close the secret modal only after acknowledging', ...);
  test('no keyboard trap: Escape or Shift+Tab always leaves every dialog and drawer', ...);
  test('focus is never obscured by sticky header or bottom bar', async ({page}) => {
    for (const r of STICKY_ROUTES) { await goto(r); for (let i=0;i<40;i++){ await page.keyboard.press('Tab'); await expectFocusedNotCovered(page); } }
  });
  test('skip link is first and works; route change moves focus to h1; document.title updates', ...);
  ```
  `expectFocusedNotCovered` uses `elementFromPoint` at the focused element's centre and asserts it is the element or its descendant.
- [ ] **Step 2:** `npm run e2e:keyboard`. Expected: fail on any obscured focus, trap or missing focus ring found by the helpers.
- [ ] **Step 3: implement fixes** (not features): set `scroll-padding-top` and `scroll-padding-bottom` equal to the sticky header and bottom tab bar heights in `globals.css`; ensure `Dialog` and drawers return focus; make sure every route sets its `<h1>` focus and title (`generateMetadata`); ensure visible focus ring on all controls (`:focus-visible`). Add the helpers `tabTo`, `assertFocusVisibleEverywhere` (computed outline or box-shadow present and contrast against the background at least 3:1) to `e2e/support/a11y.ts`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `test(web): keyboard flows, focus visibility and obscured-focus checks`.

### Task FE-17.3: Layout stress, target sizes, forced colours, reduced motion, pseudo-locale (TC-NFR-11-2)

- [ ] **Step 1:** `layout-stress.spec.ts`, `target-size.spec.ts`:
  ```ts
  test('320 px reflow: no horizontal page scroll on portal routes', async ({page}) => { /* scrollWidth <= clientWidth + 1 */ });
  test('text spacing override keeps content visible', async ({page}) => { await page.addStyleTag({content:'*{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important} p{margin-bottom:2em!important}'}); await expectNoClipping(page); });
  test('pseudo-locale keeps amounts, states and button labels fully visible at 360 px', async ({page}) => { await usePseudoLocale(page); for (const r of PORTAL_ROUTES) { await goto(r); await expectCriticalTextNotTruncated(page); } });
  test('forced colours keep focus ring and borders', async ({page}) => { await page.emulateMedia({forcedColors:'active'}); ... });
  test('reduced motion: no shimmer, no transitions over 100 ms', async ({page}) => { await page.emulateMedia({reducedMotion:'reduce'}); ... });
  test('target size: all interactive elements at least 24x24; portal at least 48; staff controls at least 44; inline text links exempt', async ({page}) => {
    for (const r of MANIFEST) { await goto(r); const small = await smallTargets(page, r.area); expect(small).toEqual([]); }
  });
  ```
  `expectCriticalTextNotTruncated` fails when an element carrying `data-critical` (amounts, statuses, button labels) has `scrollWidth > clientWidth` or computed `text-overflow: ellipsis`.
- [ ] **Step 2:** `npm run e2e:stress`. Expected: fail where text is truncated or targets are small.
- [ ] **Step 3: implement fixes** (not features): add `data-critical` to `MoneyText`, `StatusBadge`, `Button` roots; allow button and tab labels to wrap; give wide tables a focusable scroll region; raise padding of small icon buttons to meet the minimum; ensure `forced-colors` rules use system colours and borders; guard animations under `prefers-reduced-motion`. `scripts/pseudo-locale.mjs` produces the transformed message set that the test mounts through `NEXT_INTL_PSEUDO=1` handled in `src/i18n/request.ts` only when `process.env.NODE_ENV === 'test'`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `test(web): layout stress, target size and pseudo-locale checks`.

### Task FE-17.4: Security and configuration regression (TC-NFR-06-11, TC-NFR-06-12, TC-NFR-06-13)

- [ ] **Step 1:** `e2e/security-config.spec.ts`, `scripts/scan-bundle.test.ts`:
  ```ts
  test('every HTML response has a CSP with a fresh nonce; inline script without nonce is blocked', async ({page, request}) => {
    const a = await request.get('/fr'); const b = await request.get('/fr');
    const na = nonceOf(a.headers()['content-security-policy']), nb = nonceOf(b.headers()['content-security-policy']);
    expect(na).not.toEqual(nb); expect(a.headers()['content-security-policy']).not.toMatch(/unsafe-inline|unsafe-eval/);
    const violations = []; page.on('console', m => /Content Security Policy/.test(m.text()) && violations.push(m.text()));
    await page.goto('/fr'); await page.evaluate(()=>{const s=document.createElement('script'); s.textContent='window.__x=1'; document.body.appendChild(s);});
    expect(await page.evaluate(()=> (window as any).__x)).toBeUndefined();
  });
  test('Server Actions refused', async ({request}) => { const r = await request.post('/fr',{headers:{'Next-Action':'abc'}}); expect(r.status()).toBe(404); });
  test('image optimiser route refused', async ({request}) => { const r = await request.get('/_next/image?url=%2Ficons%2Ficon-192.png&w=64&q=75'); expect([400,404]).toContain(r.status()); });
  test('no secret in client bundle or NEXT_PUBLIC variables', () => { expect(scanBundle('.next/static')).toEqual([]); expect(publicEnvNames()).toEqual(['NEXT_PUBLIC_API_BASE','NEXT_PUBLIC_DEFAULT_LOCALE','NEXT_PUBLIC_SITE_ORIGIN']); });
  test('access token never in storage or cookies visible to scripts', async ({page}) => { await signIn(page); const store = await page.evaluate(()=>JSON.stringify({l:{...localStorage},s:{...sessionStorage},c:document.cookie})); expect(store).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}/); });
  test('service worker never handles API or authenticated HTML', ...);   // reuses the fe-04 simulation against the built sw.js
  ```
- [ ] **Step 2:** `npm run build && npm run e2e:security && npm run check:bundle`. Expected: fail if any regression exists.
- [ ] **Step 3: implement fixes only** where a check fails (headers in `middleware.ts`, `next.config.ts`, removal of any stray inline script); `scripts/scan-bundle.mjs` implemented as listed under Files.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `test(web): CSP nonce, Server Action, image optimiser and bundle secret regression checks`.

### Task FE-17.5: Performance gate and baseline (TC-NFR-02-4)

- [ ] **Step 1:** `scripts/check-size.test.ts` (extend) and `e2e/perf-smoke.spec.ts`:
  ```ts
  test('size regression over 10 percent versus baseline fails', () => { expect(checkSize(table({portal:121}), {portal:108}).ok).toBe(false); expect(checkSize(table({portal:118}), {portal:108}).ok).toBe(true); });
  test('portal first load at most 130 KB, staff at most 250 KB, public at most 90 KB', ...);
  test('Lighthouse throttled profile thresholds', ...);
  ```
  `lighthouserc.json` assertions (as in fe-00): LCP at most 2,500 ms, total blocking time at most 300 ms, CLS at most 0.05 (portal) and 0.1 (staff), total transfer of `/fr/portal` at most 250 KB.
- [ ] **Step 2:** `npm run build && npm run size && npx lhci autorun`. Expected: either pass (store baseline) or fail (record).
- [ ] **Step 3: implement.** Commit `size-baseline.json` from the current build; extend `check-size.mjs` with the 10 percent regression rule against the baseline per route group; Lighthouse CI runs on `/fr`, `/fr/portal`, `/fr/portal/topup`, `/fr/staff/dashboard` in mock mode with the throttled profile (1.6 Mbps down, 750 kbps up, 150 ms RTT, 4x CPU slowdown, emulated mobile viewport 360x640). Record in `docs/plan/progress.md`: numbers per route, the Interaction to Next Paint proxy (total blocking time) and, if a physical low-end device is available, the manual LCP and INP of the top-up flow. If any budget is exceeded, follow the change request procedure of `docs/plan/00-executor-guide.md`; do not raise budgets here. Fix regressions by lazy-loading (`next/dynamic`), removing dependencies or splitting shared chunks.
- [ ] **Step 4:** run until pass or until the change request is filed with measurements.
- [ ] **Step 5: commit** `test(web): size baseline, regression gate and throttled Lighthouse profile`.

### Task FE-17.6: Locale routes, accessibility statement, footer and manual checklists (TC-FR-16-3, TC-NFR-10-3, TC-NFR-11-3)

- [ ] **Step 1:** `e2e/i18n-routes.spec.ts`, `Footer.test.tsx`:
  ```ts
  test('every manifest route renders in fr and en with no missing-key marker', async ({page}) => { for (const r of MANIFEST) for (const l of ['fr','en']) { await goto(l,r); expect(await page.content()).not.toMatch(/\[missing|\{\{|__MISSING__|common\.[a-z]+\.[a-z]+/i); } });
  test('unknown locale: default French route with notice, user choice persists in cookie hub_locale', async ({page}) => { await page.goto('/de'); await expect(page).toHaveURL(/\/fr$/); await switchTo(page,'en'); await page.reload(); await expect(page).toHaveURL(/\/en/); });
  test('dates and numbers locale-aware on dashboard and receipts', ...);   // TC-NFR-11-3
  test('footer has help, accessibility statement and contact in the same position on public, portal and staff pages (SC 3.2.6)', ...);
  test('accessibility statement page is server rendered, axe clean in both locales', ...);
  ```
- [ ] **Step 2:** `npm test -- Footer && npm run e2e -- i18n-routes.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** the footer component in the three layouts (same order and place), the `accessibility` public page, keys from Interfaces, the `middleware` behaviour already built in fe-00 verified here; create `frontend/a11y/screen-reader-checklist.md` with a 20-minute script per release candidate: NVDA with Firefox on the staff flows (ticket queue table navigation, draft review, approvals, dashboard alert announcement), VoiceOver with Safari on macOS and iOS, TalkBack with Chrome on a real low-end Android for the portal (top-up status announcement, offline banner, history list); expected observations written as checkboxes (headings and landmarks list sensible; table headers read with cells; live region heard once; dialogs trap and return focus; AI label read; error summary read). Create `frontend/a11y/release-checklist.md` listing the gates: axe sweep green, keyboard suite green, layout stress green, size gate green, Lighthouse recorded, screen-reader pass signed with date and name, statement reviewed.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): accessibility statement, consistent footer and release checklists`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes (including the manifest test and the size regression tests).
3. `npm run i18n:check` exits 0; `npm run e2e -- i18n-routes.spec.ts` passes.
4. `npm run build && npm run size && npm run check:bundle` exit 0.
5. `npm run e2e:a11y` passes: zero serious or critical violations across the manifest, both locales, both themes and the six scenarios (the baseline file empty or only dated minor entries).
6. `npm run e2e:keyboard && npm run e2e:stress && npm run e2e:security` pass.
7. `npx lhci autorun` passes the thresholds and the numbers are recorded in `docs/plan/progress.md`.
8. Stack run (`make up`, `E2E_MODE=stack`) of `a11y-sweep.spec.ts` for the critical paths (top-up, ticket reply, draft approval, reconciliation proposal) passes with real data.
9. The signed screen-reader checklist (NVDA, VoiceOver, TalkBack) is attached to `docs/plan/progress.md` with date and name.

## Demo script

1. Run `npm run e2e:a11y` and show the summary: every route and scenario, zero serious violations, both languages.
2. Open `/fr/portal` at 360 px with browser zoom 200 percent and keyboard only: "Aller au contenu principal", then reach "Recharger" / "Top up" and complete a top-up to the status page; the focus ring is visible and never hidden by the bottom bar.
3. Turn on a screen reader (NVDA or TalkBack): the status page announces "Paiement confirmé. 10,00 $US ajouté." / "Payment confirmed. $10.00 added." once.
4. Switch the OS to forced colours and reduced motion: borders and focus remain visible, no shimmer.
5. Open `/fr/accessibility` and `/en/accessibility`: "Déclaration d'accessibilité" / "Accessibility statement", the footer link in the same place on public, portal and staff pages.
6. Show `curl -sI http://localhost/fr` twice: different `nonce-` values; `curl -X POST -H 'Next-Action: x' http://localhost/fr -i` returns 404; the image route returns 400 or 404.
7. Show the Lighthouse report: LCP at most 2.5 s on the throttled profile for `/fr/portal/topup`, and the size table within budgets.

## Done criteria

- Requirements: NFR-10 (WCAG 2.2 AA gate), NFR-11 (locale-aware formatting everywhere, no hard-coded strings), NFR-02 (bundle budgets enforced), NFR-06 (frontend controls), FR-16.
- Test cases from `docs/10-test-strategy.md` section 8: TC-NFR-10-1 (axe on every route in EN and FR), TC-NFR-10-2 (keyboard-only top-up and ticket reply at 200 percent zoom), TC-NFR-10-3 (manual screen-reader pass, checklist signed), TC-NFR-02-4 (first-load budgets fail the build), TC-NFR-11-2 (pseudo-locale and missing-key checks find no hard-coded string), TC-NFR-11-3 (locale-aware dates and numbers), TC-FR-16-1 (language switch renders every route without a missing key), TC-FR-16-3 (unsupported locale handled and the choice persists), TC-NFR-06-11 (CSP nonce on every HTML response), TC-NFR-06-12 (Server Actions and image optimiser disabled), TC-NFR-06-13 (no secret in `NEXT_PUBLIC_*` or in the bundle).

## Plan notes for the tech lead

- **Unsupported locale:** `TC-FR-16-3` says an unsupported locale falls back to English; D-44 and `docs/03` make French the default with `localePrefix: 'always'`. The test in Task FE-17.6 asserts the French default and persistence of the user's choice; the test case wording should be aligned (French default, English only as a missing-key fallback for staff).
- **Real-user monitoring:** `docs/08` 8.1 calls for `web-vitals` reporting; there is no ingestion operation in `api/openapi.yaml` and the `NEXT_PUBLIC_` allow-list has three names only. Not built; revisit with an observability endpoint.
- **WCAG 2.4.13 (AAA):** `docs/08` adopts the 2 px focus ring as a target; tested through the contrast and visibility helper, not as a conformance claim.
- **Mock mode versus stack mode:** the sweep runs in mock mode per pull request because later backend slices may not exist yet; the stack run of critical paths is the final gate once backend slice 17 is done.
- **Statement wording:** the accessibility statement text is a placeholder for the legal and communications owners; the keys make it replaceable without code.
